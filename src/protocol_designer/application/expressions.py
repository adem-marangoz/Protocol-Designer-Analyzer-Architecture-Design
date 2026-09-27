"""Test variables: ``${name}`` substitution and safe integer arithmetic.

Only literals, variables and arithmetic/bitwise operators are allowed, so a
protocol file can never execute arbitrary code.
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Any, Dict

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}


class ExpressionError(ValueError):
    pass


def substitute(value: Any, variables: Dict[str, Any]) -> Any:
    """Replace ``${name}`` references. A lone reference keeps the variable's type."""
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if not isinstance(value, str):
        return value
    whole = _VAR.fullmatch(value.strip())
    if whole:
        return _lookup(whole.group(1), variables)
    return _VAR.sub(lambda m: _format(_lookup(m.group(1), variables)), value)


def _lookup(name: str, variables: Dict[str, Any]) -> Any:
    if name not in variables:
        raise ExpressionError(f"undefined variable '{name}'")
    return variables[name]


def _format(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return " ".join(f"{b:02X}" for b in value)
    return str(value)


def evaluate(text: Any, variables: Dict[str, Any]) -> Any:
    """Evaluate ``"${seed} ^ 0x5A5A5A5A"`` style expressions."""
    if not isinstance(text, str):
        return text
    expr = _VAR.sub(lambda m: m.group(1), text).strip()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return substitute(text, variables)  # plain text value
    if isinstance(tree.body, ast.Name) and tree.body.id not in variables and "${" not in text:
        return text  # a bare word such as an enum label
    try:
        return _eval(tree.body, variables)
    except ExpressionError:
        raise
    except (TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ExpressionError(f"cannot evaluate '{text}': {exc}") from None


def _eval(node: ast.AST, variables: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return _lookup(node.id, variables)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, variables), _eval(node.right, variables))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, variables))
    raise ExpressionError(f"unsupported expression element: {type(node).__name__}")
