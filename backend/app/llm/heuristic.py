"""Shared frozen regex-v1 primitives; no network or eval-only imports."""
from datetime import datetime
import re
from app.cards.verification import normalized_text

DATE = r'\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}'
NUMBER = r'\d+(?:[.,]\d+)?'
PATTERNS = {
    'face_value': r'номинальн.{0,50}стоимост', 'placement_date': r'дат[аы] (?:начала )?размещени',
    'maturity_date': r'дат[аы] (?:окончательного )?погашени', 'coupon_type': r'тип купона|купон.{0,30}(?:фиксирован|плавающ|переменн)',
    'coupon_rate': r'ставк[аи].{0,30}купона|купонн.{0,20}ставк', 'coupon_schedule': r'купонные выплаты:|график (?:выплат|купонов)',
    'offers': r'оферта\s+(?:put|call)', 'amortization': r'амортизация|частичное погашение номинал',
    'covenants': r'ковенант|долг.{0,40}ebitda',
    'holder_early_redemption': r'по требованию владельц|владельц.{0,40}вправе требовать досрочн',
    'issuer_call_option': r'эмитент вправе досрочно погасить|по усмотрению эмитента|call-опцион',
    'guarantee_security': r'поручительств|обеспечен.{0,50}залог|оферта третьего лица',
    'cross_default': r'кросс[- ]?дефолт|неисполнени.{0,40}иных долговых обязательств',
    'issuer_restrictions': r'эмитенту запрещ|эмитент не вправе|ограничени.{0,30}дивиденд',
    'offer_procedure': r'через депозитарий|порядок (?:акцепта|предъявления)|окно подачи',
}


def day(text):
    match = re.search(DATE, text)
    if not match:
        return None
    try:
        return datetime.strptime(match[0], '%d.%m.%Y' if '.' in match[0] else '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def item_for(code, text):
    clean = re.sub(r'^\s*\d+(?:\.\d+)+\.?\s*', '', text).strip()
    lower = normalized_text(clean)
    if re.search(r'(?:не предусмотрен|отсутствуют|не предоставля)', lower):
        return None
    if code == 'face_value':
        match = re.search(r'(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(рубл\w*|rub|доллар\w*|usd|евро|eur)', lower)
        if match:
            currency = 'RUB' if match[2].startswith(('рубл', 'rub')) else 'USD' if match[2].startswith(('доллар', 'usd')) else 'EUR'
            return {'amount': match[1].strip(), 'currency': currency}
    elif code in ('placement_date', 'maturity_date'):
        if value := day(clean):
            return {'date': value}
    elif code == 'coupon_type':
        for word, value in [('фиксирован', 'fixed'), ('плавающ', 'floating'), ('переменн', 'variable')]:
            if word in lower:
                return {'type': value, 'description': None}
    elif code == 'coupon_rate':
        rate = re.search(r'(' + NUMBER + r')\s*(?:%|процент)', lower)
        period = re.search(r'с\s+(\d+)\s+по\s+(\d+)\s+купон', lower)
        if rate:
            return {'from': int(period[1]) if period else 1, 'to': int(period[2]) if period else 1, 'rate_percent': rate[1], 'amount': None}
    elif code == 'coupon_schedule':
        dates = [day(match[0]) for match in re.finditer(DATE, lower)]
        if dates and all(dates):
            return [{'number': i, 'start': None, 'end': None, 'payment_date': date} for i, date in enumerate(dates, 1)]
    elif code == 'offers':
        date, kind = day(clean), re.search(r'оферта\s+(put|call)', lower)
        price = re.search(r'цен[ае]\s+(' + NUMBER + r')\s*(?:%|процент)', lower)
        if date and kind:
            return {'date': date, 'type': kind[1], 'price_percent': price[1] if price else None}
    elif code == 'amortization':
        date = day(clean)
        percent = re.search(r'(' + NUMBER + r')\s*(?:%|процент)', lower)
        if date and percent:
            return {'date': date, 'amount': None, 'percent': percent[1], 'percent_base': 'initial_face_value' if 'первоначальн' in lower else 'unknown'}
    elif code == 'covenants':
        return {'condition': clean, 'test_date_rule': None, 'consequence': None, 'exceptions': None}
    elif code == 'holder_early_redemption':
        return {'trigger': clean, 'deadline': None, 'exceptions': None}
    elif code == 'issuer_call_option':
        return {'trigger': clean, 'date_rule': None, 'price_rule': None, 'notice_rule': None}
    elif code == 'guarantee_security':
        kind = 'surety' if 'поручительств' in lower else 'pledge' if 'залог' in lower else 'third_party_offer'
        return {'type': kind, 'party': None, 'scope': clean, 'expiry': None, 'exceptions': None}
    elif code == 'cross_default':
        return {'trigger': clean, 'threshold': None, 'entities': None, 'grace_period': None, 'consequence': None, 'exceptions': None}
    elif code == 'issuer_restrictions':
        return {'action': clean, 'restriction': clean, 'exceptions': None, 'consequence': None}
    elif code == 'offer_procedure':
        return {'offer_date': None, 'window_start': None, 'window_end': None, 'window_rule': None, 'via': None, 'submission': clean, 'settlement_rule': None}
    return None

