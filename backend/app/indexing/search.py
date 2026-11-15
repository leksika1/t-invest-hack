from sqlalchemy import func, select

from app.models import Chunk, Document


def search(session, issue_id, query: str, limit: int = 10, document_ids: list | None = None):
    tsquery = func.plainto_tsquery("russian", query)
    statement = (select(Chunk).join(Document, Document.id == Chunk.document_id)
                 .where(Document.issue_id == issue_id, Document.enabled.is_(True), Document.status == "ready",
                        Chunk.search_vector.op("@@")(tsquery)))
    if document_ids is not None:
        statement = statement.where(Document.id.in_(document_ids))
    return session.scalars(statement.order_by(func.ts_rank_cd(Chunk.search_vector, tsquery).desc(), Chunk.id).limit(min(limit, 100))).all()
