"""Budget reservation -> one transport attempt -> durable outcome (UTC)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import time
import uuid

import httpx
from pydantic import ValidationError
from sqlalchemy import Numeric, cast, func, select, text

from app.config import get_settings
from app.errors import ProcessingError
from app.llm.client import LLMClient
from app.llm.diagnostics import http_diagnostics, validation_summary
from app.models import LLMCall


class GatewayError(ProcessingError):
    pass


def utc_bounds(day=None):
    start = datetime.combine(day or datetime.now(timezone.utc).date(), datetime.min.time(), timezone.utc)
    return start, start + timedelta(days=1)


def finite_decimal(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def usage_cost(usage, input_price, output_price):
    cost = finite_decimal(usage.get('cost'))
    if cost is not None:
        return cost, 'provider'
    incoming = usage.get('prompt_tokens', usage.get('input_tokens'))
    outgoing = usage.get('completion_tokens', usage.get('output_tokens', 0))
    if isinstance(incoming, int) and not isinstance(incoming, bool) and incoming >= 0 and isinstance(outgoing, int) and not isinstance(outgoing, bool) and outgoing >= 0 and input_price is not None and output_price is not None:
        return (Decimal(incoming) * input_price + Decimal(outgoing) * output_price) / 1_000_000, 'config'
    return None, 'unknown'


def sanitized_usage(usage):
    allowed = {'prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens', 'total_tokens', 'cost',
               'cost_details', 'prompt_tokens_details', 'completion_tokens_details', 'input_tokens_details', 'output_tokens_details', 'is_byok'}
    def clean(value):
        if value is None or isinstance(value, (int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            return value if finite_decimal(value) is not None else None
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        return None
    return {key: clean(value) for key, value in usage.items() if key in allowed}


class BudgetLedger:
    def __init__(self, factory):
        self.factory = factory

    def reserve(self, amount, settings, purpose, model, job_id, field_id, attempt):
        start, end = utc_bounds()
        with self.factory.begin() as session:
            # All generation/embedding reservations share a single UTC-day lock.
            session.execute(text('SELECT pg_advisory_xact_lock(:namespace, :day)'),
                            {'namespace': 841032, 'day': start.date().toordinal()})
            calls = session.scalars(select(LLMCall).where(LLMCall.created_at >= start, LLMCall.created_at < end)).all()
            spent = sum((finite_decimal(c.cost_usd) or Decimal(0)) for c in calls)
            held = sum((c.reserved_usd or Decimal(0)) for c in calls if c.cost_usd is None)
            if settings.llm_daily_budget_usd <= 0 or spent + held + amount > settings.llm_daily_budget_usd:
                raise GatewayError('budget_exceeded', 'Дневной бюджет LLM исчерпан')
            entry = LLMCall(purpose=purpose, model=model, job_id=job_id, field_id=field_id, attempt=attempt,
                            reserved_usd=amount, state='reserved', duration_ms=0)
            session.add(entry)
            session.flush()
            return entry.id

    def complete(self, identifier, usage, cost, cost_source, duration_ms, error, response_id=None):
        with self.factory.begin() as session:
            entry = session.get(LLMCall, identifier)
            entry.usage = usage or None
            incoming = usage.get('prompt_tokens', usage.get('input_tokens'))
            outgoing = usage.get('completion_tokens', usage.get('output_tokens'))
            entry.tokens_in = incoming if isinstance(incoming, int) and not isinstance(incoming, bool) and incoming >= 0 else None
            entry.tokens_out = outgoing if isinstance(outgoing, int) and not isinstance(outgoing, bool) and outgoing >= 0 else None
            entry.cost_usd = str(cost) if cost is not None else None
            entry.cost_source = cost_source
            entry.duration_ms = duration_ms
            entry.error = error
            entry.provider_response_id = response_id
            entry.state = 'unknown' if cost is None else 'failed' if error else 'succeeded'
            if cost is not None:
                entry.reserved_usd = Decimal(0)


def strict_schema(schema):
    if isinstance(schema, dict):
        if 'properties' in schema:
            schema['required'] = list(schema['properties'])
            schema['additionalProperties'] = False
        for value in schema.values():
            strict_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            strict_schema(value)
    return schema


def provider_schema(model):
    """A small JSON Schema for providers; the full model still validates replies."""
    original = model.model_json_schema()
    definitions = original.get('$defs', {})

    def simplify(node, references=()):
        if '$ref' in node:
            reference = node['$ref']
            if not reference.startswith('#/$defs/') or reference in references:
                raise ValueError('Provider schema requires non-recursive local references')
            name = reference.removeprefix('#/$defs/').replace('~1', '/').replace('~0', '~')
            return simplify({**definitions[name], **{k: v for k, v in node.items() if k != '$ref'}}, (*references, reference))
        if 'anyOf' in node:
            alternatives = node['anyOf']
            present = [item for item in alternatives if item.get('type') != 'null']
            if len(present) != 1 or len(alternatives) != 2:
                raise ValueError('Provider schema supports only nullable unions')
            result = simplify({**present[0], **{k: v for k, v in node.items() if k != 'anyOf'}}, references)
            kind = result['type']
            result['type'] = [kind, 'null'] if isinstance(kind, str) else list(dict.fromkeys([*kind, 'null']))
            if 'enum' in result:
                result['enum'] = [*result['enum'], None]
            return result
        # Allow structure/short hints, never provider-side format/range/length limits.
        result = {key: node[key] for key in ('type', 'enum') if key in node}
        if 'properties' in node:
            result['properties'] = {key: simplify(value, references) for key, value in node['properties'].items()}
        if 'items' in node:
            result['items'] = simplify(node['items'], references)
        description = ' '.join(node.get('description', '').split())
        if node.get('format') in ('date', 'date-time', 'uuid'):
            description = {'date': 'ISO date: YYYY-MM-DD', 'date-time': 'ISO date-time', 'uuid': 'UUID string'}[node['format']]
        if description:
            result['description'] = description[:160]
        return result

    return strict_schema(simplify(original))


class LLMGateway:
    def __init__(self, factory=None, settings=None, transport=None, ledger=None, job_id=None, field_id=None, sleep=time.sleep):
        self.settings = settings or get_settings()
        self.transport = transport or LLMClient(self.settings)
        self.ledger = ledger or BudgetLedger(factory)
        self.job_id, self.field_id, self.sleep = job_id, field_id, sleep

    @property
    def configured(self):
        return bool(self.settings.llm_api_key.strip())

    def extract(self, code, messages, output_model):
        if not self.configured:
            raise GatewayError('llm_not_configured', 'LLM не настроена')
        schema = provider_schema(output_model)
        return self._call('extraction', {'model': self.settings.llm_model, 'messages': messages,
                          'temperature': 0, 'max_tokens': self.settings.llm_max_output_tokens,
                          'response_format': {'type': 'json_schema', 'json_schema': {'name': code, 'strict': True, 'schema': schema}}}, output_model)

    def embed(self, texts):
        if not texts or not self.settings.embedding_model or not (self.settings.embedding_api_key or self.settings.llm_api_key):
            return None
        return self._call('embedding', {'model': self.settings.embedding_model, 'input': texts})

    def _call(self, purpose, payload, output_model=None):
        embedding = purpose == 'embedding'
        input_price = self.settings.embedding_input_price_per_1m_usd if embedding else self.settings.llm_input_price_per_1m_usd
        output_price = Decimal(0) if embedding else self.settings.llm_output_price_per_1m_usd
        if input_price is None or output_price is None:
            raise GatewayError('llm_prices_not_configured', f"Не настроены тарифы для резервирования бюджета модели {payload['model']}")
        retries, repairs, attempt = 0, 0, 0
        proxy_error = {'code': 'llm_proxy_error', 'message': 'Прокси LLM недоступен'}
        while True:
            attempt += 1
            # UTF-8 byte count is a conservative token bound, including schema/messages.
            token_bound = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 1024
            reserve = (Decimal(token_bound) * input_price + Decimal(payload.get('max_tokens', 0)) * output_price) / 1_000_000
            identifier = self.ledger.reserve(reserve, self.settings, purpose, payload['model'], self.job_id, self.field_id, attempt)
            started = time.monotonic()
            response, usage, error, parsed = {}, {}, None, None
            status = None
            retry_after = None
            try:
                received = self.transport.request(purpose, payload)
                if not isinstance(received, dict):
                    raise ValueError('Invalid provider response')
                response = received
                usage = response.get('usage') or {}
                if not isinstance(usage, dict):
                    usage = {}
                if embedding:
                    items = sorted(response['data'], key=lambda item: item['index'])
                    parsed = [item['embedding'] for item in items]
                    if [item['index'] for item in items] != list(range(len(payload['input']))) or any(len(v) != self.settings.embedding_dim or not all(isinstance(n, (float, int)) and math.isfinite(n) for n in v) for v in parsed):
                        raise ValueError('Invalid embeddings')
                else:
                    parsed = output_model.model_validate_json(response['choices'][0]['message']['content'])
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                error, usage = http_diagnostics(exc.response, payload, self.settings)
                try:
                    retry_after = float(exc.response.headers.get('retry-after', ''))
                except ValueError:
                    pass
            except httpx.ProxyError:
                error = proxy_error
            except httpx.ConnectError:
                error = proxy_error if self.settings.llm_proxy_url else {'code': 'llm_transport_error', 'message': 'Ошибка соединения с моделью'}
            except httpx.ConnectTimeout:
                error = proxy_error if self.settings.llm_proxy_url else {'code': 'llm_timeout', 'message': 'Истекло время ожидания модели'}
            except (httpx.TimeoutException, TimeoutError):
                error = {'code': 'llm_timeout', 'message': 'Истекло время ожидания модели'}
            except httpx.TransportError:
                error = {'code': 'llm_transport_error', 'message': 'Ошибка соединения с моделью'}
            except ValidationError as exc:
                error = {'code': 'invalid_json', 'message': 'Ответ модели не соответствует схеме',
                         'validation_error': validation_summary(exc, output_model)}
            except (ValueError, KeyError, IndexError, TypeError):
                error = {'code': 'invalid_embedding' if embedding else 'invalid_json', 'message': 'Ответ модели не соответствует схеме'}
            except Exception:
                error = {'code': 'llm_client_error', 'message': 'Ошибка клиента модели'}
            rejected_without_usage = status is not None and 400 <= status < 500 and not usage
            usage = sanitized_usage(usage)
            cost, source = usage_cost(usage, input_price, output_price)
            if rejected_without_usage:
                cost, source = Decimal(0), 'request_rejected'
            self.ledger.complete(identifier, usage, cost, source, int((time.monotonic() - started) * 1000), error, response.get('id'))
            if not error:
                return parsed
            if error['code'] == 'llm_schema_rejected' and payload.get('response_format', {}).get('type') == 'json_schema':
                schema_text = json.dumps(payload['response_format']['json_schema']['schema'], ensure_ascii=False)
                payload = {**payload, 'response_format': {'type': 'json_object'},
                           'messages': [*payload['messages'], {'role': 'user', 'content': 'Верни JSON по этой схеме: ' + schema_text}]}
                continue
            if error['code'] == 'invalid_json' and repairs < 1:
                repairs += 1
                payload = {**payload, 'messages': [*payload['messages'], {'role': 'user', 'content': 'Ответ не прошёл валидацию. Верни только JSON строго по схеме поля; не добавляй факты.'}]}
                continue
            transient = status == 429 or (status is not None and status >= 500) or error['code'] in ('llm_timeout', 'llm_transport_error', 'llm_proxy_error')
            if transient and retries < self.settings.llm_max_retries:
                if retry_after is not None and (not math.isfinite(retry_after) or retry_after > 60):
                    raise GatewayError(error['code'], error['message'])
                self.sleep(max(min(2 ** retries, 8), retry_after or 0))
                retries += 1
                continue
            raise GatewayError(error['code'], error['message'])
