from collections import defaultdict
import json
import re

from sqlalchemy import func, select

from app.models import Chunk, Document, Page


def context_token_bound(records):
    """Conservative UTF-8 bytes, including labels/JSON, not a tokenizer estimate."""
    return len(json.dumps(records, ensure_ascii=False).encode('utf-8'))


def full_document_context(documents, pages, limit):
    """All frozen pages, never truncated; None asks the caller to use retrieval."""
    from app.documents.parser import chunks
    if limit <= 0:
        return None
    selected = {str(doc['id']) for doc in documents}
    if sum(len(text.encode('utf-8')) for (identifier, _), text in pages.items() if identifier in selected) > limit:
        return None
    records = []
    for document in documents:
        identifier = str(document['id'])
        numbered = sorted((number, text) for (doc_id, number), text in pages.items() if doc_id == identifier)
        if [number for number, _ in numbered] != list(range(1, document['pages'] + 1)) or any(not text.strip() for _, text in numbered):
            return None
        previous_clause = None
        filename = document['filename'].replace('\n', ' ').replace('\r', ' ')
        for number, text in numbered:
            # One record per physical page avoids repeating metadata per clause.
            # Clause numbers stay verbatim in text; verification scopes quotes to them.
            records.append({'document_id': identifier, 'page': number,
                            'clause': None, 'previous_clause': previous_clause, 'full_page': True,
                            'text': text, 'label': f'[документ {identifier}: {filename}, стр. {number}]'})
            _, previous_clause = chunks(text, previous_clause, size=len(text) + 1, overlap=0)
    return records if context_token_bound(records) <= limit else None


def retrieval_weight(text, field_code=None):
    """Down-rank form instructions without deleting original text or evidence."""
    template = re.compile(r'(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:Указыва(?:ется|ются|ть)\b|Для облигаций (?:с ипотечным покрытием|без срока погашения))', re.I)
    generic = re.search(r'В случае если эмитент.{0,180}(?:указыва|должн[а-я ]*быть указан)', text, re.I | re.S)
    weight = .15 if template.search(text) or generic else 1.0
    if field_code == 'offer_procedure' and re.search(r'предварительн[а-я]* договор|потенциальн[а-я ]*приобретател|книг[а-я ]*заявок', text, re.I):
        weight *= .2
    if field_code == 'issuer_restrictions' and re.search(r'владел(?:ец|ьц[а-я]*).{0,50}(?:не вправе|не имеет права|ограничени)', text, re.I):
        weight *= .2
    return weight


def bound_context(records, top_k, max_tokens):
    """Byte bound avoids underestimating Russian/legal token counts."""
    output, remaining = [], max_tokens
    for record in records[:top_k]:
        metadata = {k: record[k] for k in ('document_id', 'page', 'clause')}
        overhead = len(json.dumps(metadata, ensure_ascii=False).encode('utf-8')) + 64
        if remaining <= overhead:
            break
        text = record['text'].encode('utf-8')[:remaining - overhead].decode('utf-8', 'ignore')
        if text.strip():
            output.append({**metadata, 'text': text})
            remaining -= overhead + len(text.encode('utf-8'))
    return output


def retrieve(factory, issue_id, document_ids, query, settings, gateway, field_code=None):
    if not document_ids:
        return [], []
    warnings = []
    with factory() as session:
        # The snapshot IDs freeze the enabled selection for this job. Later toggles
        # affect the next card only; don't re-read Document.enabled here.
        base = (select(Chunk).join(Document, Document.id == Chunk.document_id)
                .where(Document.issue_id == issue_id, Document.id.in_(document_ids), Document.status == 'ready'))
        # OR hints: a long AND of synonyms otherwise returns no fragments at all.
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
                                          .order_by(Chunk.document_id, Page.number, Chunk.clause, Chunk.id).limit(settings.retrieval_top_k * 3)), 1):
                scores[chunk.id] = 1 / (60 + rank)
                candidates[chunk.id] = chunk
        records = []
        for key in scores:
            scores[key] *= retrieval_weight(candidates[key].text, field_code)
        for key in sorted(candidates, key=lambda key: (-scores[key], str(key))):
            chunk = candidates[key]
            page = session.get(Page, chunk.page_id)
            records.append({'document_id': str(chunk.document_id), 'page': page.number, 'clause': chunk.clause, 'text': chunk.text})
        return bound_context(records, settings.retrieval_top_k, settings.retrieval_max_tokens), warnings
