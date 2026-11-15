"""Persistent stage-2 entities. Schema changes are owned by Alembic."""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Computed, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from decimal import Decimal
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db import Base


class Entity:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Issue(Entity, Base):
    __tablename__ = "issues"
    isin: Mapped[str] = mapped_column(String(12), unique=True)
    secid: Mapped[str | None] = mapped_column(String(36))
    header: Mapped[dict] = mapped_column(JSONB, default=dict)
    iss_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    normalized: Mapped[dict] = mapped_column(JSONB, default=dict)
    iss_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    iss_error: Mapped[str | None] = mapped_column(Text)


class Document(Entity, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("issue_id", "sha256"),)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issues.id"), index=True)
    type: Mapped[str] = mapped_column(String(32), default="other")
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict | None] = mapped_column(JSONB)


class DocumentOrigin(Entity, Base):
    __tablename__ = "document_origins"
    __table_args__ = (UniqueConstraint("document_id", "origin_key"),)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    origin_key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    download_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archive: Mapped[dict | None] = mapped_column(JSONB)


class Page(Entity, Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "number"),)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)


class Chunk(Entity, Base):
    __tablename__ = "chunks"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    clause: Mapped[str | None] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('russian'::regconfig, text)", persisted=True))
    embedding: Mapped[list | None] = mapped_column(Vector(get_settings().embedding_dim))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        Index("ix_chunks_fts", "search_vector", postgresql_using="gin"),
        Index("ix_chunks_embedding", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class Card(Entity, Base):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("issue_id", "version"),)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issues.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    journal: Mapped[dict] = mapped_column(JSONB, default=dict)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CardField(Entity, Base):
    __tablename__ = "card_fields"
    __table_args__ = (UniqueConstraint("card_id", "code"),)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict | None] = mapped_column(JSONB)
    quote: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    page: Mapped[int | None] = mapped_column(Integer)
    clause: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24))
    iss_value: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    user_status: Mapped[str | None] = mapped_column(String(24))
    user_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSONB, default=dict)
    not_found: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Entity, Base):
    __tablename__ = "jobs"
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    step: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMCall(Entity, Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index('ix_llm_calls_created_at', 'created_at'), Index('ix_llm_calls_job_id', 'job_id'))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    field_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("card_fields.id"))
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[str | None] = mapped_column(String(40))
    cost_source: Mapped[str | None] = mapped_column(String(24))
    usage: Mapped[dict | None] = mapped_column(JSONB)
    provider_response_id: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    state: Mapped[str] = mapped_column(String(24), default="reserved")
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), default=Decimal("0"))
    attempt: Mapped[int] = mapped_column(Integer, default=1)


class RateLimitEvent(Entity, Base):
    __tablename__ = 'rate_limit_events'
    key: Mapped[str] = mapped_column(String(64), index=True)
