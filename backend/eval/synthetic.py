"""Two deterministic PDF/gold fixtures. Oracle cards exercise scoring, not model quality."""
from copy import deepcopy
from datetime import date
import hashlib
from html import escape
from pathlib import Path
import re
import uuid

import pymupdf

from app.cards.schema import SPECS, validate_value
from app.cards.verification import compare_iss, result_template
from app.documents.parser import chunks
from eval.capture import dump

SYNTHETIC_ISINS = ('RU000A10AXF7', 'RU000A10ZZZ0')


def pdf_bytes(pages):
    with pymupdf.open() as pdf:
        for paragraphs in pages:
            page = pdf.new_page(width=595, height=842)
            page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), ''.join('<p>' + escape(p) + '</p>' for p in paragraphs), css='body {font-size: 11pt;} p {margin-bottom: 12pt;}')
        pdf.set_metadata({'title': 'Synthetic bond evaluation fixture', 'author': 'Bond Card tests'})
        return pdf.tobytes(garbage=4, deflate=True, no_new_id=True)


def clauses(second=False):
    standard = [
        '3.1. Номинальная стоимость облигации составляет 1 000 рублей.',
        '3.2. Дата размещения: 10.01.2026.',
        '3.3. Дата погашения: 10.01.2028.',
        '3.4. Тип купона: фиксированный.',
        '3.5. Ставка купона с 1 по 2 купон составляет 12 процентов годовых.',
        '3.6. Купонные выплаты: 10.07.2026; 10.01.2027.',
        '3.7. Оферта put 10.01.2027 по цене 100 процентов номинала.',
        '3.8. Амортизация 10.01.2028: 100 процентов первоначального номинала.',
    ]
    risks = [
        '9.1. Ковенант: отношение долга к EBITDA не более 3.',
        '9.2. Ковенант: выплата дивидендов допускается при отсутствии просроченной задолженности.',
        '9.3. Владельцы вправе требовать досрочного погашения при нарушении ковенанта.',
        '9.4. Эмитент вправе досрочно погасить облигации по цене 100 процентов номинала, уведомив за 10 дней.',
        '9.5. Исполнение обеспечено поручительством ООО Пример в полном объёме.',
        '9.6. Кросс-дефолт: неисполнение иных долговых обязательств свыше 1000000 рублей.',
        '9.7. Эмитенту запрещена выплата дивидендов при наличии просроченной задолженности.',
        '9.8. Для участия в put-оферте владельцы предъявляют требования через депозитарий в течение 5 рабочих дней.',
    ]
    if second:
        standard[0] = '3.1. Номинальная стоимость облигации составляет 2 000 рублей.'
        standard[3] = '3.4. Тип купона: плавающий.'
        standard[4] = '3.5. Формула купона: ключевая ставка Банка России плюс 2 процентных пункта.'
        standard[7] = '3.8. Амортизация не предусмотрена.'
        risks[0] = '9.1. Ковенант: отношение долга к EBITDA не более 2, кроме периода реорганизации.'
        risks[2] = '9.3. При просрочке выплаты купона более 10 рабочих дней владельцы вправе потребовать досрочного погашения.'
        risks[3] = '9.4. По решению эмитента весь выпуск может быть погашен 10.01.2027 по номиналу с уведомлением за 15 дней.'
        risks[4:7] = ['9.5. Обеспечение и поручительство не предусмотрены.', '9.6. Кросс-дефолт не предусмотрен.', '9.7. Ограничения для эмитента не предусмотрены.']
    return [standard, risks]


def values(second=False):
    pages = clauses(second)
    clean = lambda i: re.sub(r'^\d+(?:\.\d+)+\.\s*', '', pages[1][i])
    return {
        'face_value': {'amount': '2000' if second else '1000', 'currency': 'RUB'},
        'placement_date': {'date': '2026-01-10'}, 'maturity_date': {'date': '2028-01-10'},
        'coupon_type': {'type': 'floating' if second else 'fixed', 'description': None},
        'coupon_rate': {'periods': [], 'formula': {'text': 'Ключевая ставка Банка России плюс 2 процентных пункта.', 'base': 'Ключевая ставка Банка России', 'spread_pp': '2', 'cap_percent': None, 'floor_percent': None, 'observation_rule': None, 'rounding_rule': None}} if second else {'periods': [{'from': 1, 'to': 2, 'rate_percent': '12', 'amount': None}], 'formula': None},
        'coupon_schedule': {'frequency_per_year': None, 'period_days': None, 'payments': [{'number': i, 'start': None, 'end': None, 'payment_date': day} for i, day in enumerate(['2026-07-10', '2027-01-10'], 1)]},
        'offers': {'events': [{'date': '2027-01-10', 'type': 'put', 'price_percent': '100'}]},
        'amortization': None if second else {'events': [{'date': '2028-01-10', 'amount': None, 'percent': '100', 'percent_base': 'initial_face_value'}]},
        'covenants': {'items': [{'condition': clean(i), 'test_date_rule': None, 'consequence': None, 'exceptions': None} for i in [0, 1]]},
        'holder_early_redemption': {'grounds': [{'trigger': clean(2), 'deadline': None, 'exceptions': None}]},
        'issuer_call_option': {'conditions': [{'trigger': clean(3), 'date_rule': None, 'price_rule': None, 'notice_rule': None}]},
        'guarantee_security': None if second else {'items': [{'type': 'surety', 'party': None, 'scope': clean(4), 'expiry': None, 'exceptions': None}]},
        'cross_default': None if second else {'items': [{'trigger': clean(5), 'threshold': None, 'entities': None, 'grace_period': None, 'consequence': None, 'exceptions': None}]},
        'issuer_restrictions': None if second else {'items': [{'action': clean(6), 'restriction': clean(6), 'exceptions': None, 'consequence': None}]},
        'offer_procedure': {'procedures': [{'offer_date': None, 'window_start': None, 'window_end': None, 'window_rule': None, 'via': None, 'submission': clean(7), 'settlement_rule': None}]},
    }


def make_fixture(root, second=False):
    root = Path(root)
    isin = SYNTHETIC_ISINS[int(second)]
    data = root / 'data' / 'synthetic' / isin
    data.mkdir(parents=True, exist_ok=True)
    content = pdf_bytes(clauses(second))
    pdf = data / 'decision.pdf'; pdf.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gold:' + digest))
    document = {'document_id': identifier, 'filename': 'synthetic-decision.pdf', 'path': pdf.relative_to(root).as_posix(), 'sha256': digest,
                'enabled': True, 'type': 'decision', 'origins': [{'source': 'synthetic', 'published_at': None, 'fetched_at': '2026-11-15T00:00:00Z'}]}
    quotes = {}
    with pymupdf.open(stream=content, filetype='pdf') as opened:
        for page_number, page in enumerate(opened, 1):
            fragments, _ = chunks(page.get_text('text', sort=True))
            for fragment in fragments:
                quotes[fragment['clause']] = {'quote': fragment['text'], 'document_id': identifier, 'page': page_number, 'clause': fragment['clause']}
    expected = values(second)
    iss_fields = deepcopy({s.code: expected[s.code] for s in SPECS if s.iss})
    if second:
        iss_fields['face_value']['amount'] = '1900'  # Intentional, annotated mismatch.
        iss_fields['coupon_rate'] = None  # ISS has no formula; do not infer it from one rate.
    snapshot = {'isin': isin, 'fetched_at': '2026-11-15T00:00:00Z', 'source_url': 'synthetic://iss',
                'synthetic': True, 'fields': iss_fields, 'raw': {}}
    dump(data / 'iss.json', snapshot)
    fields = {}
    risk_clauses = {'covenants': ['9.1', '9.2'], 'holder_early_redemption': ['9.3'], 'issuer_call_option': ['9.4'],
                    'guarantee_security': ['9.5'], 'cross_default': ['9.6'], 'issuer_restrictions': ['9.7'], 'offer_procedure': ['9.8']}
    for index, spec in enumerate(SPECS):
        code, value = spec.code, expected[spec.code]
        if value is None:
            fields[code] = {'value': None, 'not_found': True, 'reason': 'В синтетическом решении условие явно не предусмотрено.', 'expected_status': 'not_found'}
            continue
        value = validate_value(code, value)
        primary = quotes[f'3.{index + 1}'] if spec.iss else quotes[risk_clauses[code][0]]
        evidence, atoms = [], []
        if code == 'coupon_schedule':
            evidence = [{**primary, 'value_path': '/payments/1'}]
        elif not spec.iss:
            key = next(iter(value))
            for i, item in enumerate(value[key]):
                if i:
                    evidence.append({**quotes[risk_clauses[code][i]], 'value_path': f'/{key}/{i}'})
                category = {'covenants': 'covenants', 'issuer_call_option': 'call', 'holder_early_redemption': 'early_redemption', 'offer_procedure': 'put'}.get(code, 'other')
                atoms.append({'index': i, 'condition_id': f'{code}-{i}', 'category': category, 'alternatives': [], 'constraints': {}})
        fields[code] = {'value': value, 'not_found': False, **primary, 'evidence': evidence, 'atoms': atoms,
                        'iss_value': iss_fields.get(code), 'expected_status': compare_iss(code, value, iss_fields[code]) if spec.iss and iss_fields[code] is not None else 'extracted'}
    # One pre-approved paraphrase exercises slot matching without tuning on a real test set.
    fields['covenants']['atoms'][0]['constraints'] = {'condition': [['долга', 'долг'], ['ebitda'], ['не более']]}
    gold = {'schema_version': '1', 'isin': isin, 'kind': 'synthetic', 'as_of': '2026-11-15', 'issuer_group': 'synthetic-' + str(int(second)),
            'documents': [document], 'iss_fixture': (data / 'iss.json').relative_to(root).as_posix(), 'fields': fields,
            'review': {'annotators': ['synthetic-generator', 'synthetic-test-oracle'], 'resolved': True, 'verified_by': 'codex', 'needs_human_check': False},
            'todo': [], 'collection': {'complete_corpus': True, 'source': 'synthetic'}}
    path = root / 'gold' / 'synthetic' / (isin + '.json')
    dump(path, gold)
    return path, gold


def oracle_card(gold):
    mapping = {d['document_id']: str(uuid.uuid5(uuid.NAMESPACE_URL, 'oracle:' + d['sha256'])) for d in gold['documents']}
    documents = [{'id': mapping[d['document_id']], 'filename': d['filename'], 'sha256': d['sha256'], 'enabled': True, 'status': 'ready', 'origins': d['origins']} for d in gold['documents']]
    fields = {}
    for spec in SPECS:
        expected = gold['fields'][spec.code]
        result = {**result_template(spec.code, expected.get('iss_value')), 'user_status': None, 'user_value': None}
        if not expected['not_found']:
            result.update({key: deepcopy(expected.get(key)) for key in ('value', 'quote', 'page', 'clause', 'evidence')})
            result.update(document_id=mapping[expected['document_id']], status=expected['expected_status'], reason=None,
                          confidence=1, not_found=False, source={'kind': 'document'})
            for evidence in result['evidence']:
                evidence['document_id'] = mapping[evidence['document_id']]
        fields[spec.code] = result
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'oracle:' + gold['isin'])), 'isin': gold['isin'], 'version': 1, 'status': 'ready',
            'documents': documents, 'iss_fields': {s.code: fields[s.code] for s in SPECS if s.iss},
            'risk_fields': {s.code: fields[s.code] for s in SPECS if not s.iss},
            'journal': {'models': [], 'cost_usd': '0', 'duration_ms': 0, 'pages': 2, 'ocr_pages': 0}, 'evaluation_mode': 'synthetic-oracle'}


def generate(root):
    cards, entries = [], []
    for second in (False, True):
        path, gold = make_fixture(root, second)
        card = oracle_card(gold)
        cards.append(card)
        entries.append({'isin': gold['isin'], 'gold': path.relative_to(root).as_posix(), 'split': 'test', 'issuer_group': gold['issuer_group'], 'card_id': card['id']})
    dump(Path(root) / 'examples' / 'synthetic-cards.json', cards)
    negative = deepcopy(cards)
    negative[0]['risk_fields']['issuer_call_option']['quote'] = 'Несуществующая цитата: эмитент освобождён от всех обязательств.'
    dump(Path(root) / 'examples' / 'negative-cards.json', negative)
    return entries


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Создать детерминированные синтетические PDF/gold/oracle cards')
    parser.add_argument('--output-root', default='eval')
    args = parser.parse_args()
    entries = generate(Path(args.output_root))
    dump(Path(args.output_root) / 'manifest.synthetic.json', {'schema_version': '1', 'name': 'Synthetic scorer smoke, not model quality', 'seed': 42,
         'frozen': True, 'settings': {'schema_version': '1', 'prompt_version': '1', 'baseline': 'regex-v1', 'quote_fuzzy_threshold': .95}, 'issues': entries})
    print('Созданы 2 синтетических PDF/gold и oracle cards (не ответы модели).')
