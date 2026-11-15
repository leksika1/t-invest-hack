"""One context per group; validate/repair each field independently."""
import json

from pydantic import ValidationError, create_model

from app.cards import prompts
from app.cards.schema import extraction_model
from app.llm.gateway import GatewayError


def extract_batch(gateway, specs, context, field_ids=None):
    models = {spec.code: extraction_model(spec.code) for spec in specs}
    schema_model = create_model('FieldsBatch', **{code: (model, ...) for code, model in models.items()})

    class Envelope:
        @staticmethod
        def model_json_schema():
            return schema_model.model_json_schema()

        @staticmethod
        def model_validate_json(content):
            # Do not discard seven valid fields because the eighth is invalid.
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError('Batch must be an object')
            return value

    definitions = [{key: value for key, value in json.loads(prompts.messages(spec, [])[1]['content']).items()
                    if key != 'fragments'} for spec in specs]
    messages = [{'role': 'system', 'content': prompts.SYSTEM + '\nОтвет — объект с ключами кодов полей; для каждого ключа отдельная структура извлечения.'},
                {'role': 'user', 'content': json.dumps({'fragments': context, 'fields': definitions}, ensure_ascii=False)}]
    try:
        raw = gateway.extract('iss_batch' if specs[0].iss else 'risk_batch', messages, Envelope, batch=True)
    except GatewayError as exc:
        # Quota, budget, auth and transport failures must not fan out into 7/8 calls.
        if exc.code not in ('invalid_json', 'llm_schema_rejected', 'llm_request_rejected'):
            return {spec.code: exc for spec in specs}
        raw = {}
    results, invalid = {}, []
    for spec in specs:
        try:
            results[spec.code] = models[spec.code].model_validate(raw.get(spec.code))
        except (ValidationError, ValueError, TypeError):
            invalid.append(spec)
    for index, spec in enumerate(invalid):
        original_field_id = getattr(gateway, "field_id", None)
        if field_ids:
            gateway.field_id = field_ids.get(spec.code)
        try:
            results[spec.code] = gateway.extract(spec.code, prompts.messages(spec, context), models[spec.code])
        except GatewayError as exc:
            results[spec.code] = exc
            if exc.code in ('budget_exceeded', 'llm_rate_limited', 'llm_auth_error', 'llm_prices_not_configured'):
                for remaining in invalid[index + 1:]:
                    results[remaining.code] = exc
                break
        finally:
            if field_ids:
                gateway.field_id = original_field_id
    return results
