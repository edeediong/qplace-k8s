"""Quantity parsing against k8s.io/apimachinery resource.Quantity semantics."""

from fractions import Fraction

import pytest

from qplace.diagnostics import QuantityError, Reason
from qplace.quantity import parse_cpu_millicores, parse_memory_bytes, parse_quantity


@pytest.mark.parametrize(
    ("raw", "millicores"),
    [
        ("1", 1000),  # integer CPU
        (2, 2000),  # JSON integer form
        ("0.5", 500),  # fractional CPU
        ("1.25", 1250),
        ("500m", 500),  # millicores
        ("100m", 100),
        ("2k", 2_000_000),
        ("1e3", 1_000_000),  # decimal exponent: 1000 cores
    ],
)
def test_cpu_quantities(raw, millicores):
    assert parse_cpu_millicores(raw) == millicores


@pytest.mark.parametrize(
    ("raw", "nbytes"),
    [
        ("1Gi", 1024**3),  # binary SI
        ("512Mi", 512 * 1024**2),
        ("1.5Gi", 1536 * 1024**2),
        ("64Ki", 65536),
        ("1000M", 1_000_000_000),  # decimal SI: M = 10^6, not 2^20
        ("1G", 10**9),
        ("1k", 1000),  # lowercase k is decimal kilo
        ("128974848", 128974848),  # plain bytes
        ("129e6", 129_000_000),  # decimal exponent
        ("1E", 10**18),  # bare E is exa, not an exponent
        ("1E3", 1000),  # E followed by digits is an exponent
    ],
)
def test_memory_quantities(raw, nbytes):
    assert parse_memory_bytes(raw) == nbytes


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "1 Gi", "1KB", "1K", "Gi", "1Gb", "--1", True, None, [1]])
def test_invalid_quantities_rejected(raw):
    with pytest.raises(QuantityError) as exc:
        parse_quantity(raw)
    assert exc.value.reason is Reason.INVALID_QUANTITY


@pytest.mark.parametrize("raw", ["-1", "-500m", "-1Gi"])
def test_negative_quantities_rejected(raw):
    with pytest.raises(QuantityError) as exc:
        parse_quantity(raw)
    assert exc.value.reason is Reason.NEGATIVE_QUANTITY


def test_positive_fraction_rounds_up_never_to_zero():
    # Quantity.MilliValue()/Value() round away from zero: 0.1m CPU -> 1m, 0.5 byte -> 1 byte.
    assert parse_cpu_millicores("0.1m") == 1
    assert parse_cpu_millicores("1500u") == 2  # 1.5m -> 2m
    assert parse_cpu_millicores("1n") == 1
    assert parse_memory_bytes("0.5") == 1
    assert parse_memory_bytes("1.0000001Ki") == 1025


def test_parsing_is_exact():
    # 0.1 + 0.2 style float error must not appear: 0.3 CPU is exactly 300m.
    assert parse_quantity("0.3") == Fraction(3, 10)
    assert parse_cpu_millicores("0.3") == 300
    assert parse_memory_bytes("0.1Gi") == 107374183  # ceil(107374182.4)


def test_huge_exponent_rejected_quickly():
    # Unbounded exponents would make Fraction arithmetic effectively hang on hostile input.
    for raw in ("1e-99999999", "1e99999999"):
        with pytest.raises(QuantityError) as exc:
            parse_quantity(raw)
        assert exc.value.reason is Reason.INVALID_QUANTITY
