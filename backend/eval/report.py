"""Human-readable report and lossless JSON from the same result object."""
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re

from app.cards.schema import FIELDS


def text(value):
    return re.sub(r'([\\`*_\[\]|<>])', r'\\\1', escape(str(value), quote=False)).replace('\n', ' ')


def percent(value):
    return 'N/A' if value is None else f'{value:.2%}'


def markdown(report):
    primary = report['systems']['pipeline']
    lines = ['# Оценка карточек облигаций', '', f"Результат порогов основного прогона: **{primary['status']}**. Качество MVP: **{primary['mvp_quality_status']}**.", '',
             'Синтетические oracle/FakeLLM проверяют оценщик, не качество реальной модели. Черновики и unscorable не дают правильных ответов и блокируют приёмку.', '',
             f"Создано UTC: {report['generated_at']}. Корпус: {text(report['corpus']['name'])}. Выпусков: {report['corpus']['selected_count']}.",
             f"Часть: {text(report['corpus']['split'])}; seed: {report['corpus']['seed']}; frozen: {report['corpus']['frozen']}; типы: {text(report['corpus']['kinds'])}.", '',
             '## Конвейер / LLM vs baseline', '',
             '| Система | ISS correct/total | Accuracy ISS | Risk TP/FP/FN | Precision | Recall | Без цитаты | Неверные показанные значения | Review | Пороговый статус |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for system in report['systems'].values():
        accuracy, risk = system['iss_accuracy'], system['risk_micro']
        hallucinations = system['hallucinations']
        lines.append(f"| {text(system['label'])} | {accuracy['correct']}/{accuracy['total']} | {percent(accuracy['value'])} | {risk['tp']}/{risk['fp']}/{risk['fn']} | {percent(risk['precision'])} | {percent(risk['recall'])} | {hallucinations['hallucinations_without_citation']} | {hallucinations['hallucinations_wrong_value']} | {percent(system['needs_review']['value'])} | {system['status']} |")
    lines += ['', 'Метрики используют исходный машинный value/status. user_value/user_status не заменяют предсказание. Review с показанным неверным значением всё равно проходит аудит источника; скрытый review — FN, не TP. Совпадающие дубликаты — лишние FP.', '']
    for key, system in report['systems'].items():
        lines += [f"## {text(system['label'])}", '',
                  f"Модели из журналов: {text(', '.join(system['models']) or 'не вызывались/не указаны')}. Режимы: {text(system['modes'])}.",
                  f"ISS-only (отдельно): {system['iss_only_accuracy']['correct']}/{system['iss_only_accuracy']['total']} = {percent(system['iss_only_accuracy']['value'])}; в PDF accuracy не засчитывается.",
                  f"Mismatch: {system['mismatch']['correct']}/{system['mismatch']['total']}. Coverage: {system['coverage']['count']}/{system['coverage']['total']} = {percent(system['coverage']['value'])}.",
                  f"Needs review: {system['needs_review']['count']}/{system['needs_review']['total']} = {percent(system['needs_review']['value'])}; not_found полей: {system['not_found']}; всех проверяемых пар (включая исключённые): {system['field_count']}.",
                  f"Macro risk recall/precision: {percent(system['risk_macro_recall'])} / {percent(system['risk_macro_precision'])}. Ручных пометок проигнорировано: {system['human_edits_ignored']}.", '',
                  f"Показанных неподтверждённых/неверных единиц: {system['hallucinations']['hallucinations']}. Счётчики неверного значения и отсутствующей цитаты пересекаются и не суммируются.", '',
                  '| Рисковое поле | TP | FP | FN | Precision | Recall | TN полей |', '|---|---:|---:|---:|---:|---:|---:|']
        for code, values in system['risk'].items():
            lines.append(f"| {FIELDS[code].title} | {values['tp']} | {values['fp']} | {values['fn']} | {percent(values['precision'])} | {percent(values['recall'])} | {values['tn_fields']} |")
        lines += ['', '| Категория приёмки | TP | FN | Recall |', '|---|---:|---:|---:|']
        for category, values in system['categories'].items():
            lines.append(f"| {category} | {values['tp']} | {values['fn']} | {percent(values['recall'])} |")
        lines += ['', 'Одинаковый condition_id внутри выпуска считается один раз. Если условие отражено в нескольких полях, для TP категории должны совпасть все размеченные представления.', '',
                  '| Проверка | Результат |', '|---|---|']
        lines.extend(f"| {name} | {'PASS' if passed else 'FAIL'} |" for name, passed in system['gates'].items())
        lines += ['', f"Расходы, USD: {system['cost']['total_usd'] if system['cost']['total_usd'] is not None else 'N/A'}; известная часть {system['cost']['known_usd']}; выпусков с неизвестной стоимостью {system['cost']['unknown_issues']}.",
                  f"Время, мс: {system['duration']['total_ms'] if system['duration']['total_ms'] is not None else 'N/A'}; неизвестных выпусков {system['duration']['unknown_issues']}.", '',
                  '| Выпуск | Тип | Карточка / версия | Документов | Страниц / OCR | USD | Время, мс |', '|---|---|---|---:|---:|---:|---:|']
        for issue in system['issues']:
            lines.append(f"| {issue['isin']} | {issue['kind']} | {text(issue['card_id'])} / {issue['version']} | {issue['documents']} | {issue['pages']} / {issue['ocr_pages']} | {issue['cost_usd'] if issue['cost_usd'] is not None else 'N/A'} | {issue['duration_ms'] if issue['duration_ms'] is not None else 'N/A'} |")
        lines += ['', f"### Исключения ({len(system['exclusions'])})", '']
        lines += [f"- {text(e['isin'])}/{text(e['field'])}: {text(e['reason'])}" for e in system['exclusions']] or ['Нет.']
        lines += ['', f"### Ошибки ({len(system['errors'])})", '']
        lines += [f"- {text(e.get('isin'))}/{text(e.get('code'))}: {text(e.get('reason', e.get('status', 'ошибка входных данных')))}; документ {text(e.get('document_id'))}, стр. {text(e.get('page'))}" for e in system['errors']] or ['Нет.']
        lines.append('')
    lines += ['## Воспроизводимость и ограничения', '',
              'SHA256 gold, manifest и документов, настройки, выбор card_id, состав/источники и подробные результаты каждого поля записаны в парном JSON. Отсутствующие расходы/время не заменяются нулём. Время baseline — время локального extraction; время конвейера берётся из журнала build-job и не включает предыдущий ingest.', '',
              'Полнота автосбора, OCR accuracy и retrieval recall без отдельной эталонной разметки имеют статус N/A. Успех discover/download не доказывает полноту. Автоматическое сопоставление текста ограничено утверждёнными альтернативами/правилами; все TP реального корпуса проверяет второй разметчик.']
    return '\n'.join(lines) + '\n'


def save_report(report, output, json_output=None):
    output = Path(output)
    target = Path(json_output) if json_output else output.with_suffix('.json')
    if target.resolve() == output.resolve():
        raise ValueError('Markdown и JSON должны иметь разные пути')
    output.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown(report), encoding='utf-8')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
