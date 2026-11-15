"""python -m eval.run: read-only evaluation of saved/API cards and optional baseline."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx

from eval.baseline import build_card
from eval.corpus import asset, load_corpus, read_json
from eval.inputs import from_api, from_json, validate_cards
from eval.metrics import score_system
from eval.report import save_report
from eval.schema import GoldIssue


def mvp_ready(issues, manifest, root, split):
    if not manifest.frozen or split != 'test' or not 30 <= len(manifest.issues) <= 50:
        return False
    selected = {i.gold.isin for i in issues}
    expected = {e.isin for e in manifest.issues if e.split == 'test'}
    if selected != expected or not expected or not manifest.settings:
        return False
    for entry in manifest.issues:
        gold = GoldIssue.model_validate(read_json(asset(root, entry.gold)))
        if gold.kind != 'real' or not gold.approved:
            return False
    return True


def evaluate(issues, manifest, cards, root, manifest_path, split=None, baseline_cards=None, label='LLM / карточки конвейера', selections=None):
    eligible = mvp_ready(issues, manifest, root, split)
    systems = {'pipeline': score_system(issues, cards, label, eligible)}
    if baseline_cards is not None:
        systems['baseline'] = score_system(issues, baseline_cards, 'Baseline regex-v1', eligible)
    entries = {entry.isin: entry for entry in manifest.issues}
    return {'schema_version': '1', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'status': systems['pipeline']['status'], 'systems': systems,
            'corpus': {'name': manifest.name, 'seed': manifest.seed, 'frozen': manifest.frozen, 'split': split or 'selected/mixed',
                       'selected_count': len(issues), 'manifest_count': len(manifest.issues), 'kinds': dict(Counter(i.gold.kind for i in issues)),
                       'settings': manifest.settings, 'manifest_sha256': hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
                       'issues': [{'isin': i.gold.isin, 'kind': i.gold.kind, 'split': entries[i.gold.isin].split,
                                   'issuer_group': i.gold.issuer_group, 'gold_sha256': hashlib.sha256(i.gold_path.read_bytes()).hexdigest(),
                                   'iss_sha256': hashlib.sha256(asset(root, i.gold.iss_fixture).read_bytes()).hexdigest(),
                                   'as_of': str(i.gold.as_of), 'documents': [d.model_dump(mode='json') for d in i.gold.documents],
                                   'ocr_pages': i.ocr_pages, 'page_errors': i.page_errors,
                                   'review': i.gold.review.model_dump(), 'collection': i.gold.collection} for i in issues]},
            'selections': selections or [{'isin': isin, 'card_id': card.get('id'), 'version': card.get('version'), 'selection': 'json'} for isin, card in cards.items()],
            'unmeasured': {'collector_precision_recall': None, 'ocr_accuracy': None, 'retrieval_recall': None},
            'mvp_corpus_ready': eligible}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Оценка карточек: только чтение, без запуска LLM/job')
    parser.add_argument('--gold', required=True)
    parser.add_argument('--manifest', default='eval/manifest.json')
    parser.add_argument('--assets-root', default='eval')
    parser.add_argument('--split', choices=['dev', 'test'])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--cards', help='Массив полных GET /api/cards/{id} JSON, одна версия на ISIN')
    source.add_argument('--base-url', help='Origin стенда, например https://dev.tinvest.leksika1.ru')
    parser.add_argument('--save-cards', help='Сохранить ответы API для повторного offline прогона')
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument('--baseline-cards')
    baseline.add_argument('--with-baseline', action='store_true')
    parser.add_argument('--label', default='LLM / карточки конвейера')
    parser.add_argument('--output', default='eval/artifacts/evaluation.md')
    parser.add_argument('--json-output')
    args = parser.parse_args(argv)
    try:
        issues, manifest = load_corpus(args.gold, args.assets_root, args.manifest, args.split)
        selections = None
        if args.base_url:
            cards, selections = from_api(args.base_url, issues, manifest)
        else:
            cards = from_json(args.cards)
        if args.save_cards:
            target = Path(args.save_cards); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(list(cards.values()), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        baseline_cards = from_json(args.baseline_cards) if args.baseline_cards else validate_cards([build_card(i) for i in issues]) if args.with_baseline else None
        report = evaluate(issues, manifest, cards, args.assets_root, args.manifest, args.split, baseline_cards, args.label, selections)
        save_report(report, args.output, args.json_output)
        print(f"{report['status']}: {len(issues)} выпусков; отчёт {args.output}; качество MVP {report['systems']['pipeline']['mvp_quality_status']}")
        return 0 if report['status'] == 'PASS' else 1
    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError) as exc:
        print(f'Ошибка входных данных/сети: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
