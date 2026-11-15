"""Count every denominator explicitly; abstentions and exclusions cannot buy a PASS."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import math

from app.cards.verification import anchors_supported, normalized_text, project_iss, quote_matches
from app.documents.parser import chunks
from eval.matching import atom_equal, maximum_matching, normal, value_equal
from eval.schema import ISS_CODES, RISK_CODES, RISK_ARRAYS


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def totals(tp=0, fp=0, fn=0):
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn)}


def numeric(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (ValueError, InvalidOperation):
        return None


def honest_iss(field, code, snapshot):
    return (code in ISS_CODES and field.get('source', {}).get('kind') == 'moex_iss'
            and field.get('status') == 'extracted' and field.get('value') is not None and not field.get('quote')
            and value_equal(code, field['value'], project_iss(code, snapshot['fields'].get(code))))


class EvidenceChecker:
    def __init__(self, loaded, card):
        self.loaded = loaded
        self.documents = {d['id']: d for d in card.get('documents', [])}
        self.by_hash = {d.sha256: d for d in loaded.gold.documents if d.enabled}
        self.fragments = {}
        for document in loaded.gold.documents:
            previous = None
            for (identifier, page), text in sorted(loaded.pages.items()):
                if identifier == document.document_id:
                    fragments, previous = chunks(text, previous)
                    self.fragments[(identifier, page)] = fragments

    def citation(self, citation):
        if not isinstance(citation, dict):
            return False
        identifier, page, quote = citation.get('document_id'), citation.get('page'), citation.get('quote')
        if not isinstance(identifier, str) or not isinstance(page, int) or isinstance(page, bool) or not isinstance(quote, str):
            return False
        document = self.documents.get(identifier)
        if not document or document.get('enabled', True) is not True or document.get('status', 'ready') != 'ready':
            return False
        gold_document = self.by_hash.get(document.get('sha256'))
        if not gold_document:
            return False
        key = (gold_document.document_id, page)
        if not quote_matches(quote, self.loaded.pages.get(key, ''), 0.95):
            return False
        clause = citation.get('clause')
        return any((clause is None or clause == fragment['clause']) and quote_matches(quote, fragment['text'], 0.95)
                   for fragment in self.fragments.get(key, []))

    def proof(self, field, code, index=None):
        evidence = field.get('evidence', [])
        citations = [{k: field.get(k) for k in ('quote', 'document_id', 'page', 'clause')}, *evidence]
        if not all(self.citation(c) for c in citations):
            return False
        value = field.get('value')
        if index is not None:
            array = RISK_ARRAYS.get(code, 'events')
            items = (value or {}).get(array, [])
            quotes = [field.get('quote')] if index == 0 else [c.get('quote') for c in evidence if c.get('value_path') == f'/{array}/{index}']
            return bool(quotes) and supported(items[index], ' '.join(q for q in quotes if isinstance(q, str)))
        for key, items in (value or {}).items():
            if isinstance(items, list):
                for i, item in enumerate(items):
                    quotes = [field.get('quote')] if i == 0 else [c.get('quote') for c in evidence if c.get('value_path') == f'/{key}/{i}']
                    if not quotes or not supported(item, ' '.join(q for q in quotes if isinstance(q, str))):
                        return False
        return value is not None and supported(value, ' '.join(c['quote'] for c in citations))


def supported(value, quote):
    # A verbatim text leaf containing DD.MM.YYYY is supported even though the
    # application's numeric guard tokenizes that date as several decimals.
    if isinstance(value, str) and normalized_text(value) in normalized_text(quote):
        return True
    if isinstance(value, dict):
        return all(supported(v, quote) for k, v in value.items() if k not in ('number', 'from', 'to', 'frequency_per_year', 'period_days'))
    if isinstance(value, list):
        return all(supported(v, quote) for v in value)
    return anchors_supported(value, quote)


def score_system(issues, cards, label='Карточки конвейера', mvp_corpus_ready=False):
    counts = Counter()
    risk = {code: Counter() for code in RISK_CODES}
    category_conditions = {}
    errors, rows, exclusions = [], [], []
    all_fields = len(issues) * 15
    models = set()
    known_cost = Decimal(0)
    unknown_cost_issues = 0
    duration_known = 0.0
    unknown_duration_issues = 0
    modes = Counter()

    for loaded in issues:
        gold = loaded.gold
        card = cards.get(gold.isin)
        row = {'isin': gold.isin, 'kind': gold.kind, 'approved': gold.approved, 'card_id': card.get('id') if card else None,
               'version': card.get('version') if card else None, 'field_results': [], 'collection': gold.collection}
        rows.append(row)
        if not gold.approved:
            exclusions.append({'isin': gold.isin, 'field': '*', 'reason': 'gold_needs_review_or_todo', 'todo': gold.todo})
        if not card:
            errors.append({'isin': gold.isin, 'code': 'missing_card'})
            card = {'status': 'failed', 'documents': [], 'iss_fields': {}, 'risk_fields': {}, 'journal': {}}
        fields = {**card['iss_fields'], **card['risk_fields']}
        absent = set(ISS_CODES + RISK_CODES) - fields.keys()
        if absent:
            errors.append({'isin': gold.isin, 'code': 'missing_fields', 'fields': sorted(absent)})
        if card['status'] != 'ready':
            errors.append({'isin': gold.isin, 'code': 'card_not_ready', 'status': card['status']})
        if loaded.page_errors:
            errors.append({'isin': gold.isin, 'code': 'source_page_errors', 'pages': loaded.page_errors})
        actual = {d['sha256'] for d in card['documents'] if d.get('enabled', True)}
        expected = {d.sha256 for d in gold.documents if d.enabled}
        comparable_inputs = actual == expected
        if not comparable_inputs:
            errors.append({'isin': gold.isin, 'code': 'document_set_mismatch', 'expected_sha256': sorted(expected), 'actual_sha256': sorted(actual)})
        checker = EvidenceChecker(loaded, card)
        journal = card.get('journal', {})
        cost = numeric(journal.get('cost_usd'))
        known = cost if cost is not None else numeric(journal.get('known_cost_usd'))
        known_cost += known or Decimal(0)
        unknown_cost_issues += cost is None
        duration = journal.get('duration_ms')
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
            duration = None
            unknown_duration_issues += 1
        else:
            duration_known += duration
        models.update(m for m in journal.get('models', []) if isinstance(m, str))
        iss_only = sum(f.get('source', {}).get('kind') == 'moex_iss' and f.get('value') is not None for f in fields.values())
        mode = 'iss_only' if iss_only and not any(f.get('value') is not None and f.get('source', {}).get('kind') != 'moex_iss' for f in fields.values()) else 'document_extraction'
        if not any(f.get('value') is not None for f in fields.values()):
            mode = 'no_shown_values'
        if card.get('evaluation_mode') in ('synthetic-oracle', 'regex-v1'):
            mode = card['evaluation_mode']
        modes[mode] += 1
        row.update(mode=mode, cost_usd=str(cost) if cost is not None else None, known_cost_usd=str(known) if known is not None else None,
                   duration_ms=duration, documents=len(actual), pages=journal.get('pages'), ocr_pages=journal.get('ocr_pages'), models=journal.get('models', []))
        for code, expected_field in gold.fields.items():
            field = fields.get(code, {})
            status = field.get('status')
            shown = field.get('value') is not None and card['status'] == 'ready'
            reviewed = status == 'needs_review'
            counts['all_review'] += reviewed
            counts['all_not_found'] += status == 'not_found'
            counts['all_shown'] += shown
            counts['human_edits_ignored'] += field.get('user_status') in ('confirmed', 'corrected')
            eligible = gold.approved and not expected_field.unscorable
            result = {'code': code, 'eligible': eligible, 'status': status, 'correct': False, 'shown': shown}
            row['field_results'].append(result)
            if not eligible:
                if expected_field.unscorable:
                    exclusions.append({'isin': gold.isin, 'field': code, 'reason': expected_field.reason})
                # Audit citations even when semantic truth cannot be scored.
                if shown and not honest_iss(field, code, loaded.snapshot):
                    array = RISK_ARRAYS.get(code)
                    items = (field.get('value') or {}).get(array, []) if array else []
                    failures = sum(not checker.proof(field, code, i) for i in range(len(items))) if items else int(not checker.proof(field, code))
                    counts['hallucinations_without_citation'] += failures
                    counts['hallucinations'] += failures
                continue
            counts['scorable'] += 1
            counts['review'] += reviewed
            counts['not_found'] += status == 'not_found'
            counts['shown'] += shown
            direct_iss = shown and code in ISS_CODES and field.get('source', {}).get('kind') == 'moex_iss'
            if code in ISS_CODES:
                counts['iss_total'] += 1
                variants = [expected_field.value, *expected_field.alternatives]
                truth = shown and any(value_equal(code, field['value'], v) for v in variants if v is not None)
                if expected_field.expected_status in ('verified_iss', 'mismatch_iss'):
                    truth = truth and value_equal(code, project_iss(code, field.get('iss_value')), project_iss(code, expected_field.iss_value))
                proof = shown and checker.proof(field, code)
                if direct_iss:
                    counts['iss_only_total'] += 1
                    honest = honest_iss(field, code, loaded.snapshot)
                    counts['iss_only_correct'] += honest
                    result['iss_only_correct'] = honest
                    # Direct ISS does not count as successful PDF extraction.
                    if not honest:
                        counts['hallucinations'] += 1
                        counts['hallucinations_without_citation'] += 1
                    result['reason'] = 'iss_only_separate_metric'
                else:
                    negative = (expected_field.not_found and status == 'not_found' and field.get('value') is None and field.get('not_found') is True)
                    correct = comparable_inputs and card['status'] == 'ready' and (negative or (truth and proof and status == expected_field.expected_status and not reviewed))
                    result.update(correct=bool(correct), quote_confirmed=bool(proof))
                    counts['iss_correct'] += bool(correct)
                    counts['covered'] += bool(correct and shown)
                    if shown:
                        counts['hallucinations_without_citation'] += not proof
                        counts['hallucinations_wrong_value'] += not truth
                        counts['hallucinations'] += not proof or not truth
                    if expected_field.expected_status == 'mismatch_iss':
                        counts['mismatch_total'] += 1
                        counts['mismatch_correct'] += bool(correct)
                if not result['correct']:
                    result['reason'] = result.get('reason', 'value_source_or_status_mismatch')
                if code == 'offers' and expected_field.atoms:
                    expected_items = (expected_field.value or {}).get('events', [])
                    predicted_items = (field.get('value') or {}).get('events', []) if shown else []
                    for atom in expected_field.atoms:
                        matched = any(atom_equal(expected_items[atom.index], item, atom) and checker.proof(field, code, j)
                                      and status == expected_field.expected_status and not reviewed and comparable_inputs
                                      for j, item in enumerate(predicted_items))
                        if atom.category != 'other':
                            record = category_conditions.setdefault((gold.isin, atom.condition_id), {'categories': set(), 'matches': []})
                            record['categories'].add(atom.category)
                            record['matches'].append(matched)
            else:
                array = RISK_ARRAYS[code]
                expected_items = (expected_field.value or {}).get(array, [])
                predicted_items = (field.get('value') or {}).get(array, []) if shown else []
                atoms = sorted(expected_field.atoms, key=lambda a: a.index)
                truth_edges = [[j for j, item in enumerate(predicted_items) if atom_equal(expected_items[i], item,
                                 atom.model_copy(update={'alternatives': [*atom.alternatives, *[v[array][i] for v in expected_field.alternatives]]}))]
                               for i, atom in enumerate(atoms)]
                proof = [checker.proof(field, code, j) for j in range(len(predicted_items))]
                edges = [[j for j in candidates if proof[j] and status == 'extracted' and comparable_inputs] for candidates in truth_edges]
                matching = maximum_matching(edges)
                truth_matching = maximum_matching(truth_edges)
                tp, fn = len(matching), len(atoms) - len(matching)
                fp = len(predicted_items) - tp if not reviewed else 0
                risk[code].update(tp=tp, fp=fp, fn=fn)
                counts['hallucinations_without_citation'] += sum(not value for value in proof)
                unsupported = set(range(len(predicted_items))) - set(truth_matching.values())
                counts['hallucinations_wrong_value'] += len(unsupported)
                counts['hallucinations'] += sum(not proof[j] or j in unsupported for j in range(len(predicted_items)))
                if shown and not predicted_items:
                    counts['hallucinations_wrong_value'] += 1
                    counts['hallucinations'] += 1
                    counts['hallucinations_without_citation'] += not checker.proof(field, code)
                    risk[code]['fp'] += 1  # Empty extracted array is not an annotated absence.
                    fp += 1
                negative = expected_field.not_found and card['status'] == 'ready' and not shown and status == 'not_found' and field.get('not_found') is True
                risk[code]['tn_fields'] += negative
                result.update(correct=bool(negative or (tp == len(atoms) and not fp and not reviewed and shown)), tp=tp, fp=fp, fn=fn, quote_confirmed=bool(proof and all(proof)))
                counts['covered'] += bool(shown and result['correct'])
                for i, atom in enumerate(atoms):
                    if atom.category != 'other':
                        key = (gold.isin, atom.condition_id)
                        record = category_conditions.setdefault(key, {'categories': set(), 'matches': []})
                        record['categories'].add(atom.category)
                        record['matches'].append(i in matching)
            if not result['correct']:
                errors.append({'isin': gold.isin, 'code': code, 'reason': result.get('reason', 'risk_atom_missed_or_false_positive'),
                               'expected_status': expected_field.expected_status, 'actual_status': status,
                               'document_id': field.get('document_id'), 'page': field.get('page')})
    risk_metrics = {code: {**totals(v['tp'], v['fp'], v['fn']), 'tn_fields': v['tn_fields']} for code, v in risk.items()}
    micro = totals(sum(v['tp'] for v in risk.values()), sum(v['fp'] for v in risk.values()), sum(v['fn'] for v in risk.values()))
    categories = {}
    for category in ('covenants', 'call', 'early_redemption', 'put', 'combined'):
        selected = [record for record in category_conditions.values() if category == 'combined' or category in record['categories']]
        tp = sum(all(record['matches']) for record in selected)
        categories[category] = {'tp': tp, 'fn': len(selected) - tp, 'recall': ratio(tp, len(selected))}
    accuracy = ratio(counts['iss_correct'], counts['iss_total'])
    gates = {'iss_accuracy_95': accuracy is not None and accuracy >= .95,
             **{f'{key}_recall_85': value['recall'] is not None and value['recall'] >= .85 for key, value in categories.items()},
             'zero_uncited_hallucinations': counts['hallucinations_without_citation'] == 0,
             'zero_wrong_shown_values': counts['hallucinations_wrong_value'] == 0,
             'complete_comparable_inputs': not exclusions and all(row['approved'] for row in rows) and not any(e.get('code') in ('missing_card', 'missing_fields', 'card_not_ready', 'document_set_mismatch', 'source_page_errors') for e in errors)}
    real_test_ready = mvp_corpus_ready and bool(issues) and all(i.gold.kind == 'real' and i.gold.approved for i in issues)
    return {'label': label, 'status': 'PASS' if all(gates.values()) else 'FAIL', 'gates': gates,
            'mvp_quality_status': 'PASS' if real_test_ready and all(gates.values()) else 'FAIL',
            'mvp_quality_note': 'Требуется размеченный реальный test-корпус; синтетика проверяет только механику.',
            'issue_count': len(issues), 'approved_issues': sum(i.gold.approved for i in issues), 'field_count': all_fields,
            'iss_accuracy': {'correct': counts['iss_correct'], 'total': counts['iss_total'], 'value': accuracy},
            'iss_only_accuracy': {'correct': counts['iss_only_correct'], 'total': counts['iss_only_total'], 'value': ratio(counts['iss_only_correct'], counts['iss_only_total'])},
            'mismatch': {'correct': counts['mismatch_correct'], 'total': counts['mismatch_total']},
            'risk': risk_metrics, 'risk_micro': micro,
            'risk_macro_recall': ratio(sum(v['recall'] for v in risk_metrics.values() if v['recall'] is not None), sum(v['recall'] is not None for v in risk_metrics.values())),
            'risk_macro_precision': ratio(sum(v['precision'] for v in risk_metrics.values() if v['precision'] is not None), sum(v['precision'] is not None for v in risk_metrics.values())),
            'categories': categories, 'hallucinations': {k: counts[k] for k in ('hallucinations', 'hallucinations_without_citation', 'hallucinations_wrong_value')},
            'needs_review': {'count': counts['review'], 'total': counts['scorable'], 'value': ratio(counts['review'], counts['scorable'])},
            'coverage': {'count': counts['covered'], 'total': counts['scorable'], 'value': ratio(counts['covered'], counts['scorable'])},
            'not_found': counts['not_found'], 'all_needs_review': counts['all_review'], 'all_shown': counts['all_shown'],
            'human_edits_ignored': counts['human_edits_ignored'], 'modes': dict(modes), 'models': sorted(models),
            'cost': {'total_usd': None if unknown_cost_issues else str(known_cost), 'known_usd': str(known_cost), 'unknown_issues': unknown_cost_issues},
            'duration': {'total_ms': None if unknown_duration_issues else duration_known, 'known_ms': duration_known, 'unknown_issues': unknown_duration_issues},
            'issues': rows, 'exclusions': exclusions, 'errors': errors}
