import asyncio

import pytest

from app.tools.calculator import CalculatorInput, CalculatorTool
from app.tools.registry import ToolContext


def run_calculator(expression: str):
    return asyncio.run(
        CalculatorTool().run(
            CalculatorInput(expression=expression),
            ToolContext(),
        )
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("-1 + +2", 1),
        ("1.5 + 2.25", 3.75),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("2 ** 3", 8),
    ],
)
def test_calculator_evaluates_supported_arithmetic(expression, expected):
    result = run_calculator(expression)

    assert result.ok is True
    assert result.content == str(expected)
    assert result.data == {"result": expected}
    assert result.error is None


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os")',
        'open("x")',
        "foo + 1",
        "(1).__class__",
        "[1, 2, 3]",
        '{"x": 1}',
        "1 < 2",
    ],
)
def test_calculator_rejects_unsafe_or_unsupported_expressions(expression):
    result = run_calculator(expression)

    assert result.ok is False
    assert result.error == "calculation_failed"
    assert "Calculation failed:" in result.content


def test_calculator_returns_failure_for_division_by_zero():
    result = run_calculator("1 / 0")

    assert result.ok is False
    assert result.error == "calculation_failed"
    assert "division by zero" in result.content


def test_calculator_rejects_unbounded_exponentiation():
    result = run_calculator("9 ** 99999999")

    assert result.ok is False
    assert result.error == "calculation_failed"
    assert "power exponent is too large" in result.content


def test_calculator_rejects_oversized_results():
    result = run_calculator("1000000 * 10000000")

    assert result.ok is False
    assert result.error == "calculation_failed"
    assert "result is too large" in result.content
