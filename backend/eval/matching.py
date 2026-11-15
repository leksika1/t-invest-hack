"""Deterministic, pre-registered matching; no model judges its own answer."""
from decimal import Decimal
import re

from app.cards.verification import canonical, normalized_text


def normal(value):
    if isinstance(value, str) and value in ('SUR', 'RUR', 'RUB'):
        return 'rub'
    if isinstance(value, dict):
        return {key: normal(v) for key, v in sorted(value.items()) if v is not None}
    if isinstance(value, list):
        return sorted((normal(v) for v in value), key=repr)
    return canonical(value)


def expand_rates(value):
    output = {}
    for period in value.get('periods', []):
        if period['to'] - period['from'] > 1000:
            return None
        for number in range(period['from'], period['to'] + 1):
            if number in output:
                return None
            output[number] = {k: v for k, v in period.items() if k not in ('from', 'to')}
    return output


def value_equal(code, left, right):
    if left is None or right is None:
        return left is right
    if code == 'coupon_type':
        return left.get('type') == right.get('type')  # Descriptive prose is not a coupon type.
    if code == 'coupon_rate':
        a, b = expand_rates(left), expand_rates(right)
        return a is not None and b is not None and normal(a) == normal(b) and normal(left.get('formula')) == normal(right.get('formula'))
    return normal(left) == normal(right)


def leaf_signature(text):
    text = normalized_text(text)
    numbers = sorted(str(Decimal(n.replace(',', '.')).normalize()) for n in re.findall(r'\d+(?:[.,]\d+)?', text))
    # All semantic operators/exceptions must be preserved or explicitly approved as aliases.
    operators = sorted(re.findall(r'\b(?:не|нет|без|кроме|исключая|исключением|более|менее|свыше|превышает)\b', text))
    return numbers, operators


def atom_equal(expected, predicted, atom):
    def compare(left, right, path=''):
        if isinstance(left, dict):
            if not isinstance(right, dict) or {k for k, v in left.items() if v is not None} != {k for k, v in right.items() if v is not None}:
                return False
            return all(compare(v, right.get(k), f'{path}.{k}'.strip('.')) for k, v in left.items() if v is not None)
        if normal(left) == normal(right):
            return True
        groups = atom.constraints.get(path)
        if not groups or not isinstance(left, str) or not isinstance(right, str) or leaf_signature(left) != leaf_signature(right):
            return False
        text = normalized_text(right)
        return all(any(normalized_text(phrase) in text for phrase in group) for group in groups)
    return any(compare(variant, predicted) for variant in [expected, *atom.alternatives])


def maximum_matching(edges):
    """Maximum one-to-one matching, so duplicates cannot inflate recall."""
    matched = {}
    def visit(expected, seen):
        for predicted in edges[expected]:
            if predicted in seen:
                continue
            seen.add(predicted)
            if predicted not in matched or visit(matched[predicted], seen):
                matched[predicted] = expected
                return True
        return False
    for expected in range(len(edges)):
        visit(expected, set())
    return {expected: predicted for predicted, expected in matched.items()}
