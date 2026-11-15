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
from app.llm.heuristic import DATE, NUMBER, PATTERNS, day, item_for


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
