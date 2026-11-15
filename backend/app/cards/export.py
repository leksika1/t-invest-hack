import html
import json
import re

from app.cards.schema import DISCLAIMER, SPECS

REASONS = {'llm_not_configured': 'LLM не настроена', 'quote_not_verified': 'Цитата не подтверждена',
           'budget_exceeded': 'Дневной бюджет LLM исчерпан', 'not_in_documents': 'Не найдено в переданных документах',
           'iss_unavailable': 'Данные ISS недоступны', 'iss_only': 'Значение напрямую из MOEX ISS',
           'llm_prices_not_configured': 'Не настроены тарифы LLM', 'llm_timeout': 'Истекло время ожидания модели',
           'llm_http_error': 'Ошибка ответа провайдера модели', 'llm_transport_error': 'Ошибка соединения с моделью',
           'llm_client_error': 'Ошибка клиента модели',
           'llm_proxy_error': 'Прокси LLM недоступен',
           'invalid_json': 'Ответ модели не соответствует схеме', 'low_confidence': 'Низкая уверенность модели',
           'ambiguous_value': 'Неоднозначное значение', 'iss_comparison_incomplete': 'Недостаточно данных для полной сверки с ISS',
           'value_not_supported_by_quote': 'Значение не подтверждается параметрами цитаты', 'incomplete_evidence': 'Недостаточно цитат для всех элементов',
           'document_processing_failed': 'Часть документов не удалось распознать', 'processing': 'Поле обрабатывается',
           'conflicting_sources': 'Противоречие источников или неоднозначная редакция'}


def escape(value):
    return re.sub(r'([\\`*_\[\]()#|])', r'\\\1', html.escape(str(value), quote=False))


def render_markdown(card):
    document_names = {str(d.get('id')): d['filename'] for d in card['documents'] if d.get('id')}
    lines = ['# Карточка облигации ' + escape(card['isin']), '', DISCLAIMER, '', 'Статус: ' + card['status'],
             'Версия: ' + str(card['version']), '', '## Выпуск', '', escape(json.dumps(card['header'], ensure_ascii=False))]
    for spec in SPECS:
        field = card['iss_fields' if spec.iss else 'risk_fields'][spec.code]
        lines.extend(['', '## ' + spec.title, '', 'Машинный статус: ' + field['status']])
        if field['value'] is not None:
            label = 'Значение MOEX ISS: ' if field.get('source', {}).get('kind') == 'moex_iss' else 'Значение документа: '
            lines.append(label + escape(json.dumps(field['value'], ensure_ascii=False)))
        elif field.get('reason'):
            lines.append(escape(REASONS.get(field['reason'], field['reason'])))
        if field['iss_value'] is not None and field.get('source', {}).get('kind') != 'moex_iss':
            lines.append('Значение ISS: ' + escape(json.dumps(field['iss_value'], ensure_ascii=False)))
        if field.get('source', {}).get('kind') == 'moex_iss':
            lines.append('Источник: MOEX ISS ' + escape(field['source'].get('url') or ''))
        if field.get('user_status'):
            lines.append('Проверка человеком: ' + field['user_status'])
            if field['user_status'] == 'corrected':
                lines.append('Исправленное значение: ' + escape(json.dumps(field['user_value'], ensure_ascii=False)))
        for evidence in [field, *field.get('evidence', [])]:
            if evidence.get('quote'):
                lines.extend(['', *('> ' + escape(line) for line in evidence['quote'].splitlines()),
                              'Источник: ' + escape(document_names.get(str(evidence.get('document_id')), evidence.get('document_id'))) + ', стр. ' + str(evidence.get('page')) + ', пункт ' + escape(evidence.get('clause') or 'не указан')])
    lines.extend(['', '## Документы и происхождение', ''])
    for document in card['documents']:
        lines.append('- ' + escape(document['filename']) + ' (sha256: ' + document['sha256'] + ')')
        for origin in document.get('origins', []):
            lines.append('  ' + escape(json.dumps(origin, ensure_ascii=False)))
    lines.extend(['', '## Журнал', '', escape(json.dumps(card['journal'], ensure_ascii=False)), '', DISCLAIMER, ''])
    return '\n'.join(lines)
