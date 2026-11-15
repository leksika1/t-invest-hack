import json


class FakeLLM:
    """Deterministic per-field extraction, including an intentional hallucination."""
    def __init__(self, **kwargs):
        pass

    def embed(self, texts):
        return None

    def extract(self, code, messages, output_model, *, batch=False):
        if batch:
            from app.cards.schema import extraction_model
            data = json.loads(messages[1]['content'])
            return {field['field_code']: self.extract(field['field_code'],
                [messages[0], {'role': 'user', 'content': json.dumps({**data, **field})}], extraction_model(field['field_code'])).model_dump(mode='json') for field in data['fields']}
        fragments = json.loads(messages[1]['content'])['fragments']
        parameters = {
            'face_value': ('Номинальная', {'amount': '1000', 'currency': 'RUB'}),
            'covenants': ('EBITDA', {'items': [{'condition': 'Отношение долга к EBITDA не более 3'}]}),
            'issuer_call_option': ('Эмитент вправе', {'conditions': [{'trigger': 'По усмотрению эмитента', 'price_rule': '100 процентов номинала'}]}),
            'offer_procedure': ('депозитарий', {'procedures': [{'submission': 'Предъявление требований через депозитарий', 'window_rule': '5 рабочих дней'}]}),
            'guarantee_security': ('поручительством', {'items': [{'type': 'surety', 'party': 'ООО Пример', 'scope': 'В полном объеме'}]}),
            'cross_default': ('EBITDA', {'items': [{'trigger': 'Неисполнение иных долговых обязательств'}]}),
        }
        if code not in parameters:
            return output_model.model_validate({'not_found': True, 'value': None})
        keyword, value = parameters[code]
        fragment = next((f for f in fragments if keyword.lower() in f['text'].lower()), fragments[0])
        return output_model.model_validate({'not_found': False, 'value': value, 'quote': 'Несуществующая цитата кросс-дефолта' if code == 'cross_default' else fragment['text'],
             'document_id': fragment['document_id'], 'page': fragment['page'], 'clause': fragment['clause'], 'confidence': .99})
