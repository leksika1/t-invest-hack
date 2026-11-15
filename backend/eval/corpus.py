"""Validate frozen bytes and page evidence before scoring any predictions."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import pymupdf

from app.cards.verification import compare_iss, normalized_text, project_iss, quote_matches
from eval.matching import normal
from eval.schema import FrozenPages, GoldIssue, Manifest


def read_json(path):
    def duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Повторный ключ JSON: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Неконечное число JSON: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=duplicate, parse_constant=invalid)


def asset(root, name):
    path = (Path(root) / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(Path(root).resolve()):
        raise ValueError(f'Путь выходит за каталог корпуса: {name}')
    if not path.is_file():
        raise ValueError(f'Нет файла корпуса: {name}')
    return path


@dataclass
class LoadedIssue:
    gold: GoldIssue
    pages: dict[tuple[str, int], str]
    snapshot: dict
    gold_path: Path
    ocr_pages: int = 0
    page_errors: list[dict] = field(default_factory=list)


def load_issue(path, root):
    gold = GoldIssue.model_validate(read_json(path))
    snapshot = read_json(asset(root, gold.iss_fixture))
    if not isinstance(snapshot, dict) or snapshot.get('isin') != gold.isin or not isinstance(snapshot.get('fields'), dict):
        raise ValueError(f'{gold.isin}: ISS snapshot требует isin и fields')
    for code, field in gold.fields.items():
        from app.cards.schema import FIELDS
        if FIELDS[code].iss and normal(project_iss(code, field.iss_value)) != normal(project_iss(code, snapshot['fields'].get(code))):
            raise ValueError(f'{gold.isin}/{code}: ISS в gold не совпадает с зафиксированным snapshot')
        if FIELDS[code].iss and not field.unscorable and not field.not_found:
            expected_status = compare_iss(code, field.value, field.iss_value) if field.iss_value is not None else 'extracted'
            if field.expected_status != expected_status:
                raise ValueError(f'{gold.isin}/{code}: ожидаемый статус противоречит сверке ISS')
    pages, page_errors, ocr_pages = {}, [], 0
    for document in gold.documents:
        pdf = asset(root, document.path)
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != document.sha256:
            raise ValueError(f'{gold.isin}: sha256 PDF не совпал: {document.filename}')
        sidecar = None
        if document.pages_file:
            sidecar_path = asset(root, document.pages_file)
            if not document.pages_sha256 or hashlib.sha256(sidecar_path.read_bytes()).hexdigest() != document.pages_sha256:
                raise ValueError('sha256 страниц не совпал')
            sidecar = FrozenPages.model_validate(read_json(sidecar_path)).model_dump()
            if sidecar.get('pdf_sha256') != document.sha256:
                raise ValueError('OCR/страницы относятся к другому PDF')
        try:
            opened = pymupdf.open(pdf)
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f'Не читается PDF: {document.filename}') from exc
        with opened:
            if opened.needs_pass or not 1 <= len(opened) <= 1000:
                raise ValueError('Непригодный PDF корпуса')
            annotations = {p['number']: p for p in sidecar['pages']} if sidecar else {}
            if sidecar and (len(annotations) != len(opened) or set(annotations) != set(range(1, len(opened) + 1))):
                raise ValueError('Неполный/повторный список страниц')
            for number, page in enumerate(opened, 1):
                text = page.get_text('text', sort=True).strip()
                annotated = annotations.get(number)
                if text and annotated and normalized_text(text) != normalized_text(annotated['text']):
                    raise ValueError('Текстовый слой PDF не совпал с pages sidecar')
                if not text:
                    if document.enabled:
                        ocr_pages += 1
                    if not annotated or not annotated.get('ocr') or annotated.get('error'):
                        text = ''
                        if document.enabled:
                            page_errors.append({'document_id': document.document_id, 'page': number, 'error': annotated.get('error') if annotated else 'ocr_missing'})
                    else:
                        text = annotated['text']  # Frozen OCR; human annotation remains mandatory.
                pages[(document.document_id, number)] = text
    for code, field in gold.fields.items():
        if not field.quote:
            continue
        citations = [dict(quote=field.quote, document_id=field.document_id, page=field.page, clause=field.clause),
                     *(e.model_dump() for e in field.evidence)]
        for citation in citations:
            document = next(d for d in gold.documents if d.document_id == citation['document_id'])
            if not document.enabled or not quote_matches(citation['quote'], pages.get((citation['document_id'], citation['page']), ''), 0.95):
                raise ValueError(f'{gold.isin}/{code}: эталонная цитата не подтверждена')
        for key, items in (field.value or {}).items():
            if isinstance(items, list) and len(items) > 1:
                if any(not any(e.value_path == f'/{key}/{i}' for e in field.evidence) for i in range(1, len(items))):
                    raise ValueError(f'{code}: неполное покрытие массива цитатами')
    return LoadedIssue(gold, pages, snapshot, Path(path), ocr_pages, page_errors)


def load_corpus(gold_path, root, manifest_path, split=None):
    root = Path(root).resolve()
    manifest = Manifest.model_validate(read_json(manifest_path))
    location = Path(gold_path).resolve()
    candidates = sorted(location.rglob('*.json')) if location.is_dir() else [location]
    entries = {asset(root, e.gold): e for e in manifest.issues}
    if not candidates:
        raise ValueError('Каталог gold пуст')
    if any(path not in entries for path in candidates):
        raise ValueError('Gold отсутствует в manifest')
    loaded = []
    for path in candidates:
        entry = entries[path]
        if split and entry.split != split:
            continue
        issue = load_issue(path, root)
        if issue.gold.isin != entry.isin or issue.gold.issuer_group != entry.issuer_group:
            raise ValueError('ISIN/эмитент gold не совпал с manifest')
        loaded.append(issue)
    if not loaded:
        raise ValueError('Нет выпусков выбранной части корпуса')
    return loaded, manifest
