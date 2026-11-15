"""Offline examples and conservative regex extraction through the gateway protocol."""
from copy import deepcopy
import json
from pathlib import Path
import re

from app.cards.schema import extraction_model
from app.cards.verification import normalized_text
from app.documents.parser import chunks
from app.llm.heuristic import PATTERNS, item_for

DEMO_NOTICE = 'Демо-режим: ответы модели имитированы; для полноценной работы нужен API-ключ LLM'
DEMO_NOT_FOUND = 'Демо-режим: эвристика не нашла условие; полноценный разбор требует LLM'
ARRAYS = {'covenants': 'items', 'holder_early_redemption': 'grounds', 'issuer_call_option': 'conditions',
          'guarantee_security': 'items', 'cross_default': 'items', 'issuer_restrictions': 'items',
          'offer_procedure': 'procedures', 'coupon_rate': 'periods', 'coupon_schedule': 'payments',
          'offers': 'events', 'amortization': 'events'}


def heuristic(code, context):
    found, records = [], []
    for page in context:
        fragments, _ = chunks(page['text'], page.get('previous_clause'))
        for fragment in fragments:
            text = fragment['text']
            lower = normalized_text(text)
            # These subjects/procedures are not the requested issuer/put conditions.
            if code == 'offer_procedure' and re.search(r'размещен|предварительн|потенциальн|книг[аи] заявок', lower):
                continue
            if code == 'issuer_restrictions' and not re.search(r'эмитенту запрещ|эмитент не вправе|эмитент обязуется', lower):
                continue
            if re.search(r'указывается|указываются|для облигаций с ипотечным', lower):
                continue
            if not re.search(PATTERNS[code], lower):
                continue
            candidate = item_for(code, text)
            if candidate is None:
                continue
            for item in candidate if code == 'coupon_schedule' else [candidate]:
                if item not in found and len(found) < 10:
                    found.append(item)
                    records.append({**page, **fragment})
    if not found:
        return {'not_found': True, 'value': None}
    array = ARRAYS.get(code)
    value = {array: found} if array else found[0]
    primary = records[0]
    evidence = [{k: row[k] for k in ('document_id', 'page', 'clause')} |
                {'quote': row['text'], 'value_path': f'/{array}/{i}'} for i, row in enumerate(records[1:], 1)] if array else []
    return {'value': value, 'quote': primary['text'], 'document_id': primary['document_id'], 'page': primary['page'],
            'clause': primary['clause'], 'confidence': 1, 'not_found': False, 'ambiguous': not array and len(found) > 1,
            'evidence': evidence}


class DemoGateway:
    configured = True

    def __init__(self, *, isin=None, documents=(), **kwargs):
        self.field_id = None
        self.engine = 'demo_heuristic'
        self.recorded = None
        path = Path(__file__).with_name('demo_responses') / f'{isin}.json' if isin in ('RU000A10CS75', 'RU000A10ATW2') else None
        if path and path.exists():
            recorded = json.loads(path.read_text(encoding='utf-8'))
            actual = {doc.get('sha256'): str(doc['id']) for doc in documents}
            if set(actual) == {doc['sha256'] for doc in recorded['documents']}:
                self.recorded = deepcopy(recorded['fields'])
                mapping = {doc['document_id']: actual[doc['sha256']] for doc in recorded['documents']}
                for field in self.recorded.values():
                    for evidence in [field, *(field.get('evidence') or [])]:
                        if evidence.get('document_id'):
                            evidence['document_id'] = mapping[evidence['document_id']]
                self.engine = 'demo_recorded'

    def embed(self, texts):
        return None

    def extract(self, code, messages, output_model, *, batch=False):
        payload = json.loads(messages[1]['content'])
        codes = [field['field_code'] for field in payload['fields']] if batch else [code]
        result = {key: deepcopy(self.recorded[key]) if self.recorded else heuristic(key, payload['fragments']) for key in codes}
        if not self.recorded:
            for key, value in result.items():
                try:
                    extraction_model(key).model_validate(value)
                except ValueError:
                    result[key] = {'not_found': True, 'value': None}
        if batch:
            return result
        return output_model.model_validate(result[code])


def make_gateway(*, settings, **kwargs):
    if settings.llm_mode == 'demo':
        return DemoGateway(settings=settings, **kwargs)
    from app.llm.gateway import LLMGateway
    kwargs.pop('isin', None)
    kwargs.pop('documents', None)
    return LLMGateway(settings=settings, **kwargs)

