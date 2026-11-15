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
    if code == 'coupon_type':
        value.pop('inferred', None)
    if code == 'coupon_rate':
        value.pop('current_rate_percent', None)
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


def compare_iss_details(code, value, iss_value):
    """Compare supported parameters, not the absence of the rest of a graph."""
    projected = project_iss(code, iss_value)
    if projected is None or value is None:
        return 'extracted', False
    if code == 'coupon_type' and iss_value.get('inferred'):
        return 'extracted', False
    comparisons = []

    def add(left, right):
        if left is not None and right is not None and not has_unknown(left) and not has_unknown(right):
            comparisons.append(canonical(left) == canonical(right))

    if code == 'coupon_type':
        add(value.get('type'), projected.get('type'))
        partial = False
    elif code == 'coupon_rate':
        # A formula is not verified by a point-in-time ISS rate.
        if value.get('formula'):
            return 'extracted', False
        def expand(periods):
            result = {}
            for period in periods:
                if period['to'] - period['from'] > 1000:
                    return None, False
                for index in range(period['from'], period['to'] + 1):
                    parameters = {key: period.get(key) for key in ('rate_percent', 'amount')}
                    if index in result and canonical(result[index]) != canonical(parameters):
                        return None, True
                    result[index] = parameters
            return result, False
        left, conflicting = expand(value.get('periods', []))
        right, _ = expand(projected.get('periods', []))
        if conflicting:
            return 'needs_review', False
        if left is None or right is None:
            return 'extracted', False
        for index in left.keys() & right.keys():
            for key in ('rate_percent', 'amount'):
                add(left[index].get(key), right[index].get(key))
        partial = canonical(value) != canonical(projected)
    elif code == 'coupon_schedule':
        payments = projected['payments']
        period_days = projected.get('period_days')
        durations = {(datetime.fromisoformat(row['end']) - datetime.fromisoformat(row['start'])).days
                     for row in payments if row.get('start') and row.get('end')}
        if period_days is None and len(durations) == 1:
            period_days = next(iter(durations))
        add(value.get('period_days'), period_days)
        add(value.get('frequency_per_year'), projected.get('frequency_per_year'))
        add(value.get('total_payments'), projected.get('total_payments') or (len(payments) if payments else None))
        by_number = {row['number']: row for row in payments}
        for row in value['payments']:
            other = by_number.get(row['number'], {})
            for key in ('start', 'end', 'payment_date'):
                add(row.get(key), other.get(key))
        partial = canonical(value) != canonical(projected)
    elif code in ('offers', 'amortization'):
        for row in value['events']:
            candidates = [other for other in projected['events'] if other['date'] == row['date']]
            if not candidates and len(value['events']) == len(projected['events']) == 1:
                candidates = projected['events']
            if code == 'offers' and row.get('type') not in (None, 'unknown'):
                same_type = [other for other in candidates if other.get('type') == row['type']]
                if same_type:
                    candidates = same_type
            if len(candidates) == 1:
                other = candidates[0]
                add(row['date'], other['date'])
                for key in row.keys() - {'date'}:
                    if code == 'amortization' and key == 'percent' and (has_unknown(row.get('percent_base')) or has_unknown(other.get('percent_base'))):
                        continue
                    add(row[key], other.get(key))
        partial = canonical(value) != canonical(projected) or has_unknown(value) or has_unknown(projected)
    else:
        for key, parameter in value.items():
            add(parameter, projected.get(key))
        partial = False
    status = 'mismatch_iss' if False in comparisons else 'verified_iss' if comparisons else 'extracted'
    return status, partial


def compare_iss(code, value, iss_value):
    return compare_iss_details(code, value, iss_value)[0]


def result_template(code, iss_value=None):
    return {'code': code, 'value': None, 'quote': None, 'document_id': None, 'page': None, 'clause': None,
            'status': 'not_found', 'iss_value': iss_value, 'confidence': None, 'source': {}, 'not_found': True,
            'evidence': [], 'reason': 'not_in_documents'}


def degraded_field(code, iss_value, iss_url, reason='llm_not_configured'):
    result = result_template(code, iss_value)
    if FIELDS[code].iss and (value := project_iss(code, iss_value)) is not None:
        result.update(value=value, status='extracted', not_found=False, reason='iss_only', source={'kind': 'moex_iss', 'name': 'MOEX ISS', 'url': iss_url})
    else:
        result['reason'] = 'iss_unavailable' if FIELDS[code].iss else reason
        if not FIELDS[code].iss and reason != 'llm_not_configured':
            result.update(status='needs_review', not_found=False)
    return result


def _verify_document(code, extracted, pages, context, settings, iss_value=None):
    result = result_template(code, iss_value)
    data = extracted.model_dump(mode='json', by_alias=True)
    result.update(confidence=data['confidence'], not_found=data['not_found'])
    if data['not_found']:
        if data['ambiguous']:
            result.update(status='needs_review', reason='conflicting_sources')
        return result
    if not FIELDS[code].iss and not any(data['value'].values()):
        result.update(status='needs_review', reason='empty_extraction', not_found=False)
        return result
    primary = {k: data[k] for k in ('quote', 'document_id', 'page', 'clause')}
    evidence = [primary, *data['evidence']]
    for item in evidence:
        page_text = pages.get((str(item['document_id']), item['page']))
        fragments = []
        for fragment in context:
            if str(fragment['document_id']) != str(item['document_id']) or fragment['page'] != item['page']:
                continue
            if item['clause'] is None or fragment['clause'] == item['clause']:
                fragments.append(fragment)
            elif fragment.get('full_page'):
                from app.documents.parser import chunks
                sections, _ = chunks(fragment['text'], fragment.get('previous_clause'), size=len(fragment['text']) + 1, overlap=0)
                fragments.extend(section for section in sections if section['clause'] == item['clause'])
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
    elif has_unknown(value) and not FIELDS[code].iss:
        result.update(status='needs_review', reason='ambiguous_value')
    elif FIELDS[code].iss and iss_value is not None:
        result['status'] = compare_iss(code, value, iss_value)
        if result['status'] == 'needs_review':
            result['reason'] = 'conflicting_sources'
    return result


def verify(code, extracted, pages, context, settings, iss_value=None, iss_url=''):
    result = _verify_document(code, extracted, pages, context, settings, iss_value)
    projected = project_iss(code, iss_value) if FIELDS[code].iss else None
    if projected is None:
        return result
    if extracted.ambiguous or result.get('reason') == 'conflicting_sources':
        if result['value'] is None:
            result = degraded_field(code, iss_value, iss_url)
        result.update(status='needs_review', reason='conflicting_sources')
        return result
    if result['status'] not in ('verified_iss', 'mismatch_iss'):
        fallback = degraded_field(code, iss_value, iss_url)
        fallback['reason'] = 'iss_not_confirmed'
        return fallback
    if result['status'] == 'verified_iss' and compare_iss_details(code, result['value'], iss_value)[1]:
        result['reason'] = 'iss_partial_confirmation'
    return result


def anchors_supported(value, quotes):
    """Conservative numeric/date support; not a substitute for legal review."""
    quotes = normalized_text(quotes)
    compact = re.sub(r'(?<=\d)\s+(?=\d)', '', quotes).replace(',', '.')
    if isinstance(value, dict):
        # Coupon ordinal numbers are structure; only parameters need anchors.
        return all(anchors_supported(v, quotes) for k, v in value.items() if k not in ('number', 'from', 'to'))
    if isinstance(value, list):
        return all(anchors_supported(v, quotes) for v in value)
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(re.search(r'(?<![\w.])' + str(value) + r'(?![\w.])', compact))
    if not isinstance(value, str):
        return True
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        year, month, day = value.split('-')
        if value in quotes or f'{day}.{month}.{year}' in quotes:
            return True
        stems = ['январ', 'феврал', 'март', 'апрел', 'ма', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр']
        return bool(re.search(r'\b0?' + str(int(day)) + r'\s+' + stems[int(month)-1] + r'[а-я]*\s+' + year + r'\b', quotes))
    for date_text in re.findall(r'\b\d{2}\.\d{2}\.\d{4}\b', value):
        if date_text not in quotes:
            return False
        value = value.replace(date_text, '')
    numbers = re.findall(r'\d+(?:[.,]\d+)?', value)
    quoted_numbers = {Decimal(n) for n in re.findall(r'(?<![\w.])[+-]?\d+(?:\.\d+)?', compact)}
    return all(Decimal(n.replace(',', '.')) in quoted_numbers for n in numbers)
