"""Offline A4 report; embedded OFL fonts, no browser or remote resources."""
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from io import BytesIO
import math
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from app.cards.export import REASONS
from app.cards.schema import DISCLAIMER, SPECS
from app.llm.demo import DEMO_NOTICE

INK = colors.HexColor('#0e1a2b')
MUTED = colors.HexColor('#5b6577')
RULE = colors.HexColor('#dde2ec')
PAPER = colors.white
BLUE = colors.HexColor('#1f3a8a')
STATUS = {'verified_iss': ('Сверено с ISS', '#2f7a55'), 'extracted': ('Извлечено', '#1f3a8a'),
          'mismatch_iss': ('Расхождение с ISS', '#b42318'), 'not_found': ('Не найдено', '#5a6673'),
          'needs_review': ('Нужна проверка', '#946014')}
LABELS = {'date': 'Дата', 'amount': 'Сумма', 'currency': 'Валюта', 'type': 'Тип', 'description': 'Описание',
          'periods': 'Периоды', 'from': 'С купона №', 'to': 'По купон №', 'rate_percent': 'Ставка, %',
          'formula': 'Формула', 'text': 'Формулировка', 'base': 'Базовая ставка', 'spread_pp': 'Спред, п. п.',
          'cap_percent': 'Верхняя граница, %', 'floor_percent': 'Нижняя граница, %', 'observation_rule': 'Наблюдение',
          'rounding_rule': 'Округление', 'frequency_per_year': 'Выплат в год', 'period_days': 'Период, дней',
          'total_payments': 'Всего купонов', 'payments': 'Выплаты', 'number': '№', 'payment_date': 'Дата выплаты',
          'start': 'Начало', 'end': 'Конец', 'events': 'События', 'price_percent': 'Цена, % номинала',
          'percent': 'Доля, %', 'percent_base': 'База расчёта', 'items': 'Условия', 'condition': 'Условие',
          'test_date_rule': 'Проверка', 'consequence': 'Последствия', 'exceptions': 'Исключения', 'grounds': 'Основания',
          'trigger': 'Основание', 'deadline': 'Срок', 'conditions': 'Условия', 'date_rule': 'Дата / правило',
          'price_rule': 'Цена / правило', 'notice_rule': 'Уведомление', 'party': 'Лицо', 'scope': 'Объём',
          'expiry': 'Срок действия', 'threshold': 'Порог', 'entities': 'Круг лиц', 'grace_period': 'Льготный срок',
          'action': 'Действие', 'restriction': 'Ограничение', 'procedures': 'Порядок', 'offer_date': 'Дата оферты',
          'window_start': 'Начало окна', 'window_end': 'Конец окна', 'window_rule': 'Окно требований',
          'via': 'Через кого', 'submission': 'Способ подачи', 'settlement_rule': 'Расчёты',
          'current_rate_percent': 'Текущая ставка ISS, %', 'raw_type': 'Тип в ISS', 'inferred': 'Предположение ISS'}
ENUMS = {'fixed': 'Фиксированный', 'floating': 'Плавающий', 'variable': 'Переменный', 'unknown': 'Неизвестно',
         'put': 'Put', 'call': 'Call', 'surety': 'Поручительство', 'third_party_offer': 'Оферта третьего лица',
         'pledge': 'Залог', 'other': 'Иное', 'initial_face_value': 'Первоначальный номинал',
         'remaining_face_value': 'Остаточный номинал'}


@lru_cache(maxsize=1)
def register_fonts():
    root = Path(__file__).with_name('fonts')
    for name, filename in [('Golos', 'GolosText-Regular.ttf'), ('GolosBold', 'GolosText-Semibold.ttf'), ('PTSerif', 'PTSerif-Regular.ttf')]:
        pdfmetrics.registerFont(TTFont(name, str(root / filename)))
    pdfmetrics.registerFontFamily('Golos', normal='Golos', bold='GolosBold', italic='Golos', boldItalic='GolosBold')
    pdfmetrics.registerFontFamily('PTSerif', normal='PTSerif', bold='PTSerif', italic='PTSerif', boldItalic='PTSerif')


def rosette_points(seed, layer):
    """FNV-1a and curves match frontend/src/art/guilloche.tsx for ASCII ISIN."""
    number = 2166136261
    for char in seed:
        number = ((number ^ ord(char)) * 16777619) & 0xffffffff
    lobes = [17, 19, 23, 25, 27, 29][number % 6]
    radius, amplitude = 70 + layer * 3, 23 + layer * 1.5
    phase = (number % 360) * math.pi / 180 + layer * .11
    points = []
    for i in range(1441):
        t = i / 1440 * math.pi * 14
        points.append((round(120 + radius * math.cos(t) + amplitude * math.cos(lobes / 7 * t + phase), 2),
                       round(120 + radius * math.sin(t) - amplitude * math.sin(lobes / 7 * t + phase), 2)))
    return points


class Rosette(Flowable):
    def __init__(self, seed, size=84):
        super().__init__()
        self.width = self.height = size
        self.seed = seed

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.translate(0, self.height)
        canvas.scale(1, -1)
        canvas.scale(self.width / 240, self.height / 240)
        canvas.setStrokeColor(colors.HexColor('#728bc3'))
        canvas.setLineWidth(.65)
        for layer in range(5):
            points = rosette_points(self.seed, layer)
            path = canvas.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            path.close()
            canvas.drawPath(path)
        canvas.circle(120, 120, 32)
        canvas.circle(120, 120, 35)
        canvas.restoreState()


class FactStamp(Flowable):
    """Small monochrome security stamp, suitable for office printing."""
    def __init__(self):
        super().__init__()
        self.width, self.height = 168, 38

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(BLUE)
        canvas.setFillColor(BLUE)
        canvas.setLineWidth(.6)
        canvas.roundRect(1, 3, 166, 30, 15)
        canvas.roundRect(4, 6, 160, 24, 12)
        canvas.setFont('Golos', 8)
        canvas.drawCentredString(84, 15, 'Факты, не рекомендация')
        canvas.restoreState()


def text(value):
    if value is None:
        return 'Нет данных'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, list):
        return '; '.join(text(item) for item in value) if value else 'Не предусмотрено (по данным источника)'
    if isinstance(value, dict):
        return '; '.join(f'{LABELS.get(key, key)}: {text(item)}' for key, item in value.items() if item is not None) or 'Нет данных'
    return ENUMS.get(str(value), str(value))


def display_value(field):
    value, iss = field.get('value'), field.get('iss_value')
    if field.get('code') == 'coupon_schedule' and not (value or {}).get('payments'):
        return iss or value
    return (iss or value) if field.get('source', {}).get('kind') == 'moex_iss' else value


def brief(value):
    """Compact facts for the overview; full comparisons remain below."""
    if not isinstance(value, dict):
        return text(value)
    if 'amount' in value and 'currency' in value:
        return f"{value['amount']} {value['currency']}"
    if 'date' in value:
        return value['date']
    if value.get('formula'):
        return text(value['formula'])
    if value.get('current_rate_percent'):
        return f"{value['current_rate_percent']}% (текущая ставка ISS)"
    if value.get('periods'):
        periods = value['periods']
        first = periods[0]
        known = f"{first['rate_percent']}%" if first.get('rate_percent') is not None else f"{first['amount']} в валюте номинала" if first.get('amount') is not None else 'Не определено'
        return f"{known} для купонов № {first['from']}–{first['to']}; известных периодов: {len(periods)}"
    if isinstance(value.get('events'), list) and len(value['events']) > 1:
        dates = sorted(event['date'] for event in value['events'] if event.get('date'))
        return f"Событий: {len(value['events'])}" + (f"; даты: {dates[0]} — {dates[-1]}" if dates else '')
    return text(value)


def risk_summary(field):
    if field.get('status') == 'not_found':
        return 'Нет в документах', field.get('reason_message') or REASONS.get(field.get('reason'), 'Проверьте полноту документов')
    if field.get('status') not in ('extracted', 'verified_iss') or field.get('value') is None:
        return 'Требует проверки', field.get('reason_message') or REASONS.get(field.get('reason'), 'Проверьте условие вручную')
    return 'Есть', text(field['value'])


def source(field, documents):
    document = next((d for d in documents if str(d.get('id')) == str(field.get('document_id'))), {})
    name = field.get('source', {}).get('name') or document.get('filename') or 'Документ'
    label = name + (f", стр. {field['page']}" if field.get('page') else '') + (f", п. {field['clause']}" if field.get('clause') else '')
    url = field.get('source', {}).get('url') or next((o.get('download_url') or o.get('source_url') for o in document.get('origins', []) if o.get('download_url') or o.get('source_url')), None)
    try:
        parts = urlsplit(url or '')
        if parts.scheme in ('http', 'https') and parts.netloc:
            url = urlunsplit(parts._replace(fragment=f"page={field['page']}" if field.get('page') else parts.fragment))
            return f'<link href="{escape(url, quote=True)}" color="#1f3a8a">{escape(label)}</link>'
    except ValueError:
        pass
    return escape(label)


def render_pdf(card, generated_at=None):
    register_fonts()
    generated = generated_at or datetime.now(timezone.utc)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TextRu', fontName='Golos', fontSize=9, leading=13, textColor=INK, spaceAfter=6, splitLongWords=True))
    styles.add(ParagraphStyle(name='SmallRu', parent=styles['TextRu'], fontSize=7.5, leading=10.5, textColor=MUTED))
    styles.add(ParagraphStyle(name='HeadingRu', parent=styles['TextRu'], fontName='GolosBold', fontSize=15, leading=19, spaceBefore=17, spaceAfter=9, keepWithNext=True))
    styles.add(ParagraphStyle(name='SubRu', parent=styles['TextRu'], fontName='GolosBold', fontSize=10, leading=14, spaceBefore=10, keepWithNext=True))
    styles.add(ParagraphStyle(name='QuoteRu', parent=styles['TextRu'], fontName='PTSerif', fontSize=11, leading=16, backColor=PAPER, borderColor=RULE, borderWidth=.5, borderPadding=9, leftIndent=10, rightIndent=10, spaceBefore=9, spaceAfter=12))
    styles.add(ParagraphStyle(name='TitleRu', parent=styles['TextRu'], fontName='PTSerif', fontSize=17, leading=22, textColor=BLUE))

    def p(value, style='TextRu', markup=False):
        return Paragraph(value if markup else escape(str(value)).replace('\n', '<br/>'), styles[style])

    def heading(value):
        story.append(p(value, 'HeadingRu'))

    def table(rows, widths, header=True):
        converted = [[cell if isinstance(cell, (Paragraph, Flowable)) else p(cell, 'SmallRu') for cell in row] for row in rows]
        result = LongTable(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT', splitInRow=1)
        commands = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), .4, RULE),
                    ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                    ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]
        if header:
            commands.append(('BACKGROUND', (0, 0), (-1, 0), PAPER))
        result.setStyle(TableStyle(commands))
        return result

    def status(field):
        label, color = STATUS.get(field.get('status'), ('Неизвестен', '#5a6673'))
        return p(f'<font color="{color}">{label}</font>', 'SmallRu', True)

    width = A4[0] - 84
    story = []
    header = card.get('header', {})
    certificate = table([[p(card['isin'], 'SubRu'), Rosette(card['isin'])],
                         [p(header.get('name') or 'Облигация', 'TitleRu'), p(f"Версия {card['version']}", 'SmallRu')],
                         [p(header.get('issuer') or 'Эмитент не указан'), p(f"ИНН {header.get('inn') or 'не указан'}", 'SmallRu')],
                         [p(f"Регистрационный номер: {header.get('registration_number') or 'не указан'}", 'SmallRu'), p('Бланк выпуска', 'SmallRu')]], [width - 110, 110], False)
    certificate.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .8, BLUE), ('BACKGROUND', (0, 0), (-1, -1), PAPER)]))
    story.extend([certificate, Spacer(1, 10), p(DISCLAIMER, 'SmallRu'), p('Сформировано: ' + generated.strftime('%d.%m.%Y %H:%M UTC'), 'SmallRu')])
    if card.get('journal', {}).get('llm_mode') == 'demo':
        story.append(p(DEMO_NOTICE, 'SmallRu'))
    fields = card['iss_fields']
    currency = (display_value(fields.get('face_value', {})) or {}).get('currency') or 'в валюте номинала'
    heading('Сводка')
    counts = {}
    for field in card['risk_fields'].values():
        label, _ = risk_summary(field)
        counts[label] = counts.get(label, 0) + 1
    story.append(p(' · '.join(f'{label}: {count}' for label, count in counts.items())))
    for spec in SPECS:
        if not spec.iss and spec.code in card['risk_fields']:
            label, detail = risk_summary(card['risk_fields'][spec.code])
            story.append(p(f'{spec.title} — {label}. {detail}'))
    schedule_field = fields.get('coupon_schedule', {})
    schedule = display_value(schedule_field) or {}
    payments = sorted(schedule.get('payments') or [], key=lambda item: item['payment_date'])
    next_payment = next((item for item in payments if item['payment_date'] >= generated.date().isoformat()), None)
    for code in ('face_value', 'maturity_date', 'coupon_rate', 'offers', 'amortization'):
        field = fields.get(code)
        if field:
            story.append(p(f"{next(s.title for s in SPECS if s.code == code)}: {brief(display_value(field))}"))
    story.append(p('Ближайшая выплата: ' + (next_payment['payment_date'] if next_payment else 'нет будущих дат в графике')))
    story.append(p('Отсутствие условия в найденных документах не доказывает отсутствие обязательства.', 'SmallRu'))

    heading('Параметры выпуска и сверка ISS')
    story.append(p('ISS — справочные данные биржи. Сверка и машинный статус приведены отдельно; пустой график документа не заменяет график ISS.', 'SmallRu'))
    rows = [['Параметр / статус', 'В документе', 'MOEX ISS']]
    for spec in SPECS:
        if not spec.iss or spec.code not in fields:
            continue
        field = fields[spec.code]
        def compact(value):
            if spec.code == 'coupon_schedule' and value is not None:
                count = len(value.get('payments') or [])
                return f"{count} известных выплат; всего: {value.get('total_payments') or 'не указано'}" if count else f"Дат выплат нет; всего по документу: {value.get('total_payments') or 'не указано'}"
            if spec.code == 'coupon_rate':
                return brief(value)
            return text(value)
        document_value = None if field.get('source', {}).get('kind') == 'moex_iss' else field.get('value')
        label, color = STATUS.get(field.get('status'), ('Неизвестен', '#5a6673'))
        if field.get('source', {}).get('kind') == 'moex_iss' and field.get('reason') == 'iss_not_confirmed':
            label = 'Из ISS, в документах не подтверждено'
        rows.append([p(escape(spec.title) + f'<br/><font color="{color}">{label}</font>', 'SmallRu', True), compact(document_value), compact(field.get('iss_value'))])
    story.append(table(rows, [width * .29, width * .355, width * .355]))

    heading('Рисковые условия и цитаты')
    for spec in SPECS:
        if spec.iss or spec.code not in card['risk_fields']:
            continue
        field = card['risk_fields'][spec.code]
        risk_start = len(story)
        story.extend([p(spec.title, 'SubRu'), status(field)])
        if field.get('value') is not None:
            story.append(p(text(field['value'])))
        reason = field.get('reason_message') or REASONS.get(field.get('reason'))
        if reason:
            story.append(p(reason, 'SmallRu'))
        for evidence in [field, *field.get('evidence', [])]:
            if evidence.get('quote'):
                story.extend([p(evidence['quote'], 'QuoteRu'), p(source(evidence, card['documents']), 'SmallRu', True)])
        if field.get('status') in ('not_found', 'needs_review'):
            story.append(p('Проверьте полноту документов и условие вручную; при необходимости загрузите решение о выпуске, условия размещения и программу.', 'SmallRu'))
        if field.get('user_status'):
            story.append(p('Проверка человеком: ' + ('Исправлено' if field['user_status'] == 'corrected' else 'Подтверждено')))
            if field.get('user_status') == 'corrected':
                story.append(p('Значение пользователя (отдельно от машинного): ' + text(field.get('user_value'))))
        if not field.get('quote') and not field.get('evidence') and len(text(field.get('value'))) < 1500:
            story[risk_start:] = [KeepTogether(story[risk_start:])]
    # Include provenance and human corrections for exchange parameters as well.
    for spec in SPECS:
        field = fields.get(spec.code) if spec.iss else None
        if not field:
            continue
        if field.get('quote') or field.get('evidence') or field.get('user_status'):
            story.append(p(spec.title + ' — подтверждение и правки', 'SubRu'))
            for evidence in [field, *field.get('evidence', [])]:
                if evidence.get('quote'):
                    story.extend([p(evidence['quote'], 'QuoteRu'), p(source(evidence, card['documents']), 'SmallRu', True)])
            if field.get('user_status'):
                story.append(p('Проверка человеком: ' + ('Исправлено: ' + text(field.get('user_value')) if field['user_status'] == 'corrected' else 'Подтверждено')))

    coupon_start = len(story)
    heading('Купонный график')
    document_total = (schedule_field.get('value') or {}).get('total_payments')
    total_label = f'Всего по документу: {document_total}' if document_total else f"Всего по источнику графика: {schedule.get('total_payments') or 'не указано'}"
    story.append(p(f"Известно выплат: {len(payments)}. {total_label}."))
    if schedule is schedule_field.get('iss_value') or schedule_field.get('source', {}).get('kind') == 'moex_iss':
        story.append(p('Источник графика — MOEX ISS; даты не подтверждены документами.', 'SmallRu'))
    if payments:
        # First three and next five, without duplicate rows; full schedule stays in Markdown/card.
        selected = {item['number']: item for item in payments[:3]}
        selected.update({item['number']: item for item in [item for item in payments if item['payment_date'] >= generated.date().isoformat()][:5]})
        rates = display_value(fields.get('coupon_rate', {})) or {}
        rows = [['Купон №', 'Дата выплаты', 'Известная сумма / ставка']]
        for item in sorted(selected.values(), key=lambda item: item['payment_date']):
            period = next((period for period in rates.get('periods', []) if period['from'] <= item['number'] <= period['to']), {})
            rate = f"{period['amount']} {currency}" if period.get('amount') is not None else f"{period['rate_percent']}%" if period.get('rate_percent') is not None else 'Не определена / по формуле'
            rows.append([str(item['number']), item['payment_date'], rate])
        story.append(table(rows, [75, 130, width - 205]))
        story.append(p(f'Показано {len(selected)} из {len(payments)} известных выплат. Полный график — в карточке и Markdown. Прошедшая дата не подтверждает факт выплаты.', 'SmallRu'))
    else:
        story.append(p('График не найден. Загрузите условия выпуска.'))
    story[coupon_start:] = [KeepTogether(story[coupon_start:])]

    journal_start = len(story)
    heading('Журнал сборки и документы')
    journal = card.get('journal', {})
    cost = journal.get('cost_usd')
    story.append(p(f"Документов использовано: {journal.get('documents_used', 'неизвестно')}; страниц / OCR: {journal.get('pages', '—')} / {journal.get('ocr_pages', '—')}."))
    story.append(p('Модель: ' + ('Демо-режим (без LLM)' if journal.get('llm_mode') == 'demo' else ', '.join(journal.get('models') or []) or 'Без вызовов модели')))
    story.append(p(f"Стоимость, USD: {cost if cost is not None else 'неизвестна'}; известная часть: {journal.get('known_cost_usd', 'неизвестна')}; вызовов с неизвестной стоимостью: {journal.get('unknown_cost_calls', 0)}."))
    duration = journal.get('duration_ms')
    story.append(p(f"Время: {duration / 1000:.1f} с" if duration is not None else 'Время неизвестно'))
    story[journal_start:] = [KeepTogether(story[journal_start:])]
    for document in card['documents']:
        story.append(p(document.get('filename', 'Документ'), 'SubRu'))
        story.append(p(f"{'Использован' if document.get('enabled') and document.get('status') == 'ready' else 'Не использован'}; страниц / OCR: {document.get('pages', 0)} / {document.get('ocr_pages', 0)}; статус: {document.get('status', 'неизвестен')}", 'SmallRu'))
        for origin in document.get('origins', []):
            story.append(p(f"Источник: {origin.get('source', 'неизвестен')}; получено: {origin.get('fetched_at') or 'неизвестно'}; публикация: {origin.get('published_at') or 'неизвестна'}", 'SmallRu'))
            if origin.get('archive'):
                archive = origin['archive']
                story.append(p(f"Архив: {archive.get('filename') or archive.get('url') or 'ZIP'}; файл: {archive.get('entry_name')}", 'SmallRu'))
    for warning in journal.get('warnings') or []:
        story.append(p(REASONS.get(warning, warning), 'SmallRu'))
    for error in journal.get('errors') or []:
        story.append(p(error.get('message', 'Ошибка сборки'), 'SmallRu'))
    story.extend([Spacer(1, 16), KeepTogether([FactStamp(), p(DISCLAIMER, 'SmallRu')])])

    output = BytesIO()
    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(42, 38, A4[0] - 42, 38)
        canvas.setFont('Golos', 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(42, 26, card['isin'] + f" · версия {card['version']}")
        canvas.drawRightString(A4[0] - 42, 26, f"Страница {document.page}")
        canvas.restoreState()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=35, bottomMargin=54,
                                 title=f"Карточка {card['isin']}", author='Карточка облигации', pageCompression=1)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
