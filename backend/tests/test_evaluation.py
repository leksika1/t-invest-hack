"""Offline regression tests for the evaluator, with independent hand-counted errors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import httpx
import pymupdf
import pytest

from app.cards.schema import SPECS
from eval.baseline import build_card, main as baseline_main
from eval.capture import dump
from eval.corpus import load_corpus, load_issue, read_json
from eval.inputs import from_api, from_json, validate_cards
from eval.matching import maximum_matching, value_equal
from eval.metrics import score_system
from eval.report import markdown
from eval.run import evaluate, main
from eval.schema import Atom, GoldIssue, Manifest
from eval.synthetic import clauses, generate, oracle_card, pdf_bytes


@pytest.fixture(scope='session')
def evaluation_assets(tmp_path_factory):
    root = tmp_path_factory.mktemp('evaluation')
    entries = generate(root)
    manifest = {'schema_version': '1', 'name': 'test corpus', 'seed': 42, 'frozen': True,
                'settings': {'baseline': 'regex-v1'}, 'issues': entries}
    dump(root / 'manifest.json', manifest)
    return root


@pytest.fixture
def evaluation(evaluation_assets):
    root = evaluation_assets
    issues, manifest = load_corpus(root / 'gold', root, root / 'manifest.json')
    cards = from_json(root / 'examples/synthetic-cards.json')
    return root, issues, manifest, cards


def test_synthetic_bytes_are_reproducible():
    assert hashlib.sha256(pdf_bytes(clauses())).digest() == hashlib.sha256(pdf_bytes(clauses())).digest()


def test_oracle_mechanics_pass_without_claiming_real_quality(evaluation):
    root, issues, manifest, cards = evaluation
    score = score_system(issues, cards)
    assert score['status'] == 'PASS'
    assert score['iss_accuracy'] == {'correct': 16, 'total': 16, 'value': 1}
    assert score['risk_micro'] == {'tp': 13, 'fp': 0, 'fn': 0, 'precision': 1, 'recall': 1}
    assert score['mismatch'] == {'correct': 1, 'total': 1}
    assert score['mvp_quality_status'] == 'FAIL'
    report = evaluate(issues, manifest, cards, root, root / 'manifest.json')
    assert report['mvp_corpus_ready'] is False
    assert 'Качество MVP: **FAIL**' in markdown(report)


def test_wrong_quote_is_a_hallucination_and_loses_one_true_positive(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[0].gold.isin]['risk_fields']['issuer_call_option']
    field['quote'] = 'Несуществующая цитата: эмитент вправе прекратить все выплаты.'
    score = score_system(issues, cards)
    assert score['status'] == 'FAIL'
    assert score['risk']['issuer_call_option']['tp'] == 1
    assert score['risk']['issuer_call_option']['fp'] == 1
    assert score['risk']['issuer_call_option']['fn'] == 1
    assert score['hallucinations']['hallucinations_without_citation'] == 1


def test_hidden_review_is_false_negative_not_a_success(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[0].gold.isin]['risk_fields']['covenants']
    field.update(value=None, quote=None, status='needs_review', not_found=False)
    score = score_system(issues, cards)
    assert score['risk']['covenants']['tp'] == 2
    assert score['risk']['covenants']['fn'] == 2
    assert score['risk']['covenants']['fp'] == 0
    assert score['needs_review']['count'] == 1
    assert score['hallucinations']['hallucinations_without_citation'] == 0
    assert score['status'] == 'FAIL'


def test_missing_fields_and_issues_stay_in_denominators(evaluation):
    _, issues, _, cards = evaluation
    del cards[issues[0].gold.isin]['iss_fields']['face_value']
    del cards[issues[1].gold.isin]
    score = score_system(issues, cards)
    assert score['iss_accuracy']['total'] == 16
    assert score['iss_accuracy']['correct'] == 7
    assert score['status'] == 'FAIL'
    assert any(e['code'] == 'missing_card' for e in score['errors'])


def test_iss_mismatch_requires_both_correct_values_and_status(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[1].gold.isin]['iss_fields']['face_value']
    field['status'] = 'verified_iss'
    assert score_system(issues, cards)['iss_accuracy']['correct'] == 15
    field['status'] = 'mismatch_iss'
    field['iss_value']['amount'] = '1999'
    score = score_system(issues, cards)
    assert score['iss_accuracy']['correct'] == 15
    assert score['hallucinations']['hallucinations_wrong_value'] == 1


def test_iss_only_is_measured_separately(evaluation):
    _, issues, _, _ = evaluation
    cards = validate_cards([build_card(issue, iss_only=True) for issue in issues])
    score = score_system(issues, cards)
    assert score['iss_accuracy']['correct'] < 16
    assert score['iss_only_accuracy']['value'] == 1
    assert score['risk_micro']['recall'] == 0
    assert score['hallucinations']['hallucinations_without_citation'] == 0
    assert score['modes'] == {'iss_only': 2}
    assert score['status'] == 'FAIL'


def test_duplicates_do_not_inflate_recall_and_raise_false_positives(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[0].gold.isin]['risk_fields']['issuer_call_option']
    field['value']['conditions'].append(deepcopy(field['value']['conditions'][0]))
    field['evidence'].append({k: field[k] for k in ('quote', 'document_id', 'page', 'clause')} | {'value_path': '/conditions/1'})
    score = score_system(issues, cards)
    assert score['risk']['issuer_call_option']['tp'] == 2
    assert score['risk']['issuer_call_option']['fp'] == 1
    assert score['risk']['issuer_call_option']['recall'] == 1


def test_put_condition_shared_by_offers_and_procedure_is_not_counted_twice(evaluation):
    _, issues, _, cards = evaluation
    for issue in issues:
        issue.gold.fields['offers'].atoms = [Atom(index=0, condition_id='offer_procedure-0', category='put')]
    score = score_system(issues, cards)
    assert score['categories']['put'] == {'tp': 2, 'fn': 0, 'recall': 1}
    field = cards[issues[0].gold.isin]['risk_fields']['offer_procedure']
    field.update(value=None, quote=None, status='needs_review', not_found=False)
    score = score_system(issues, cards)
    assert score['categories']['put'] == {'tp': 1, 'fn': 1, 'recall': .5}


def test_threshold_boundaries_use_real_denominators(evaluation):
    _, issues, _, cards = evaluation
    examples, predictions = [], {}
    template = issues[0]
    for i in range(10):
        issue = deepcopy(template); issue.gold.isin = 'RU' + str(i).zfill(9) + '0'
        card = deepcopy(cards[template.gold.isin]); card['isin'] = issue.gold.isin
        examples.append(issue); predictions[issue.gold.isin] = card
    # 17/20 covenants = exactly 85%; removing a fourth gives 80%.
    for issue in examples[:3]:
        field = predictions[issue.gold.isin]['risk_fields']['covenants']
        field['value']['items'] = field['value']['items'][:1]; field['evidence'] = []
    score = score_system(examples, predictions)
    assert score['categories']['covenants']['recall'] == .85
    assert score['gates']['covenants_recall_85'] is True
    field = predictions[examples[3].gold.isin]['risk_fields']['covenants']
    field['value']['items'] = field['value']['items'][:1]; field['evidence'] = []
    assert score_system(examples, predictions)['gates']['covenants_recall_85'] is False
    # 38/40 standard fields = exactly 95%; 37/40 fails.
    examples = examples[:5]; predictions = {i.gold.isin: predictions[i.gold.isin] for i in examples}
    for issue in examples[:2]:
        predictions[issue.gold.isin]['iss_fields']['face_value'].update(value=None, status='needs_review')
    assert score_system(examples, predictions)['iss_accuracy']['value'] == .95
    assert score_system(examples, predictions)['gates']['iss_accuracy_95'] is True
    predictions[examples[2].gold.isin]['iss_fields']['face_value'].update(value=None, status='needs_review')
    assert score_system(examples, predictions)['gates']['iss_accuracy_95'] is False


def test_fabricated_item_on_negative_example_is_false_positive(evaluation):
    _, issues, _, cards = evaluation
    first, second = [cards[i.gold.isin] for i in issues]
    invented = deepcopy(first['risk_fields']['cross_default'])
    invented['document_id'] = second['documents'][0]['id']
    second['risk_fields']['cross_default'] = invented
    score = score_system(issues, cards)
    assert score['risk']['cross_default']['tp'] == 1
    assert score['risk']['cross_default']['fp'] == 1
    assert score['hallucinations']['hallucinations_without_citation'] == 1


def test_empty_extracted_array_is_not_a_correct_negative(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[1].gold.isin]['risk_fields']['cross_default']
    field.update(value={'items': []}, quote=None, status='extracted', not_found=False)
    score = score_system(issues, cards)
    assert score['risk']['cross_default']['fp'] == 1
    assert score['hallucinations']['hallucinations_without_citation'] == 1
    row = next(r for r in score['issues'] if r['isin'] == issues[1].gold.isin)
    assert not next(f for f in row['field_results'] if f['code'] == 'cross_default')['correct']


@pytest.mark.parametrize('change', ['page', 'sha', 'clause', 'evidence'])
def test_wrong_document_page_clause_or_array_evidence_cannot_pass(evaluation, change):
    _, issues, _, cards = evaluation
    card = cards[issues[0].gold.isin]
    field = card['risk_fields']['covenants']
    if change == 'page':
        field['page'] = 100
    elif change == 'sha':
        card['documents'][0]['sha256'] = '0' * 64
    elif change == 'clause':
        field['clause'] = '900.1'
    else:
        field['evidence'] = []
    score = score_system(issues, cards)
    assert score['status'] == 'FAIL'
    assert score['risk']['covenants']['fn'] >= 1
    assert score['hallucinations']['hallucinations_without_citation'] >= 1


def test_changed_number_or_exception_with_real_quote_is_semantic_error(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[1].gold.isin]['risk_fields']['covenants']
    field['value']['items'][0]['condition'] = 'Ковенант: отношение долга к EBITDA не более 2.'
    score = score_system(issues, cards)
    assert score['risk']['covenants']['fn'] == 1
    assert score['hallucinations']['hallucinations_wrong_value'] == 1


def test_user_correction_does_not_fix_machine_accuracy(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[0].gold.isin]['iss_fields']['face_value']
    field['value']['amount'] = '1234'
    field.update(user_status='corrected', user_value={'amount': '1000', 'currency': 'RUB'})
    score = score_system(issues, cards)
    assert score['iss_accuracy']['correct'] == 15
    assert score['human_edits_ignored'] == 1


def test_decimal_currency_dates_order_and_coupon_ranges_normalize():
    assert value_equal('face_value', {'amount': '1000,00', 'currency': 'SUR'}, {'amount': '1000', 'currency': 'RUB'})
    assert value_equal('coupon_rate', {'periods': [{'from': 1, 'to': 2, 'rate_percent': '12.00'}]}, {'periods': [{'from': 2, 'to': 2, 'rate_percent': '12'}, {'from': 1, 'to': 1, 'rate_percent': '12'}]})
    assert not value_equal('coupon_rate', {'periods': [{'from': 1, 'to': 1, 'rate_percent': '12'}]}, {'periods': [{'from': 1, 'to': 2, 'rate_percent': '12'}]})
    assert not value_equal('coupon_rate', {'periods': [], 'formula': {'text': 'КС + 2'}}, {'periods': [{'from': 1, 'to': 1, 'rate_percent': '2'}]})
    assert maximum_matching([[0, 1], [0]]) == {0: 1, 1: 0}


def test_approved_slot_paraphrase_matches_but_not_opposite_operator(evaluation):
    _, issues, _, cards = evaluation
    field = cards[issues[0].gold.isin]['risk_fields']['covenants']
    field['value']['items'][0]['condition'] = 'Отношение долга к EBITDA не более 3.'
    assert score_system(issues, cards)['risk']['covenants']['fn'] == 0
    field['value']['items'][0]['condition'] = 'Отношение долга к EBITDA не менее 3.'
    assert score_system(issues, cards)['risk']['covenants']['fn'] == 1


def test_unknown_cost_is_not_zero_and_time_is_per_issue(evaluation):
    _, issues, _, cards = evaluation
    cards[issues[0].gold.isin]['journal'].update(cost_usd=None, known_cost_usd='.25', duration_ms=None)
    score = score_system(issues, cards)
    assert score['cost'] == {'total_usd': None, 'known_usd': '0.25', 'unknown_issues': 1}
    assert score['duration']['total_ms'] is None
    assert score['duration']['unknown_issues'] == 1


def test_unreviewed_gold_and_unscorable_cannot_silently_pass(evaluation):
    _, issues, _, cards = evaluation
    issues[0].gold.review.needs_human_check = True
    issues[1].gold.fields['face_value'].unscorable = True
    issues[1].gold.fields['face_value'].reason = 'Неопределённая редакция'
    score = score_system(issues, cards)
    assert score['status'] == 'FAIL'
    assert score['iss_accuracy']['total'] == 7
    assert any(e['field'] == '*' for e in score['exclusions'])
    assert score['approved_issues'] == 1


def test_unreviewed_gold_does_not_exempt_falsely_attributed_iss_value(evaluation):
    _, issues, _, cards = evaluation
    issues[0].gold.review.needs_human_check = True
    field = cards[issues[0].gold.isin]['iss_fields']['face_value']
    field.update(value={'amount': '99999', 'currency': 'RUB'}, quote=None, source={'kind': 'moex_iss'}, status='extracted')
    score = score_system(issues, cards)
    assert score['hallucinations']['hallucinations_without_citation'] == 1
    assert score['status'] == 'FAIL'


def test_no_positives_is_na_not_100_percent(evaluation):
    _, issues, _, cards = evaluation
    for issue in issues:
        card = cards[issue.gold.isin]
        for code in ('covenants', 'issuer_call_option', 'holder_early_redemption', 'offer_procedure'):
            field = issue.gold.fields[code]
            field.value, field.not_found, field.atoms = None, True, []
            card['risk_fields'][code].update(value=None, quote=None, status='not_found', not_found=True)
    score = score_system(issues, cards)
    assert score['categories']['call']['recall'] is None
    assert score['status'] == 'FAIL'


def test_baseline_extracts_multiple_clauses_and_uses_no_model(evaluation, monkeypatch):
    _, issues, _, _ = evaluation
    def forbidden(*args, **kwargs):
        raise AssertionError('Model transport must never run in evaluation')
    monkeypatch.setattr('app.llm.client.LLMClient.request', forbidden)
    cards = validate_cards([build_card(issue) for issue in issues])
    first = cards[issues[0].gold.isin]
    assert first['iss_fields']['face_value']['status'] == 'verified_iss'
    assert len(first['risk_fields']['covenants']['value']['items']) >= 2
    assert first['risk_fields']['issuer_call_option']['quote']
    score = score_system(issues, cards)
    assert score['risk']['issuer_call_option']['tp'] == 1
    assert score['risk']['issuer_call_option']['fn'] == 1  # Deliberately different phrasing in PDF #2.
    assert score['status'] == 'FAIL'
    assert score['cost']['total_usd'] == '0'


def test_baseline_does_not_read_annotation_answers(evaluation):
    _, issues, _, _ = evaluation
    original = build_card(issues[0])
    altered = deepcopy(issues[0])
    altered.gold.fields['face_value'].value['amount'] = '123456'
    altered.gold.fields['covenants'].value['items'][0]['condition'] = 'Fake annotation unrelated to PDF'
    predicted = build_card(altered)
    assert predicted['iss_fields'] == original['iss_fields']
    assert predicted['risk_fields'] == original['risk_fields']


def test_gold_validator_rejects_missing_code_extra_code_wrong_type(evaluation):
    _, issues, _, _ = evaluation
    raw = issues[0].gold.model_dump(mode='json')
    for mutation in ('missing', 'unknown', 'number', 'atoms', 'citation'):
        broken = deepcopy(raw)
        if mutation == 'missing': del broken['fields']['offers']
        elif mutation == 'unknown': broken['fields']['typo'] = broken['fields'].pop('offers')
        elif mutation == 'number': broken['fields']['face_value']['value']['amount'] = 1000
        elif mutation == 'atoms': broken['fields']['covenants']['atoms'].pop()
        else: broken['fields']['face_value']['page'] = 0
        with pytest.raises(ValueError): GoldIssue.model_validate(broken)


def test_manifest_disallows_issuer_leakage(evaluation):
    _, _, manifest, _ = evaluation
    data = manifest.model_dump()
    data['issues'][1].update(issuer_group=data['issues'][0]['issuer_group'], split='dev')
    with pytest.raises(ValueError, match='эмитент'): Manifest.model_validate(data)


def test_pdf_hash_path_and_gold_quote_are_checked(evaluation, tmp_path):
    root, issues, _, _ = evaluation
    raw = issues[0].gold.model_dump(mode='json')
    path = tmp_path / 'bad.json'
    for mutation in ('hash', 'path', 'quote', 'iss'):
        data = deepcopy(raw)
        if mutation == 'hash': data['documents'][0]['sha256'] = '0' * 64
        elif mutation == 'path': data['documents'][0]['path'] = '../outside.pdf'
        elif mutation == 'iss': data['fields']['face_value']['iss_value']['amount'] = '999'
        else: data['fields']['face_value']['quote'] = 'Не существует в PDF'
        dump(path, data)
        with pytest.raises(ValueError): load_issue(path, root)


def test_frozen_ocr_pages_are_counted_and_failed_pages_block_pass(evaluation, tmp_path):
    root, issues, _, _ = evaluation
    copied = tmp_path / 'corpus'
    shutil.copytree(root, copied)
    gold_path = copied / issues[0].gold_path.relative_to(root)
    raw = read_json(gold_path);document = raw['documents'][0];pdf_path = copied / document['path']
    with pymupdf.open(pdf_path) as opened:
        opened.new_page()
        content = opened.tobytes(no_new_id=True)
    pdf_path.write_bytes(content)
    document['sha256'] = hashlib.sha256(content).hexdigest()
    with pymupdf.open(pdf_path) as opened:
        pages = [{'number': n, 'text': page.get_text('text', sort=True).strip() if n < 3 else 'Сканированная страница.', 'ocr': n == 3, 'error': None} for n, page in enumerate(opened, 1)]
    sidecar = copied / 'ocr.json'
    dump(sidecar, {'pdf_sha256': document['sha256'], 'pages': pages})
    document.update(pages_file='ocr.json', pages_sha256=hashlib.sha256(sidecar.read_bytes()).hexdigest())
    dump(gold_path, raw)
    loaded = load_issue(gold_path, copied)
    assert loaded.ocr_pages == 1 and not loaded.page_errors
    assert build_card(loaded)['journal']['ocr_pages'] == 1
    pages[2]['ocr'] = 'true'
    dump(sidecar, {'pdf_sha256': document['sha256'], 'pages': pages})
    document['pages_sha256'] = hashlib.sha256(sidecar.read_bytes()).hexdigest();dump(gold_path, raw)
    with pytest.raises(ValueError): load_issue(gold_path, copied)
    pages[2]['ocr'] = True
    pages[2].update(text='', error='ocr_failed')
    dump(sidecar, {'pdf_sha256': document['sha256'], 'pages': pages})
    document['pages_sha256'] = hashlib.sha256(sidecar.read_bytes()).hexdigest();dump(gold_path, raw)
    loaded = load_issue(gold_path, copied)
    score = score_system([loaded], validate_cards([oracle_card(raw)]))
    assert score['gates']['complete_comparable_inputs'] is False
    assert score['status'] == 'FAIL'


def test_input_rejects_bad_structure_and_duplicate_isin(evaluation):
    _, _, _, cards = evaluation
    first = next(iter(cards.values()))
    with pytest.raises(ValueError): validate_cards([first, first])
    with pytest.raises(ValueError): validate_cards({'items': []})
    raw = deepcopy(first);raw['iss_fields']['face_value']['status'] = 'confirmed'
    with pytest.raises(ValueError): validate_cards([raw])
    raw = deepcopy(first);raw['journal']['cost_usd'] = float('nan')
    with pytest.raises(ValueError): validate_cards([raw])
    raw = deepcopy(first);raw['risk_fields']['covenants']['source'] = {'kind': 'moex_iss'}
    with pytest.raises(ValueError): validate_cards([raw])


def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(tmp_path):
    path = tmp_path / 'bad.json'
    for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
        path.write_text(text, encoding='utf-8')
        with pytest.raises(ValueError): read_json(path)


def test_api_adapter_only_gets_frozen_ids_or_explicit_latest(evaluation):
    _, issues, manifest, cards = evaluation
    manifest.issues[0].card_id = None
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == '/api/cards':
            card = cards[request.url.params['isin']]
            return httpx.Response(200, json={'items': [{'id': card['id']}]})
        return httpx.Response(200, json=next(card for card in cards.values() if request.url.path.endswith(card['id'])))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetched, selections = from_api('https://dev.example', issues, manifest, client)
    assert set(fetched) == set(cards)
    assert {r.method for r in requests} == {'GET'}
    assert selections[0]['selection'] == 'latest' and selections[1]['selection'] == 'manifest'
    with pytest.raises(ValueError): from_api('https://user:secret@example.org', issues, manifest)


def test_cli_pass_fail_and_invalid_exit_codes(evaluation, tmp_path):
    root, _, _, cards = evaluation
    args = ['--gold', str(root / 'gold'), '--assets-root', str(root), '--manifest', str(root / 'manifest.json'), '--cards', str(root / 'examples/synthetic-cards.json'), '--with-baseline', '--output', str(tmp_path / 'report.md')]
    assert main(args) == 0
    report = read_json(tmp_path / 'report.json')
    assert report['systems']['pipeline']['status'] == 'PASS'
    assert report['systems']['baseline']['status'] == 'FAIL'
    assert len(report['corpus']['issues'][0]['documents'][0]['sha256']) == 64
    card = next(iter(cards.values()));card['risk_fields']['issuer_call_option']['quote'] = 'Выдуманная цитата'
    dump(tmp_path / 'bad-cards.json', list(cards.values()))
    args[args.index('--cards') + 1] = str(tmp_path / 'bad-cards.json')
    assert main(args) == 1
    assert read_json(tmp_path / 'report.json')['status'] == 'FAIL'
    args[args.index('--cards') + 1] = str(tmp_path / 'missing.json')
    assert main(args) == 2
    assert baseline_main(['--gold', str(root / 'gold'), '--assets-root', str(root), '--manifest', str(root / 'manifest.json'), '--output', str(tmp_path / 'baseline.json')]) == 0
    assert len(from_json(tmp_path / 'baseline.json')) == 2


def test_capture_keeps_iss_failure_as_todo_not_fake_data(tmp_path, monkeypatch):
    from eval import capture as module
    from app.iss.client import IssError
    def unavailable(self, isin): raise IssError('offline')
    monkeypatch.setattr(module.MoexIssSource, 'resolve', unavailable)
    class Blocked:
        name = 'prime'
        def __init__(self, settings): pass
        def discover(self, identity): raise RuntimeError('blocked')
    monkeypatch.setitem(module.DEMOS, 'RU000A10ATW2', Blocked)
    result = module.capture('RU000A10ATW2', tmp_path)
    assert not result['iss'] and result['documents'] == 0
    gold = GoldIssue.model_validate(read_json(tmp_path / 'gold/demo/RU000A10ATW2.json'))
    assert not gold.approved and all(field.unscorable for field in gold.fields.values())
    assert gold.review.needs_human_check is True and gold.review.verified_by == 'codex'
    with pytest.raises(ValueError, match='существует'): module.capture('RU000A10ATW2', tmp_path)


def test_shipped_gold_templates_and_real_drafts_validate():
    root = Path(__file__).parents[1] / 'eval'
    issues, _ = load_corpus(root / 'gold', root, root / 'manifest.json')
    assert len(issues) == 4
    drafts = [issue for issue in issues if issue.gold.kind == 'real']
    assert len(drafts) == 2 and all(not i.gold.approved for i in drafts)
    assert all(i.gold.review.needs_human_check and i.gold.documents for i in drafts)
    assert all(i.snapshot.get('fetched_at') for i in drafts)
    assert not GoldIssue.model_validate(read_json(root / 'templates/gold.json')).approved
