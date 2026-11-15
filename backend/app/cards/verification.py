from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
import json
import re
import unicodedata

from app.cards.schema import FIELDS, validate_value


def normalized_text(value):
    value = unicodedata.normalize('NFKC', value).lower().replace('ё', 'е')
    value = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', value)
    value = value.translate(str.maketrans({'«': '"', '»': '"', '“': '"', '”': '"', '‘': "'", '’': "'", '–': '-', '—': '-'}))
    return re.sub(r'\s+', ' ', value).strip()


def critical_tokens(text):
    # Fuzzy matching must not silently change dates, values, negations/exceptions.
    return re.findall(r'\d+(?:[.,]\d+)*|\b(?:не|нет|без|кроме|исключая|исключением)\b', text)


def quote_matches(quote, page, threshold):
    quote, page = normalized_text(quote), normalized_text(page)
    if not quote:
        return False
    if quote in page:
        return True
    if len(quote) < 40:
        return False
    # Locate a local candidate, not a similarity score for the entire page.
    matcher = SequenceMatcher(None, quote, page, autojunk=False)
    anchors = matcher.get_matching_blocks()
    for anchor in sorted(anchors, key=lambda b: b.size, reverse=True)[:5]:
        start = max(0, anchor.b - anchor.a)
        for delta in (-3, 0, 3):
            candidate = page[max(0, start + delta):max(0, start + delta) + len(quote)]
            if critical_tokens(quote) == critical_tokens(candidate) and SequenceMatcher(None, quote, candidate, autojunk=False).ratio() >= threshold:
                return True
    return False


def project_iss(code, value):
    """Keep the typed contract; retain the unmodified ISS JSON in iss_value."""
    if value is None:
        return None
    value = json.loads(json.dumps(value))
    if code == 'offers':
        value['events'] = [{k: item.get(k) for k in ('date', 'type', 'price_percent')} for item in value.get('events', [])]
    try:
        return validate_value(code, value)
    except ValueError:
        return None


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(value, str):
        if re.fullmatch(r'-?\d+(?:[.,]\d+)?', value):
            return str(Decimal(value.replace(',', '.')).normalize())
        if value in ('SUR', 'RUR'):
            return 'RUB'
        return normalized_text(value)
    return value


def has_unknown(value):
    if isinstance(value, dict):
        return any(has_unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(has_unknown(v) for v in value)
    return value == 'unknown'


def comparable(code, value):
    if value is None or has_unknown(value):
        return False
    if code == 'coupon_rate':
        return not value.get('formula') and bool(value.get('periods')) and all(p.get('rate_percent') is not None or p.get('amount') is not None for p in value['periods'])
    if code == 'coupon_schedule':
        return bool(value.get('payments')) and all(p.get('payment_date') for p in value['payments'])
    if code in ('offers', 'amortization'):
        return bool(value.get('events'))
    return True


def compare_iss(code, value, iss_value):
    projected = project_iss(code, iss_value)
    if not comparable(code, projected) or not comparable(code, value):
        return 'needs_review' if projected is not None else 'extracted'
    # Only compare metadata ISS can support. A formula cannot be verified by one rate.
    if code == 'coupon_type':
        left, right = value.get('type'), projected.get('type')
    elif code == 'coupon_rate':
        def expand(v):
            output = {}
            for period in v['periods']:
                if period['to'] - period['from'] > 1000:
                    return None
                for i in range(period['from'], period['to'] + 1):
                    if i in output:
                        return None
                    output[i] = period
            return output
        left, right = expand(value), expand(projected)
        if left is None or right is None or left.keys() != right.keys():
            return 'needs_review'
        comparisons = []
        for i in left:
            common = [k for k in ('rate_percent', 'amount') if left[i].get(k) is not None and right[i].get(k) is not None]
            if not common:
                return 'needs_review'
            comparisons.extend(canonical(left[i][k]) == canonical(right[i][k]) for k in common)
        return 'verified_iss' if all(comparisons) else 'mismatch_iss'
    elif code in ('coupon_schedule', 'offers', 'amortization'):
        array = 'payments' if code == 'coupon_schedule' else 'events'
        left_rows, right_rows = value[array], projected[array]
        if len(left_rows) != len(right_rows):
            return 'needs_review'
        date_key = 'payment_date' if code == 'coupon_schedule' else 'date'
        left_rows = sorted(left_rows, key=lambda v: v[date_key])
        right_rows = sorted(right_rows, key=lambda v: v[date_key])
        comparisons = []
        for a, b in zip(left_rows, right_rows):
            keys = [k for k in a if a[k] is not None and b.get(k) is not None]
            comparisons.extend(canonical(a[k]) == canonical(b[k]) for k in keys)
        if code == 'coupon_schedule':
            for key in ('frequency_per_year', 'period_days'):
                if value.get(key) is not None and projected.get(key) is not None:
                    comparisons.append(value[key] == projected[key])
        return 'verified_iss' if all(comparisons) else 'mismatch_iss'
    else:
        left, right = value, projected
    return 'verified_iss' if canonical(left) == canonical(right) else 'mismatch_iss'


def result_template(code, iss_value=None):
    return {'code': code, 'value': None, 'quote': None, 'document_id': None, 'page': None, 'clause': None,
            'status': 'not_found', 'iss_value': iss_value, 'confidence': None, 'source': {}, 'not_found': True,
            'evidence': [], 'reason': 'not_in_documents'}


def degraded_field(code, iss_value, iss_url):
    result = result_template(code, iss_value)
    if FIELDS[code].iss and (value := project_iss(code, iss_value)) is not None:
        result.update(value=value, status='extracted', not_found=False, reason='iss_only', source={'kind': 'moex_iss', 'name': 'MOEX ISS', 'url': iss_url})
    else:
        result['reason'] = 'iss_unavailable' if FIELDS[code].iss else 'llm_not_configured'
    return result


def verify(code, extracted, pages, context, settings, iss_value=None):
    result = result_template(code, iss_value)
    data = extracted.model_dump(mode='json', by_alias=True)
    result.update(confidence=data['confidence'], not_found=data['not_found'])
    if data['not_found']:
        if data['ambiguous']:
            result.update(status='needs_review', reason='conflicting_sources')
        return result
    primary = {k: data[k] for k in ('quote', 'document_id', 'page', 'clause')}
    evidence = [primary, *data['evidence']]
    for item in evidence:
        page_text = pages.get((str(item['document_id']), item['page']))
        fragments = [f for f in context if str(f['document_id']) == str(item['document_id']) and f['page'] == item['page'] and (item['clause'] is None or f['clause'] == item['clause'])]
        if not item['quote'] or not page_text or not fragments or not quote_matches(item['quote'], page_text, settings.quote_fuzzy_threshold) or not any(quote_matches(item['quote'], f['text'], settings.quote_fuzzy_threshold) for f in fragments):
            result.update(status='needs_review', reason='quote_not_verified')
            return result
    value = validate_value(code, data['value'])
    if not anchors_supported(value, ' '.join(item['quote'] for item in evidence)):
        result.update(status='needs_review', reason='value_not_supported_by_quote')
        return result
    for key, items in value.items():
        if isinstance(items, list) and len(items) > 1:
            supported = {e.get('value_path') for e in data['evidence']}
            if any('/' + key + '/' + str(i) not in supported for i in range(1, len(items))):
                result.update(status='needs_review', reason='incomplete_evidence')
                return result
            for index, item in enumerate(items):
                quotes = [data['quote']] if index == 0 else [e['quote'] for e in data['evidence'] if e.get('value_path') == f'/{key}/{index}']
                if not anchors_supported(item, ' '.join(quotes)):
                    result.update(status='needs_review', reason='value_not_supported_by_quote')
                    return result
    result.update(value=value, quote=data['quote'], document_id=data['document_id'], page=data['page'], clause=data['clause'], evidence=data['evidence'],
                  source={'kind': 'document'}, status='extracted', reason=None)
    if data['ambiguous']:
        result.update(status='needs_review', reason='conflicting_sources')
    elif data['confidence'] is None or data['confidence'] < settings.extraction_min_confidence:
        result.update(status='needs_review', reason='low_confidence')
    elif has_unknown(value):
        result.update(status='needs_review', reason='ambiguous_value')
    elif FIELDS[code].iss and iss_value is not None:
        result['status'] = compare_iss(code, value, iss_value)
        if result['status'] == 'needs_review':
            result['reason'] = 'iss_comparison_incomplete'
    return result


def anchors_supported(value, quotes):
    """Conservative numeric/date support; not a substitute for legal review."""
    quotes = normalized_text(quotes)
    compact = re.sub(r'(?<=\d)\s+(?=\d)', '', quotes).replace(',', '.')
    if isinstance(value, dict):
        # Coupon ordinal numbers are structure; only parameters need anchors.
        return all(anchors_supported(v, quotes) for k, v in value.items() if k not in ('number', 'from', 'to', 'frequency_per_year', 'period_days'))
    if isinstance(value, list):
        return all(anchors_supported(v, quotes) for v in value)
    if not isinstance(value, str):
        return True
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        year, month, day = value.split('-')
        if value in quotes or f'{day}.{month}.{year}' in quotes:
            return True
        stems = ['январ', 'феврал', 'март', 'апрел', 'ма', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр']
        return bool(re.search(r'\b0?' + str(int(day)) + r'\s+' + stems[int(month)-1] + r'[а-я]*\s+' + year + r'\b', quotes))
    numbers = re.findall(r'\d+(?:[.,]\d+)?', value)
    quoted_numbers = {Decimal(n) for n in re.findall(r'(?<![\w.])[+-]?\d+(?:\.\d+)?', compact)}
    return all(Decimal(n.replace(',', '.')) in quoted_numbers for n in numbers)
