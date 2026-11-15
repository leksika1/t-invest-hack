"""Read-only API adapter and saved GET-card exports."""
from urllib.parse import urlsplit
import json
import re

import httpx

from app.cards.schema import FIELDS, validate_value
from eval.corpus import read_json


def validate_cards(raw):
    json.dumps(raw, allow_nan=False)  # API JSON parsers may otherwise accept NaN/Infinity.
    if isinstance(raw, dict) and 'cards' in raw:
        raw = raw['cards']
    if not isinstance(raw, list):
        raise ValueError('Ожидается JSON-массив карточек или {cards: [...]}')
    result = {}
    for card in raw:
        if not isinstance(card, dict) or not isinstance(card.get('isin'), str) or card['isin'] in result:
            raise ValueError('Карточка без ISIN или повторный ISIN: оставьте одну выбранную версию')
        if card.get('status') not in ('ready', 'processing', 'failed'):
            raise ValueError('Неизвестное состояние карточки')
        combined = {}
        for group in ('iss_fields', 'risk_fields'):
            if not isinstance(card.get(group), dict):
                raise ValueError(f'Нет объекта {group}')
            for code, field in card[group].items():
                if code not in FIELDS or code in combined or not isinstance(field, dict) or field.get('code') != code:
                    raise ValueError('Неизвестное/повторное/несогласованное поле карточки')
                if FIELDS[code].iss != (group == 'iss_fields'):
                    raise ValueError('Поле в неверной группе')
                if field.get('status') not in ('verified_iss', 'mismatch_iss', 'extracted', 'not_found', 'needs_review'):
                    raise ValueError('Неверный машинный статус; user_status хранится отдельно')
                if field.get('value') is not None:
                    validate_value(code, field['value'])
                if field.get('status') == 'not_found' and field.get('value') is not None:
                    raise ValueError('not_found несовместим с показанным значением')
                if field.get('status') in ('verified_iss', 'mismatch_iss', 'extracted') and field.get('value') is None:
                    raise ValueError('Статус найденного поля требует value')
                if not FIELDS[code].iss and field.get('status') in ('verified_iss', 'mismatch_iss'):
                    raise ValueError('Рисковые поля не сверяются с ISS')
                if any(not isinstance(e, dict) for e in field.get('evidence', [])):
                    raise ValueError('Evidence должен содержать объекты')
                if not isinstance(field.get('source', {}), dict) or not isinstance(field.get('evidence', []), list):
                    raise ValueError('Неверный источник/evidence')
                if not FIELDS[code].iss and field.get('source', {}).get('kind') == 'moex_iss':
                    raise ValueError('MOEX ISS не является источником рисковых условий')
                combined[code] = field
        documents = card.get('documents')
        if not isinstance(documents, list):
            raise ValueError('Нет snapshot документов карточки')
        if any(not isinstance(d, dict) or not isinstance(d.get('id'), str) or not isinstance(d.get('sha256'), str) for d in documents):
            raise ValueError('Документ карточки требует id и sha256')
        if any(not re.fullmatch(r'[a-f0-9]{64}', d['sha256']) for d in documents):
            raise ValueError('Неверный sha256 документа карточки')
        if len({d['id'] for d in documents}) != len(documents):
            raise ValueError('Повторный ID документа карточки')
        if not isinstance(card.get('journal', {}), dict):
            raise ValueError('Неверный журнал карточки')
        result[card['isin']] = card
    return result


def from_json(path):
    return validate_cards(read_json(path))


def from_api(base_url, issues, manifest, client=None):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('base-url должен быть HTTP(S) URL стенда без ключей/query/fragment')
    base = base_url.rstrip('/')
    entries = {entry.isin: entry for entry in manifest.issues}
    cards, selections = [], []
    def fetch(transport):
        for issue in issues:
            entry = entries[issue.gold.isin]
            identifier = entry.card_id
            if identifier is None:
                response = transport.get(base + '/api/cards', params={'isin': entry.isin, 'limit': 1})
                response.raise_for_status()
                items = response.json()['items']
                if not items:
                    raise ValueError(f'{entry.isin}: на стенде нет карточки; CLI не создаёт job')
                identifier = items[0]['id']
            # IDs are opaque path components, never injected into a query/path verbatim.
            from urllib.parse import quote
            response = transport.get(base + '/api/cards/' + quote(str(identifier), safe=''))
            response.raise_for_status()
            card = response.json()
            if card.get('isin') != entry.isin:
                raise ValueError('Карточка стенда относится к другому ISIN')
            cards.append(card)
            selections.append({'isin': entry.isin, 'card_id': str(identifier), 'selection': 'manifest' if entry.card_id else 'latest', 'version': card.get('version')})
    if client is None:
        with httpx.Client(timeout=30, follow_redirects=False) as transport:
            fetch(transport)
    else:
        fetch(client)
    return validate_cards(cards), selections
