"""Frozen regex-v1: section/keyword extraction, no gateway, ISS only for comparison."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import uuid

from app.cards.schema import SPECS, extraction_model
from app.cards.verification import degraded_field, normalized_text, result_template, verify
from app.config import Settings
from app.documents.parser import chunks
from eval.corpus import load_corpus
from eval.schema import RISK_ARRAYS

VERSION = 'regex-v1'
DATE = r'\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}'
NUMBER = r'\d+(?:[.,]\d+)?'
PATTERNS = {
    'face_value': r'номинальн.{0,50}стоимост', 'placement_date': r'дат[аы] (?:начала )?размещени',
    'maturity_date': r'дат[аы] (?:окончательного )?погашени', 'coupon_type': r'тип купона|купон.{0,30}(?:фиксирован|плавающ|переменн)',
    'coupon_rate': r'ставк[аи].{0,30}купона|купонн.{0,20}ставк', 'coupon_schedule': r'купонные выплаты:|график (?:выплат|купонов)',
    'offers': r'оферта\s+(?:put|call)', 'amortization': r'амортизация|частичное погашение номинал',
    'covenants': r'ковенант|долг.{0,40}ebitda',
    'holder_early_redemption': r'по требованию владельц|владельц.{0,40}вправе требовать досрочн',
    'issuer_call_option': r'эмитент вправе досрочно погасить|по усмотрению эмитента|call-опцион',
    'guarantee_security': r'поручительств|обеспечен.{0,50}залог|оферта третьего лица',
    'cross_default': r'кросс[- ]?дефолт|неисполнени.{0,40}иных долговых обязательств',
    'issuer_restrictions': r'эмитенту запрещ|эмитент не вправе|ограничени.{0,30}дивиденд',
    'offer_procedure': r'через депозитарий|порядок (?:акцепта|предъявления)|окно подачи',
}


def day(text):
    match = re.search(DATE, text)
    if not match:
        return None
    try:
        return datetime.strptime(match[0], '%d.%m.%Y' if '.' in match[0] else '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def item_for(code, text):
    clean = re.sub(r'^\s*\d+(?:\.\d+)+\.?\s*', '', text).strip()
    lower = normalized_text(clean)
    if re.search(r'(?:не предусмотрен|отсутствуют|не предоставля)', lower):
        return None
    if code == 'face_value':
        match = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(рубл\w*|rub|доллар\w*|usd|евро|eur)', lower)
        if match:
            currency = 'RUB' if match[2].startswith(('рубл', 'rub')) else 'USD' if match[2].startswith(('доллар', 'usd')) else 'EUR'
            return {'amount': match[1].strip(), 'currency': currency}
    elif code in ('placement_date', 'maturity_date'):
        if value := day(clean):
            return {'date': value}
    elif code == 'coupon_type':
        for word, value in [('фиксирован', 'fixed'), ('плавающ', 'floating'), ('переменн', 'variable')]:
            if word in lower:
                return {'type': value, 'description': None}
    elif code == 'coupon_rate':
        rate = re.search(r'(' + NUMBER + r')\s*(?:%|процент)', lower)
        period = re.search(r'с\s+(\d+)\s+по\s+(\d+)\s+купон', lower)
        if rate:
            return {'from': int(period[1]) if period else 1, 'to': int(period[2]) if period else 1, 'rate_percent': rate[1], 'amount': None}
    elif code == 'coupon_schedule':
        dates = [day(match[0]) for match in re.finditer(DATE, lower)]
        if dates and all(dates):
            return [{'number': i, 'start': None, 'end': None, 'payment_date': date} for i, date in enumerate(dates, 1)]
    elif code == 'offers':
        date, kind = day(clean), re.search(r'оферта\s+(put|call)', lower)
        price = re.search(r'цен[ае]\s+(' + NUMBER + r')\s*(?:%|процент)', lower)
        if date and kind:
            return {'date': date, 'type': kind[1], 'price_percent': price[1] if price else None}
    elif code == 'amortization':
        date = day(clean)
        percent = re.search(r'(' + NUMBER + r')\s*(?:%|процент)', lower)
        if date and percent:
            return {'date': date, 'amount': None, 'percent': percent[1], 'percent_base': 'initial_face_value' if 'первоначальн' in lower else 'unknown'}
    elif code == 'covenants':
        return {'condition': clean, 'test_date_rule': None, 'consequence': None, 'exceptions': None}
    elif code == 'holder_early_redemption':
        return {'trigger': clean, 'deadline': None, 'exceptions': None}
    elif code == 'issuer_call_option':
        return {'trigger': clean, 'date_rule': None, 'price_rule': None, 'notice_rule': None}
    elif code == 'guarantee_security':
        kind = 'surety' if 'поручительств' in lower else 'pledge' if 'залог' in lower else 'third_party_offer'
        return {'type': kind, 'party': None, 'scope': clean, 'expiry': None, 'exceptions': None}
    elif code == 'cross_default':
        return {'trigger': clean, 'threshold': None, 'entities': None, 'grace_period': None, 'consequence': None, 'exceptions': None}
    elif code == 'issuer_restrictions':
        return {'action': clean, 'restriction': clean, 'exceptions': None, 'consequence': None}
    elif code == 'offer_procedure':
        return {'offer_date': None, 'window_start': None, 'window_end': None, 'window_rule': None, 'via': None, 'submission': clean, 'settlement_rule': None}
    return None


def build_card(loaded, iss_only=False):
    start = time.monotonic()
    gold = loaded.gold
    documents, context, pages = [], [], {}
    for document in gold.documents:
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, 'baseline:' + document.sha256))
        documents.append({'id': identifier, 'sha256': document.sha256, 'filename': document.filename, 'enabled': document.enabled,
                          'status': 'ready', 'origins': document.origins})
        previous = None
        for (original_id, number), text in sorted(loaded.pages.items()):
            if original_id != document.document_id or not document.enabled:
                continue
            pages[(identifier, number)] = text
            fragments, previous = chunks(text, previous)
            context.extend({'document_id': identifier, 'page': number, **f} for f in fragments)
    fields = {}
    for spec in SPECS:
        code = spec.code
        iss_value = loaded.snapshot['fields'].get(code) if spec.iss else None
        if iss_only:
            fields[code] = degraded_field(code, iss_value, loaded.snapshot.get('source_url', 'https://iss.moex.com/iss'))
            continue
        found, records = [], []
        for fragment in context:
            if not re.search(PATTERNS[code], normalized_text(fragment['text'])):
                continue
            candidate = item_for(code, fragment['text'])
            if candidate is None:
                continue
            candidates = candidate if code == 'coupon_schedule' else [candidate]
            for candidate in candidates:
                if candidate not in found:
                    found.append(candidate)
                    records.append(fragment)
        if not found:
            fields[code] = result_template(code, iss_value)
            continue
        array = RISK_ARRAYS.get(code, {'coupon_rate': 'periods', 'coupon_schedule': 'payments', 'offers': 'events', 'amortization': 'events'}.get(code))
        value = {array: found} if array else found[0]
        if code == 'coupon_rate':
            value['formula'] = None
        elif code == 'coupon_schedule':
            value.update(frequency_per_year=None, period_days=None)
        evidence = [{**{k: record[k] for k in ('document_id', 'page', 'clause')}, 'quote': record['text'], 'value_path': f'/{array}/{i}'}
                    for i, record in enumerate(records[1:], 1)] if array else []
        primary = records[0]
        data = {'value': value, 'quote': primary['text'], 'document_id': primary['document_id'], 'page': primary['page'],
                'clause': primary['clause'], 'evidence': evidence, 'not_found': False,
                'confidence': 1.0, 'ambiguous': not array and len(found) > 1}
        try:
            extracted = extraction_model(code).model_validate(data)
            fields[code] = verify(code, extracted, pages, context, Settings(quote_fuzzy_threshold=.95, extraction_min_confidence=.8, _env_file=None), iss_value)
        except ValueError:
            fields[code] = {**result_template(code, iss_value), 'status': 'needs_review', 'not_found': False, 'reason': 'baseline_schema_error'}
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, VERSION + ':' + gold.isin)), 'isin': gold.isin, 'version': 1, 'status': 'ready',
            'documents': documents, 'iss_fields': {s.code: fields[s.code] for s in SPECS if s.iss},
            'risk_fields': {s.code: fields[s.code] for s in SPECS if not s.iss},
            'journal': {'algorithm': VERSION, 'models': [], 'cost_usd': '0', 'known_cost_usd': '0',
                        'duration_ms': int((time.monotonic() - start) * 1000), 'pages': len(pages),
                        'ocr_pages': loaded.ocr_pages, 'documents_used': sum(d.enabled for d in gold.documents)},
            'evaluation_mode': 'iss_only' if iss_only else VERSION}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Baseline regex-v1, без LLM/сети')
    parser.add_argument('--gold', default='eval/gold')
    parser.add_argument('--manifest', default='eval/manifest.json')
    parser.add_argument('--assets-root', default='eval')
    parser.add_argument('--split', choices=['dev', 'test'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--iss-only', action='store_true', help='Отдельный контрольный режим, не PDF extraction')
    args = parser.parse_args(argv)
    try:
        issues, _ = load_corpus(args.gold, args.assets_root, args.manifest, args.split)
        cards = [build_card(issue, args.iss_only) for issue in issues]
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(cards, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(f'Baseline: {len(cards)} выпусков → {output}')
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f'Ошибка baseline: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
