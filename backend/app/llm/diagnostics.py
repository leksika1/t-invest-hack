"""Bounded provider diagnostics: never persist raw envelopes or request content."""
import math
import json
import re


PROVIDER_REASONS = {
    'llm_schema_rejected': 'Модель отклонила схему ответа',
    'llm_request_rejected': 'Модель отклонила запрос',
    'llm_auth_error': 'Неверный ключ LLM',
    'llm_insufficient_credits': 'Нет кредитов на OpenRouter',
    'llm_access_denied': 'Провайдер LLM запретил доступ',
    'llm_model_not_found': 'Модель или endpoint LLM не найдены',
    'llm_request_too_large': 'Превышен размер запроса LLM',
    'llm_rate_limited': 'Превышен лимит запросов LLM',
}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _input_phrases(payload):
    contents = [m.get('content', '') for m in payload.get('messages', []) if isinstance(m, dict)]
    contents.extend(payload.get('input', []))
    phrases = set()
    for content in contents:
        if not isinstance(content, str):
            continue
        try:
            content = json.loads(content)
        except ValueError:
            pass
        for value in _strings(content):
            words = re.findall(r'\w+', value[:64000].casefold())
            # Omit excerpts even when a provider echoes only part of a document.
            if len(words) < 2:
                phrase = ' '.join(words)
                if len(phrase) >= 12:
                    phrases.add(phrase)
            else:
                phrases.update(phrase for i in range(len(words) - 1)
                               if len(phrase := ' '.join(words[i:i + 2])) >= 12)
    return phrases


def _safe_text(value, settings, phrases, limit=500):
    if not isinstance(value, str):
        return None
    value = value[:20000]
    for secret in (settings.llm_api_key, settings.embedding_api_key, settings.llm_proxy_url):
        if secret:
            value = value.replace(secret, '[redacted]')
    value = re.sub(r'\b(?:AIza[A-Za-z0-9_-]{6,}|AQ\.[A-Za-z0-9_.-]{6,})', '[redacted]', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', value, flags=re.I)
    value = re.sub(r'\bBearer\s+[^\s\"\'<>]+', 'Bearer [redacted]', value, flags=re.I)
    value = re.sub(r'\b(?:https?|socks5h?)://[^\s\"\'<>]+', '[redacted URL]', value, flags=re.I)
    value = re.sub(r'''(?ix)\b(?:api[_-]?key|authorization|access[_-]?token|password|token)\b
                      ["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', '[redacted]', value)
    value = re.sub(r'(?is)\b(?:request(?:\s+(?:body|payload|json))?|messages|prompt|input|content|documents|fragments)\s*[:=].*',
                   '[request content omitted]', value)
    value = re.sub(r'(?s)\{.*', '[details omitted]', value)
    normalized = ' '.join(re.findall(r'\w+', value.casefold()))
    if any(phrase in normalized for phrase in phrases):
        return '[request content omitted]'
    return re.sub(r'\s+', ' ', value).strip()[:limit] or None


def http_diagnostics(response, payload, settings):
    """Return a Russian reason with sanitized details and the original usage."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, list):
        body = next((item for item in body if isinstance(item, dict) and isinstance(item.get('error'), dict)), {})
    if not isinstance(body, dict):
        body = {}
    usage = body.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    schema_problem = status in (400, 422) and any(
        word in response.text[:64000].casefold() for word in ('schema', 'too many states', 'response_format', 'structured'))
    code = 'llm_schema_rejected' if schema_problem else {
        400: 'llm_request_rejected', 401: 'llm_auth_error', 402: 'llm_insufficient_credits',
        403: 'llm_access_denied', 404: 'llm_model_not_found', 413: 'llm_request_too_large',
        422: 'llm_request_rejected', 429: 'llm_rate_limited',
    }.get(status, 'llm_request_rejected' if 400 <= status < 500 else 'llm_http_error')
    if status == 407 and settings.llm_proxy_url:
        code = 'llm_proxy_error'
    message = 'Прокси LLM недоступен' if code == 'llm_proxy_error' else PROVIDER_REASONS.get(code, f'Провайдер вернул HTTP {status}')
    result = {'code': code, 'message': message, 'http_status': status}
    error = body.get('error')
    error = error if isinstance(error, dict) else {}
    metadata = error.get('metadata', {})
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = metadata.get('raw')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if len(raw) <= 65536 else None
        except ValueError:
            raw = None
    inner = raw.get('error', raw) if isinstance(raw, dict) else {}
    inner = inner if isinstance(inner, dict) else {}
    phrases = _input_phrases(payload)
    for name, value, limit in (
        ('openrouter_message', error.get('message'), 500),
        ('provider_name', metadata.get('provider_name'), 80),
        ('provider_message', inner.get('message'), 500),
        ('provider_status', inner.get('status'), 64),
    ):
        if safe := _safe_text(value, settings, phrases, limit):
            result[name] = safe
    google = 'generativelanguage.googleapis.com' in settings.llm_base_url
    if google:
        if safe := _safe_text(error.get('message'), settings, phrases):
            result['message'] = safe
        if safe := _safe_text(error.get('status'), settings, phrases, 64):
            result['status'] = safe
        details = error.get('details') or []
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict):
                continue
            violations = detail.get('violations')
            for violation in violations[:10] if isinstance(violations, list) else []:
                if isinstance(violation, dict) and 'quota_id' not in result:
                    for key, raw_key in [('quota_id', 'quotaId'), ('quota_value', 'quotaValue')]:
                        value = violation.get(raw_key)
                        if isinstance(value, (str, int)) and (safe := _safe_text(str(value), settings, phrases, 160)):
                            result[key] = safe
            delay = detail.get('retryDelay')
            if isinstance(delay, str) and re.fullmatch(r'\d+(?:\.\d+)?s', delay):
                number = float(delay[:-1])
                if math.isfinite(number):
                    result['retry_after_seconds'] = number
        daily = status == 429 and ('perday' in result.get('quota_id', '').lower() or
                  'per day' in str(error.get('message', '')).lower() or result.get('retry_after_seconds', 0) > 60)
        if daily:
            result['daily_quota'] = True
            quota = result.get('quota_value')
            hours = math.ceil(result.get('retry_after_seconds', 0) / 3600)
            result['message'] = 'Исчерпана дневная квота Google' + (' бесплатного тарифа' if 'freetier' in result.get('quota_id', '').lower() else '')
            if quota and re.fullmatch(r'\d+', quota):
                result['message'] += f' ({quota} запросов на модель)'
            if hours:
                result['message'] += f', сброс через ~{hours} ч'
    return result, usage


def validation_summary(error, model):
    """Only allowlisted field paths and Pydantic error codes; never input/msg/ctx."""
    names = set()
    def fields(node):
        if isinstance(node, dict):
            names.update(node.get('properties', {}))
            for value in node.values():
                fields(value)
        elif isinstance(node, list):
            for value in node:
                fields(value)
    fields(model.model_json_schema())
    summaries = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:10]:
        path = '.'.join(str(part) if isinstance(part, int) else part if part in names else '?' for part in item['loc']) or 'response'
        code = item['type'] if re.fullmatch(r'[a-z_]+', item['type']) else 'validation_error'
        summaries.append(f'{path}: {code}')
    return '; '.join(summaries)[:500]
