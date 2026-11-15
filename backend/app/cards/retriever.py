from collections import defaultdict

from sqlalchemy import func, select

from app.models import Chunk, Document, Page


def bound_context(records, top_k, max_tokens):
    """Byte bound avoids underestimating Russian/legal token counts."""
    output, remaining = [], max_tokens
    for record in records[:top_k]:
        metadata = {k: record[k] for k in ('document_id', 'page', 'clause')}
        import json
        overhead = len(json.dumps(metadata, ensure_ascii=False).encode('utf-8')) + 64
        if remaining <= overhead:
            break
        text = record['text'].encode('utf-8')[:remaining - overhead].decode('utf-8', 'ignore')
        if text.strip():
            output.append({**metadata, 'text': text})
            remaining -= overhead + len(text.encode('utf-8'))
    return output


def retrieve(factory, issue_id, document_ids, query, settings, gateway):
    if not document_ids:
        return [], []
    warnings = []
    with factory() as session:
        # The snapshot IDs freeze the enabled selection for this job. Later toggles
        # affect the next card only; don't re-read Document.enabled here.
        base = (select(Chunk).join(Document, Document.id == Chunk.document_id)
                .where(Document.issue_id == issue_id, Document.id.in_(document_ids), Document.status == 'ready'))
        # OR hints: a long AND of synonyms otherwise returns no fragments at all.
        import re
        terms = re.findall(r'\w+', query)
        tsquery = func.to_tsquery('russian', ' | '.join(terms))
        fts = session.scalars(base.where(Chunk.search_vector.op('@@')(tsquery))
                              .order_by(func.ts_rank_cd(Chunk.search_vector, tsquery).desc(), Chunk.id).limit(settings.retrieval_top_k * 3)).all()
        rankings = [fts]
        if settings.embedding_model:
            try:
                vectors = gateway.embed([query])
                if vectors:
                    vector_rows = session.scalars(base.where(Chunk.embedding.is_not(None), Chunk.embedding_model == settings.embedding_model)
                                                   .order_by(Chunk.embedding.cosine_distance(vectors[0]), Chunk.id).limit(settings.retrieval_top_k * 3)).all()
                    rankings.append(vector_rows)
            except Exception:
                warnings.append('query_embeddings_failed_fts_only')
        scores, candidates = defaultdict(float), {}
        for ranking in rankings:
            for rank, chunk in enumerate(ranking, 1):
                scores[chunk.id] += 1 / (60 + rank)  # reciprocal rank fusion
                candidates[chunk.id] = chunk
        # Document order fallback also allows exact dates/tables without keywords.
        if not candidates:
            for rank, chunk in enumerate(session.scalars(base.join(Page, Page.id == Chunk.page_id)
                                          .order_by(Chunk.document_id, Page.number, Chunk.clause, Chunk.id).limit(settings.retrieval_top_k)), 1):
                scores[chunk.id] = 1 / (60 + rank)
                candidates[chunk.id] = chunk
        records = []
        for key in sorted(candidates, key=lambda key: (-scores[key], str(key))):
            chunk = candidates[key]
            page = session.get(Page, chunk.page_id)
            records.append({'document_id': str(chunk.document_id), 'page': page.number, 'clause': chunk.clause, 'text': chunk.text})
        return bound_context(records, settings.retrieval_top_k, settings.retrieval_max_tokens), warnings
