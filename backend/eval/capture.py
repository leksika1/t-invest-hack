"""Opt-in live capture of two demo issues, using existing ISS/robots-aware adapters.

Creates annotation drafts, never a reviewed gold corpus and never model calls.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

from app.cards.schema import SPECS
from app.cards.verification import project_iss
from app.config import Settings
from app.documents.sources import Identity, MoexIssSource, PrimeSource, SkrinSource
from app.documents.storage import archive_pdfs, validate_pdf

DEMOS = {'RU000A10ATW2': PrimeSource, 'RU000A10CS75': SkrinSource}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def capture(isin, root, max_documents=1):
    root = Path(root).resolve()
    gold_path = root / 'gold' / 'demo' / (isin + '.json')
    if gold_path.exists():
        raise ValueError(f'Черновик уже существует: {gold_path}; выберите новый output-root для нового снимка')
    now = datetime.now(timezone.utc).isoformat()
    target = root / 'data' / 'demo' / isin
    target.mkdir(parents=True, exist_ok=True)
    settings = Settings(files_dir=str(root / '.cache' / isin), _env_file=None)
    errors, documents = [], []
    try:
        resolved = MoexIssSource().resolve(isin)
        snapshot = {'isin': isin, 'fetched_at': now, 'source_url': settings.iss_base_url.rstrip('/') + f'/securities/{isin}.json', **resolved}
    except Exception as exc:
        snapshot = {'isin': isin, 'fetched_at': None, 'fields': {}, 'raw': {}, 'warnings': ['live_iss_failed'], 'error': type(exc).__name__}
        errors.append({'stage': 'iss', 'code': type(exc).__name__})
    dump(target / 'iss.json', snapshot)
    header = snapshot.get('header', {})
    identity = Identity(isin, header.get('inn'), header.get('issuer'), header.get('ogrn'), header.get('registration_number'), header.get('name'))
    source = DEMOS[isin](settings)
    collection = {'captured_at': now, 'source': source.name, 'selected_limit': max_documents,
                  'complete_corpus': False, 'errors': errors}
    try:
        discovered = source.discover(identity)
        collection.update(status=discovered.status, documents_found=len(discovered.documents), message=discovered.message)
        for candidate in sorted(discovered.documents, key=lambda d: d.type != 'decision'):
            if len(documents) >= max_documents:
                break
            try:
                content = source.fetch(candidate)
                files = archive_pdfs(content, settings) if content.startswith((b'PK', b'Rar!')) else [(candidate.title, content)]
                for filename, data in files:
                    if len(documents) >= max_documents:
                        break
                    validate_pdf(data, settings)
                    digest = hashlib.sha256(data).hexdigest()
                    if any(d['sha256'] == digest for d in documents):
                        continue
                    pdf = target / (digest + '.pdf')
                    pdf.write_bytes(data)
                    completed = subprocess.run([sys.executable, '-m', 'app.documents.parse_worker', str(pdf)], capture_output=True,
                                               timeout=settings.document_parse_timeout_seconds, check=True)
                    pages = json.loads(completed.stdout.decode('utf-8'))
                    pages_path = target / (digest + '.pages.json')
                    dump(pages_path, {'pdf_sha256': digest, 'pages': pages})
                    origin = asdict(candidate)
                    origin['fetched_at'] = now
                    documents.append({'document_id': str(uuid.uuid5(uuid.NAMESPACE_URL, digest)), 'filename': filename,
                                      'path': pdf.relative_to(root).as_posix(), 'sha256': digest, 'enabled': True, 'type': candidate.type,
                                      'origins': [origin], 'pages_file': pages_path.relative_to(root).as_posix(),
                                      'pages_sha256': hashlib.sha256(pages_path.read_bytes()).hexdigest()})
            except Exception as exc:
                errors.append({'stage': 'fetch_parse', 'url': candidate.download_url, 'code': type(exc).__name__})
    except Exception as exc:
        collection.update(status='failed', message='Сбор недоступен; требуется ручная загрузка')
        errors.append({'stage': 'discover', 'code': type(exc).__name__})
    collection['documents_downloaded'] = len(documents)
    fields = {}
    for spec in SPECS:
        iss = snapshot.get('fields', {}).get(spec.code) if spec.iss else None
        fields[spec.code] = {'value': project_iss(spec.code, iss) if iss is not None else None, 'iss_value': iss,
                             'not_found': False, 'unscorable': True,
                             'reason': 'TODO: сверить документное значение с ISS' if spec.iss else 'TODO: разметить условие по PDF и проверить полноту комплекта'}
    draft = {'schema_version': '1', 'isin': isin, 'kind': 'real', 'as_of': now[:10], 'issuer_group': header.get('inn') or isin,
             'documents': documents, 'iss_fixture': (target / 'iss.json').relative_to(root).as_posix(), 'fields': fields,
             'review': {'annotators': [], 'resolved': False, 'verified_by': 'codex', 'needs_human_check': True},
             'todo': ['Два разметчика и арбитр; проверить комплект и действующую редакцию.', 'Дополнить/проверить все 15 документных полей.'],
             'collection': collection}
    dump(gold_path, draft)
    return {'isin': isin, 'iss': snapshot.get('fetched_at') is not None, 'documents': len(documents), 'collection_status': collection.get('status'), 'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Явный live capture demo: ISS + штатные коллекторы; без LLM')
    parser.add_argument('--isin', choices=DEMOS, action='append')
    parser.add_argument('--output-root', default='eval')
    parser.add_argument('--max-documents', type=int, choices=range(1, 21), default=1)
    args = parser.parse_args(argv)
    try:
        for isin in args.isin or DEMOS:
            print(json.dumps(capture(isin, args.output_root, args.max_documents), ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError) as exc:
        print(f'Ошибка capture: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
