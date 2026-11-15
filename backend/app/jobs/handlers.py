from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
import uuid
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, select, text, update

from app.config import get_settings
from app.documents.parser import chunks
from app.documents.sources import EDisclosureSource, Identity, MoexIssSource, PrimeSource, SkrinSource, assisted
from app.documents.storage import archive_pdfs
from app.errors import ProcessingError
from app.iss.client import IssError
from app.issues.service import put_document, update_iss
from app.llm.embeddings import DisabledEmbeddingGateway
from app.models import Chunk, Document, Issue, Job, Page

_owner = ContextVar("job_owner", default=None)


def parse_document(factory, document_id, settings, gateway=None):
    # Serialize retries/duplicate uploads of one PDF across backend processes.
    # The job claim transaction is already committed; only this document is fenced.
    with factory.begin() as lock_session:
        lock_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": document_id.int % (2**63 - 1)})
        return _parse_document(factory, document_id, settings, gateway)


def _parse_document(factory, document_id, settings, gateway=None):
    with factory.begin() as session:
        document = session.get(Document, document_id)
        if document.status == "ready":
            return
        path = document.path
        document.status, document.error = "processing", None
    try:
        completed = subprocess.run([sys.executable, "-m", "app.documents.parse_worker", path], capture_output=True,
                                   timeout=settings.document_parse_timeout_seconds, check=True)
        pages = json.loads(completed.stdout.decode("utf-8"))
        with factory.begin() as session:
            session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            session.execute(delete(Page).where(Page.document_id == document_id))
            clause, ocr_pages, errors, embedded = None, 0, [], []
            for data in pages:
                page = Page(document_id=document_id, **data)
                session.add(page)
                session.flush()
                ocr_pages += int(data["ocr"])
                if data["error"]:
                    errors.append({"page": data["number"], "code": data["error"]})
                fragments, clause = chunks(data["text"], clause)
                for fragment in fragments:
                    chunk = Chunk(document_id=document_id, page_id=page.id, **fragment)
                    session.add(chunk)
                    embedded.append(chunk)
            warning = None
            try:
                vectors = (gateway or DisabledEmbeddingGateway()).embed([c.text for c in embedded])
                if vectors is not None:
                    import math
                    if len(vectors) != len(embedded) or any(len(v) != settings.embedding_dim or not all(math.isfinite(x) for x in v) for v in vectors):
                        raise ValueError("Invalid embedding batch")
                    for chunk, vector in zip(embedded, vectors):
                        chunk.embedding, chunk.embedding_model = vector, settings.embedding_model
                elif settings.embedding_model:
                    warning = "embeddings_disabled_until_budget_gateway"
            except Exception:
                warning = "embeddings_failed_fts_only"
            document = session.get(Document, document_id)
            document.pages, document.ocr_pages = len(pages), ocr_pages
            document.status = "failed" if errors else "ready"
            document.error = {"code": "page_errors", "message": "Есть страницы без распознанного текста", "pages": errors} if errors else None
            return {"warning": warning, "errors": errors}
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        with factory.begin() as session:
            document = session.get(Document, document_id)
            document.status, document.error = "failed", {"code": "parse_failed", "message": "Разбор PDF завершился ошибкой или превысил таймаут"}
        raise ProcessingError("parse_failed", "Разбор PDF завершился ошибкой или превысил таймаут") from exc


def progress(factory, job_id, step, percent, result=None):
    with factory.begin() as session:
        statement = update(Job).where(Job.id == job_id)
        if _owner.get():
            statement = statement.where(Job.worker_id == _owner.get(), Job.status == "running")
        values = {"step": step, "progress": percent}
        if result is not None:
            values["result"] = result
        session.execute(statement.values(**values))


def ingest(factory, job_id, payload, settings, start=0, span=95):
    outcomes = []
    for index, identifier in enumerate(payload["document_ids"]):
        try:
            result = parse_document(factory, uuid.UUID(identifier), settings)
            outcomes.append({"document_id": identifier, **(result or {})})
        except ProcessingError as exc:
            outcomes.append({"document_id": identifier, "error": exc.as_dict()})
        progress(factory, job_id, "parse", start + int(span * (index + 1) / len(payload["document_ids"])))
    return {"document_ids": payload["document_ids"], "documents": outcomes}


def collect(factory, job_id, payload, settings):
    with factory() as session:
        issue = session.get(Issue, uuid.UUID(payload["issue_id"]))
        identity = Identity(issue.isin, issue.header.get("inn"), issue.header.get("issuer"), issue.header.get("ogrn"), issue.header.get("registration_number"), issue.header.get("name"))
    warnings, results, identifiers = [], [], []
    progress(factory, job_id, "resolve_iss", 2)
    try:
        resolved = MoexIssSource().resolve(identity.isin)
        identity = Identity(identity.isin, resolved["header"].get("inn"), resolved["header"].get("issuer"), resolved["header"].get("ogrn"), resolved["header"].get("registration_number"), resolved["header"].get("name"))
        with factory.begin() as session:
            update_iss(session.get(Issue, uuid.UUID(payload["issue_id"])), resolved)
    except IssError as exc:
        warnings.append("iss_unavailable")
        with factory.begin() as session:
            session.get(Issue, uuid.UUID(payload["issue_id"])).iss_error = str(exc)
    for index, source in enumerate([PrimeSource(settings), SkrinSource(settings), EDisclosureSource(settings)]):
        name = getattr(source, "name", "edisclosure")
        progress(factory, job_id, name, 10 + index * 20)
        source_errors = []
        try:
            if not identity.inn and not identity.name:
                result = {"source": name, "status": "empty", "documents_found": 0, "message": "Нет ИНН/названия из ISS; доступна загрузка PDF"}
            else:
                discovered = source.discover(identity)
                for candidate in discovered.documents:
                    try:
                        content = source.fetch(candidate)
                        filename = unquote(urlsplit(candidate.download_url).path.rsplit("/", 1)[-1]) or "document.pdf"
                        archive = None
                        if content.startswith((b"PK", b"Rar!")):
                            archive = {"url": candidate.download_url, "sha256": hashlib.sha256(content).hexdigest()}
                            files = archive_pdfs(content, settings)
                        else:
                            files = [(filename, content)]
                        for filename, data in files:
                            origin = {"source": candidate.source, "source_url": candidate.source_url, "download_url": candidate.download_url,
                                      "published_at": candidate.published_at, "archive": {**archive, "entry_name": filename} if archive else None}
                            with factory.begin() as session:
                                document, _ = put_document(session, uuid.UUID(payload["issue_id"]), data, filename, candidate.type, settings, origin)
                                identifiers.append(str(document.id))
                    except ProcessingError as exc:
                        source_errors.append({"url": candidate.download_url, **exc.as_dict()})
                result = {"source": discovered.source, "status": discovered.status, "documents_found": len(discovered.documents), "message": discovered.message}
                if discovered.documents and len(source_errors) == len(discovered.documents):
                    result["status"] = "failed"
        except ProcessingError as exc:
            blocked = exc.code in ("robots_denied", "robots_unavailable", "source_blocked")
            state = "blocked" if blocked else "empty" if exc.code == "source_not_found" else "failed"
            result = {"source": name, "status": state, "documents_found": 0, "message": exc.message}
        except Exception:
            result = {"source": name, "status": "failed", "documents_found": 0, "message": "Ошибка адаптера источника; остальные источники будут опрошены"}
        result.update({"checked_at": datetime.now(timezone.utc).isoformat(), "errors": source_errors})
        results.append(result)
        progress(factory, job_id, name, 25 + index * 20, {"sources": results, "assisted": assisted(identity), "warnings": warnings})
    identifiers = list(dict.fromkeys(identifiers))
    parsed = ingest(factory, job_id, {"document_ids": identifiers}, settings, start=70, span=25)
    return {**parsed, "sources": results, "assisted": assisted(identity), "warnings": warnings}


def handle(factory, job):
    settings = get_settings()
    token = _owner.set(job.worker_id)
    try:
        if job.type == "ingest":
            return ingest(factory, job.id, job.payload, settings)
        if job.type == "collect":
            return collect(factory, job.id, job.payload, settings)
        raise ProcessingError("unknown_job_type", "Неизвестный тип фоновой задачи")
    finally:
        _owner.reset(token)
