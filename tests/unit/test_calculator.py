from decimal import ROUND_UP, localcontext

import pytest

from min_agent.tools.calculator import CALCULATOR_TOOL, calculate
from min_agent.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+2", "3"),
        ("8-3", "5"),
        ("2*3", "6"),
        ("7/2", "3.5"),
        ("1+2*3", "7"),
        ("(1+2)*3", "9"),
        ("8/2/2", "2"),
        (" (2 + 3) * -4 ", "-20"),
        ("--2", "2"),
        ("+(2)", "2"),
        ("1--2", "3"),
        (".5+1.", "1.5"),
        ("0.1+0.2", "0.3"),
        ("1/3", "0." + "3" * 28),
        ("2/3", "0." + "6" * 27 + "7"),
        ("0.1234567890123456789012345678", "0.1234567890123456789012345678"),
        ("12345678901234567890123456785", "12345678901234567890123456780"),
        ("12345678901234567890123456795", "12345678901234567890123456800"),
        ("1.20+0.30", "1.5"),
        ("1000", "1000"),
        ("-0.000", "0"),
        ("0." + "0" * 200, "0"),
    ],
)
def test_supported_arithmetic_returns_decimal_text(expression, expected):
    result = calculate(expression)

    assert isinstance(result, str)
    assert result == expected


@pytest.mark.parametrize("expression", ["1/0", "1/(2-2)", "0/0"])
def test_division_by_zero_reports_an_error(expression):
    with pytest.raises(ValueError, match="零"):
        calculate(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "", " ", "\n", "1+", "(1+2", "1..2", "1 2",
        "2**3", "3//2", "1%2", "abs(-2)", "__import__('os')",
        "(1).__class__", "True", "None", "'2'", "[1]",
        "1e3", "0x10", "1_000", "1 # comment", "1;2", "１２+３",
    ],
)
def test_unsupported_or_invalid_expression_is_rejected(expression):
    with pytest.raises(ValueError):
        calculate(expression)


@pytest.mark.parametrize("expression", [None, True, 123])
def test_expression_must_be_a_string(expression):
    with pytest.raises(ValueError):
        calculate(expression)


def test_raw_input_length_boundary():
    assert calculate("1" + " " * 511) == "1"

    with pytest.raises(ValueError):
        calculate("1" + " " * 512)


def test_ast_node_boundary():
    assert calculate("+" * 63 + "1") == "1"

    with pytest.raises(ValueError, match="节点"):
        calculate("+" * 64 + "1")


def test_numeric_magnitude_boundaries_are_supported():
    largest_power = "1" + "0" * 100
    smallest_power = "0." + "0" * 99 + "1"

    assert calculate(largest_power) == largest_power
    assert calculate(smallest_power) == smallest_power


@pytest.mark.parametrize(
    "expression",
    [
        "1" + "0" * 101,
        "1" + "0" * 100 + "*10",
        "0." + "0" * 100 + "1",
        "0." + "0" * 99 + "1/10",
    ],
)
def test_out_of_range_literals_and_intermediate_results_are_rejected(expression):
    with pytest.raises(ValueError, match="范围"):
        calculate(expression)


def test_global_decimal_context_does_not_affect_the_calculation():
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_UP
        before_flags = dict(ambient.flags)

        assert calculate("1/3") == "0." + "3" * 28
        assert calculate("0.1+0.2") == "0.3"
        assert ambient.prec == 3
        assert ambient.rounding == ROUND_UP
        assert ambient.flags == before_flags


def test_file_access_expression_is_rejected_without_creating_a_file(tmp_path):
    target = tmp_path / "must-not-be-created.txt"

    with pytest.raises(ValueError):
        calculate(f"open({str(target)!r}, 'w')")

    assert not target.exists()


def test_calculator_definition_can_be_registered_and_called_explicitly():
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    tool = registry.get("calculator")
    arguments = tool.parameters.model_validate({"expression": "0.1+0.2"})

    assert tool.handler(**arguments.model_dump()) == "0.3"
    assert registry.schemas()[0]["parameters"]["properties"]["expression"]["maxLength"] == 512
