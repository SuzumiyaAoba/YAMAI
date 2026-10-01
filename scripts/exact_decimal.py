"""Compact JSON decimal values outside the host Decimal exponent range.

Only parsing, numeric comparison and schema divisibility are needed here;
this is deliberately not an arithmetic context. Coefficients and signed
exponents stay decimal strings, bounded by the input's length. No power of
ten is expanded and no unbounded exponent is converted to a Python int.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import total_ordering
from numbers import Number


def exponent_compare(left: str, right: str) -> int:
    """Compare canonical signed integer strings without parsing them."""
    ln, rn = left.startswith("-"), right.startswith("-")
    if ln != rn:
        return -1 if ln else 1
    a, b = left.lstrip("-"), right.lstrip("-")
    result = (len(a) > len(b)) - (len(a) < len(b)) or (a > b) - (a < b)
    return -result if ln else result


def _canonical_exponent(raw: str) -> str:
    digits = raw.lstrip("+-").lstrip("0") or "0"
    return ("-" if raw.startswith("-") and digits != "0" else "") + digits


def exponent_add_small(value: str, offset: int) -> str:
    """Add an input-length-sized offset, carrying over exponent digits only."""
    if not offset:
        return value
    negative = value.startswith("-")
    digits = value.lstrip("-")
    delta = -offset if negative else offset
    # A short exponent can be handled by bounded native integer arithmetic.
    if len(digits) <= len(str(abs(offset))):
        return str(int(value) + offset)
    out = list(digits)
    index = len(out) - 1
    carry = delta
    while carry:
        carry, digit = divmod(int(out[index]) + carry, 10)
        out[index] = str(digit)
        index -= 1
        if index < 0:
            if carry:
                out.insert(0, str(carry))
            break
    result = "".join(out).lstrip("0") or "0"
    return ("-" if negative and result != "0" else "") + result


def bounded_exponent_difference(left: str, right: str, bound: int) -> int:
    """Return min(left - right, bound), for left >= right and bound >= 1."""
    if exponent_compare(left, exponent_add_small(right, bound)) >= 0:
        return bound
    # The difference is now smaller than bound. Residues recover it exactly
    # without converting either potentially million-digit exponent.
    width = len(str(bound)) + 1
    modulus = 10 ** width
    def residue(value):
        return (-1 if value.startswith("-") else 1) * int(value.lstrip("-")[-width:])
    return (residue(left) - residue(right)) % modulus


@total_ordering
@dataclass(frozen=True, eq=False)
class ExactDecimal(Number):
    sign: int
    coefficient: str
    exponent: str

    def is_finite(self) -> bool:
        return True

    def canonical(self) -> str:
        return ("-" if self.sign else "") + self.coefficient + "e" + self.exponent

    def __eq__(self, other):
        if isinstance(other, bool) or not isinstance(other, (int, float, Decimal, ExactDecimal)):
            return NotImplemented
        return compare_numbers(self, other) == 0

    def __lt__(self, other):
        if isinstance(other, bool) or not isinstance(other, (int, float, Decimal, ExactDecimal)):
            return NotImplemented
        return compare_numbers(self, other) < 0

    __hash__ = None


def number_parts(value) -> tuple[int, str, str]:
    """Return normalized sign, coefficient and exponent for a finite number."""
    if isinstance(value, ExactDecimal):
        return value.sign, value.coefficient, value.exponent
    number = value if isinstance(value, Decimal) else Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    sign, digits, exponent = number.as_tuple()
    coefficient = "".join(map(str, digits)).lstrip("0")
    if not coefficient:
        return 0, "0", "0"
    trimmed = coefficient.rstrip("0")
    return sign, trimmed, str(exponent + len(coefficient) - len(trimmed))


def compare_numbers(left, right) -> int:
    ls, lc, le = number_parts(left)
    rs, rc, re = number_parts(right)
    if lc == "0" or rc == "0":
        if lc == rc:
            return 0
        return (-1 if rs == 0 else 1) if lc == "0" else (-1 if ls else 1)
    if ls != rs:
        return -1 if ls else 1
    result = exponent_compare(exponent_add_small(le, len(lc)), exponent_add_small(re, len(rc)))
    if not result:
        width = max(len(lc), len(rc))
        a, b = lc.ljust(width, "0"), rc.ljust(width, "0")
        result = (a > b) - (a < b)
    return -result if ls else result


def parse_real(raw: str, max_integer: int):
    """Parse a JSON real token, retaining exact value rather than host bounds."""
    mantissa, marker, exponent = raw.lower().partition("e")
    exponent = _canonical_exponent(exponent) if marker else "0"
    sign = int(mantissa.startswith("-"))
    mantissa = mantissa.lstrip("-")
    whole, dot, fraction = mantissa.partition(".")
    coefficient = (whole + fraction).lstrip("0")
    if not coefficient:
        return 0  # In particular, zero never needs its exponent interpreted.
    trimmed = coefficient.rstrip("0")
    exponent = exponent_add_small(exponent, len(coefficient) - len(trimmed) - len(fraction))
    coefficient = trimmed
    if not exponent.startswith("-"):
        limit_digits = len(str(max_integer))
        if len(coefficient) > limit_digits or exponent_compare(exponent, str(limit_digits - len(coefficient))) > 0:
            raise ValueError("integer outside IEEE-754 safe range")
        integer = int(coefficient) * 10 ** int(exponent)
        if integer > max_integer:
            raise ValueError("integer outside IEEE-754 safe range")
        return -integer if sign else integer
    token = ("-" if sign else "") + coefficient + "e" + exponent
    try:
        number = Decimal(token)
        if number.is_finite():
            return number
    except InvalidOperation:
        pass
    # A finite fraction remains valid even when its exponent cannot fit
    # Decimal's platform C integer. Keep the same normalized JSON value,
    # including when the caller disabled Decimal's InvalidOperation trap.
    return ExactDecimal(sign, coefficient, exponent)
