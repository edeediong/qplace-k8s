"""Exact Kubernetes resource.Quantity parsing.

Grammar (k8s.io/apimachinery/pkg/api/resource):

    <quantity>        ::= <signedNumber><suffix>
    <suffix>          ::= <binarySI> | <decimalExponent> | <decimalSI>
    <binarySI>        ::= Ki | Mi | Gi | Ti | Pi | Ei
    <decimalSI>       ::= m | "" | k | M | G | T | P | E   (plus n, u accepted by the parser)
    <decimalExponent> ::= "e" <signedNumber> | "E" <signedNumber>

Values are converted with ``fractions.Fraction`` so no precision is lost, then
rounded *up* (away from zero) exactly like ``Quantity.MilliValue()`` and
``Quantity.Value()``. A positive fractional request is therefore never rounded
down to zero.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

from qplace.diagnostics import QuantityError, Reason

_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
_QUANTITY_RE = re.compile(rf"^(?P<number>{_NUMBER})(?P<suffix>[A-Za-z]*(?:[+-]?[0-9]+)?)$")

_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_DECIMAL = {
    "n": Fraction(1, 10**9),
    "u": Fraction(1, 10**6),
    "m": Fraction(1, 10**3),
    "": Fraction(1),
    "k": Fraction(10**3),
    "M": Fraction(10**6),
    "G": Fraction(10**9),
    "T": Fraction(10**12),
    "P": Fraction(10**15),
    "E": Fraction(10**18),
}
# Bounds work on hostile input; real resource quantities are far inside this range.
MAX_EXPONENT = 64
_EXPONENT_RE = re.compile(r"^[eE](?P<exp>[+-]?[0-9]+)$")


def parse_quantity(raw: object) -> Fraction:
    """Parse a Kubernetes quantity into an exact, non-negative Fraction of base units."""
    if isinstance(raw, bool):
        raise QuantityError(raw, Reason.INVALID_QUANTITY, f"quantity must not be a boolean: {raw!r}")
    if isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, float):
        if not math.isfinite(raw):
            raise QuantityError(raw, Reason.INVALID_QUANTITY, f"non-finite quantity: {raw!r}")
        text = repr(raw)
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        raise QuantityError(raw, Reason.INVALID_QUANTITY, f"unsupported quantity type: {type(raw).__name__}")

    match = _QUANTITY_RE.match(text)
    if not match:
        raise QuantityError(raw, Reason.INVALID_QUANTITY, f"invalid quantity: {raw!r}")
    number = Fraction(match.group("number"))
    suffix = match.group("suffix")

    if suffix in _BINARY:
        multiplier: Fraction = Fraction(_BINARY[suffix])
    elif suffix in _DECIMAL:
        multiplier = _DECIMAL[suffix]
    elif exp := _EXPONENT_RE.match(suffix):
        exponent = int(exp.group("exp"))
        if abs(exponent) > MAX_EXPONENT:
            raise QuantityError(raw, Reason.INVALID_QUANTITY, f"quantity exponent out of supported range: {raw!r}")
        multiplier = Fraction(10) ** exponent
    else:
        raise QuantityError(raw, Reason.INVALID_QUANTITY, f"invalid quantity suffix {suffix!r} in {raw!r}")

    value = number * multiplier
    if value < 0:
        raise QuantityError(raw, Reason.NEGATIVE_QUANTITY, f"negative quantity not allowed: {raw!r}")
    return value


def _ceil(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def parse_cpu_millicores(raw: object) -> int:
    """CPU quantity -> integer millicores, rounded up (Quantity.MilliValue semantics)."""
    return _ceil(parse_quantity(raw) * 1000)


def parse_memory_bytes(raw: object) -> int:
    """Memory quantity -> integer bytes, rounded up (Quantity.Value semantics)."""
    return _ceil(parse_quantity(raw))


def parse_count(raw: object) -> int:
    """Integer-valued quantity such as allocatable ``pods``, rounded up."""
    return _ceil(parse_quantity(raw))
