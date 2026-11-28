"""The gate computes with whole numbers only, so a decision is the same anywhere.

A comparison made with a decimal fraction fails on no one machine, so no
test of a decision can catch it. These tests read the files' code instead.
"""

import ast
from pathlib import Path

import pytest

import lamplighter.bench

FOLDER = Path(lamplighter.bench.__file__).parent
GATE_FILES = ["gate.py", "sign_flip.py", "interval.py", "gate_rule.py"]
REFUSED_MODULES = {"fractions", "decimal", "random", "time", "math"}


def _fractional(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Constant) and isinstance(node.value, float | complex):
            found.append(f"{where}: the decimal number {node.value!r}")
        if isinstance(node, ast.BinOp | ast.AugAssign) and isinstance(node.op, ast.Div):
            found.append(f"{where}: a division that gives a decimal fraction")
        if isinstance(node, ast.Call) and _called(node) in ("float", "round"):
            found.append(f"{where}: a call of {_called(node)}")
        found.extend(f"{where}: {i}" for i in _refused_imports(node))
    return found


def _called(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else None


def _refused_imports(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"an import of {alias.name}"
            for alias in node.names
            if alias.name.split(".")[0] in REFUSED_MODULES
        ]
    if isinstance(node, ast.ImportFrom) and node.module in REFUSED_MODULES:
        allowed = {"isqrt"} if node.module == "math" else set[str]()
        return [
            f"an import of {node.module}.{alias.name}"
            for alias in node.names
            if alias.name not in allowed
        ]
    return []


@pytest.mark.parametrize("name", GATE_FILES)
def test_a_gate_file_holds_no_decimal_fraction(name: str) -> None:
    tree = ast.parse((FOLDER / name).read_text(encoding="utf-8"))
    assert _fractional(tree) == []


@pytest.mark.parametrize(
    "line",
    [
        "x = 0.05",
        "x = a / b",
        "x /= 2",
        "x = float(a)",
        "x = round(a)",
        "import math",
        "from math import sqrt",
        "from fractions import Fraction",
        "import decimal",
        "from random import random",
        "import time",
    ],
)
def test_the_check_finds_each_kind_of_fraction(line: str) -> None:
    assert _fractional(ast.parse(line)) != []


@pytest.mark.parametrize(
    "line", ["x = a // b", "x //= 2", "from math import isqrt", "x = 1_000"]
)
def test_the_check_lets_whole_numbers_pass(line: str) -> None:
    assert _fractional(ast.parse(line)) == []
