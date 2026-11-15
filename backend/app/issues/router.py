from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cards.schema import CardRequest, FIELDS
from app.config import get_settings
from app.db import get_sessionmaker
from app.documents.sources import Identity, MoexIssSource, UploadSource, assisted
from app.errors import ProcessingError
from app.iss.client import IssError
from app.issues.service import document_json, ensure_issue, new_job, put_document, update_iss
from app.models import Document, Issue, Job, CardField

router = APIRouter(prefix="/api", tags=["issues", "documents", "jobs"])
TYPES = {"decision", "prospectus", "amendment", "material_fact", "other"}


def database():
    with get_sessionmaker().begin() as session:
        yield session


def isin_value(isin):
    try:
        return CardRequest(isin=isin).isin
    except ValueError as exc:
        raise HTTPException(422, detail="Некорректный ISIN") from exc


def issue_required(session, isin):
    issue = session.scalar(select(Issue).where(Issue.isin == isin_value(isin)))
    if issue is None:
        raise HTTPException(404, detail="Выпуск не найден")
    return issue


def last_collect(session, issue):
    return session.scalar(select(Job).where(Job.type == "collect", Job.payload["issue_id"].astext == str(issue.id))
                          .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))


def identity_of(issue):
    return Identity(issue.isin, issue.header.get("inn"), issue.header.get("issuer"), issue.header.get("ogrn"), issue.header.get("registration_number"), issue.header.get("name"))


def document_list(session, issue, limit=20, offset=0):
    job = last_collect(session, issue)
    documents = session.scalars(select(Document).where(Document.issue_id == issue.id)
                               .order_by(Document.created_at.desc(), Document.id.desc()).limit(limit).offset(offset))
    total = session.scalar(select(func.count()).select_from(Document).where(Document.issue_id == issue.id))
    return {"items": [document_json(session, d) for d in documents], "total": total, "limit": limit, "offset": offset,
            "sources": (job.result or {}).get("sources", []) if job else [],
            "assisted": assisted(identity_of(issue)), "warnings": (job.result or {}).get("warnings", []) if job else [],
            "active_collect_job_id": job.id if job and job.status in ("queued", "running") else None}


@router.get("/issues/{isin}")
def get_issue(isin: str, session: Session = Depends(database)):
    code = isin_value(isin)
    issue = session.scalar(select(Issue).where(Issue.isin == code))
    if issue is None:
        try:
            resolved = MoexIssSource().resolve(code)
        except IssError as exc:
            raise HTTPException(404 if "не найден" in str(exc) else 502, detail="ISS недоступен или выпуск не найден") from exc
        issue = ensure_issue(session, code)
        update_iss(issue, resolved)
    state = "stale" if issue.iss_data and issue.iss_error else "available" if issue.iss_data else "unavailable"
    listing = document_list(session, issue)
    return {"isin": issue.isin, "secid": issue.secid, "header": issue.header,
            "iss": {"status": state, "fetched_at": issue.iss_updated_at, **issue.iss_data},
            "normalized": issue.normalized, "documents": listing["items"],
            "warnings": ([issue.iss_error] if issue.iss_error else []) + listing["warnings"]}


@router.post("/issues/{isin}/collect", status_code=202)
def collect_issue(isin: str, session: Session = Depends(database)):
    issue = ensure_issue(session, isin_value(isin))
    session.execute(select(Issue).where(Issue.id == issue.id).with_for_update())
    job = last_collect(session, issue)
    if job is None or job.status not in ("queued", "running"):
        job = new_job(session, "collect", {"issue_id": str(issue.id)})
    return {"isin": issue.isin, "job_id": job.id, "status": job.status}


@router.get("/issues/{isin}/documents")
def documents(isin: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(database)):
    return document_list(session, issue_required(session, isin), limit, offset)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.patch("/issues/{isin}/documents/{document_id}")
def select_document(isin: str, document_id: uuid.UUID, selection: Selection, session: Session = Depends(database)):
    issue = issue_required(session, isin)
    document = session.get(Document, document_id)
    if document is None or document.issue_id != issue.id:
        raise HTTPException(404, detail="Документ не найден")
    document.enabled = selection.enabled
    return document_json(session, document)


@router.post("/issues/{isin}/documents", status_code=202)
def upload_documents(isin: str, files: list[UploadFile] = File(...), document_types: list[str] | None = Form(None), session: Session = Depends(database)):
    code = isin_value(isin)
    settings = get_settings()
    try:
        if not 1 <= len(files) <= settings.max_upload_files:
            raise ProcessingError("too_many_files", "Превышен лимит числа PDF", 413)
        kinds = document_types or ["other"] * len(files)
        if len(kinds) != len(files) or any(t not in TYPES for t in kinds):
            raise ProcessingError("invalid_document_types", "Типы документов должны соответствовать PDF по порядку")
        prepared = []
        # Validate the entire batch before any issue/document/file mutation.
        for upload, kind in zip(files, kinds):
            content = upload.file.read(settings.max_upload_bytes + 1)
            prepared.append(UploadSource.prepare(content, upload.filename or "document.pdf", kind, settings))
        issue = ensure_issue(session, code)
        result = []
        to_parse = []
        for content, filename, kind in prepared:
            document, duplicate = put_document(session, issue.id, content, filename, kind, settings,
                            {"source": "upload", "source_url": None, "download_url": None, "published_at": None, "archive": None})
            if document.status != "ready":
                to_parse.append(str(document.id))
            result.append({**document_json(session, document), "duplicate": duplicate})
        job = new_job(session, "ingest", {"issue_id": str(issue.id), "document_ids": list(dict.fromkeys(to_parse))})
        if not to_parse:
            from datetime import timezone
            job.status, job.step, job.progress = "succeeded", "finalize", 100
            job.finished_at = datetime.now(timezone.utc)
        return {"isin": issue.isin, "documents": result, "job_id": job.id}
    finally:
        for upload in files:
            upload.file.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, session: Session = Depends(database)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail="Задача не найдена")
    processed = None
    if job.type == 'build_card' and job.payload.get('card_id'):
        processed = session.scalar(select(func.count()).select_from(CardField).where(
            CardField.card_id == uuid.UUID(job.payload['card_id']),
            (CardField.reason.is_(None) | (CardField.reason != 'processing'))))
    return {"id": job.id, "type": job.type, "status": job.status, "step": job.step, "progress": job.progress,
            "card_id": job.payload.get('card_id'),
            'processed_fields': processed, 'total_fields': len(FIELDS) if job.type == 'build_card' else None,
            "error": job.error, "result": job.result, "attempts": job.attempts,
            "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}
