"""Controlled, auditable local tools.

Phase 22 (post-v1) starts the tool layer for research workflows: small,
deterministic, side-effect-free utilities the app can run on demand. Nothing is
executed via `eval`; every tool validates its arguments and raises `ToolError`
on anything unexpected, so a caller can never smuggle code through.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from datetime import date
from typing import NotRequired, TypedDict

MATH_ERROR = "invalid expression"


class ToolError(Exception):
    """Recoverable tool failure — bad name, bad args, or a refused operation."""


# ---------- calculator (AST-restricted arithmetic) ----------

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPONENT = 1000
_MAX_RESULT = 1e18


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):  # True/False are ints in Python
            raise ToolError(MATH_ERROR)
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError("exponent too large")
        result = _BINOPS[type(node.op)](left, right)
        if isinstance(result, complex) or abs(result) > _MAX_RESULT:
            raise ToolError("result out of range")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_node(node.operand))
    raise ToolError(MATH_ERROR)


def calculator(expression: str) -> dict:
    """Evaluate an arithmetic expression using only + - * / // % ** and parens."""
    if not isinstance(expression, str) or not expression.strip():
        raise ToolError("expression is required")
    if len(expression) > 200:
        raise ToolError("expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"{MATH_ERROR}: {e.msg}") from e
    try:
        result = _eval_node(tree)
    except ZeroDivisionError as e:
        raise ToolError("division by zero") from e
    return {"expression": expression.strip(), "result": result}


# ---------- date arithmetic ----------


def days_between(start: str, end: str) -> dict:
    """Whole days from `start` to `end` (both ISO dates, e.g. 1969-07-20)."""
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as e:
        raise ToolError("start and end must be ISO dates (YYYY-MM-DD)") from e
    return {"start": start, "end": end, "days": (end_date - start_date).days}


# ---------- unit conversion ----------

_FACTORS = {
    "length": {
        "m": 1.0,
        "km": 1000.0,
        "cm": 0.01,
        "mm": 0.001,
        "mi": 1609.344,
        "ft": 0.3048,
        "in": 0.0254,
        "yd": 0.9144,
    },
    "mass": {"g": 1.0, "kg": 1000.0, "mg": 0.001, "lb": 453.59237, "oz": 28.349523125},
    "data": {"b": 1.0, "kb": 1024.0, "mb": 1024.0**2, "gb": 1024.0**3, "tb": 1024.0**4},
}
_TEMPERATURE = ("c", "f", "k")


def _to_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return (value - 32) * 5 / 9
    return value - 273.15


def _from_celsius(value: float, unit: str) -> float:
    if unit == "c":
        return value
    if unit == "f":
        return value * 9 / 5 + 32
    return value + 273.15


def convert(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert a value between length, mass, data, or temperature units."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as e:
        raise ToolError("value must be a number") from e
    src = str(from_unit).strip().lower()
    dst = str(to_unit).strip().lower()
    if src in _TEMPERATURE and dst in _TEMPERATURE:
        result = _from_celsius(_to_celsius(amount, src), dst)
    else:
        family = next((f for f, units in _FACTORS.items() if src in units and dst in units), None)
        if family is None:
            raise ToolError(f"cannot convert {from_unit!r} to {to_unit!r}")
        result = amount * _FACTORS[family][src] / _FACTORS[family][dst]
    return {"value": amount, "from": src, "to": dst, "result": result}


# ---------- text ----------


def word_count(text: str) -> dict:
    """Count whitespace-separated words in a bounded string."""
    if not isinstance(text, str):
        raise ToolError("text must be a string")
    if len(text) > 200_000:
        raise ToolError("text is too long")
    words = text.split()
    return {"words": len(words), "characters": len(text)}


class _ToolSpec(TypedDict):
    description: str
    args: dict[str, str]
    run: Callable[..., dict]
    contextual: NotRequired[bool]  # needs a {sources: [...]} context (research)


def _source_rows(context: dict | None) -> list[dict]:
    rows = (context or {}).get("sources")
    return rows if isinstance(rows, list) else []


def count_in_sources(args: dict, context: dict) -> dict:
    """Count case-insensitive occurrences of a term across the notebook sources."""
    term = args.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ToolError("term is required")
    needle = term.lower()
    total = matched = 0
    for row in _source_rows(context):
        hits = str(row.get("text", "")).lower().count(needle)
        if hits:
            total += hits
            matched += 1
    return {"term": term, "count": total, "sources_with_matches": matched}


def find_in_sources(args: dict, context: dict) -> dict:
    """Return short passages containing a term across the notebook sources."""
    term = args.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ToolError("term is required")
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError) as e:
        raise ToolError("limit must be an integer") from e
    limit = max(1, min(limit, 20))
    needle = term.lower()
    matches = []
    for row in _source_rows(context):
        text = str(row.get("text", ""))
        found = text.lower().find(needle)
        if found == -1:
            continue
        start, end = max(0, found - 80), min(len(text), found + len(term) + 80)
        matches.append(
            {
                "source": row.get("source_name", ""),
                "page": row.get("page"),
                "snippet": text[start:end],
            }
        )
        if len(matches) >= limit:
            break
    return {"term": term, "matches": matches}


TOOLS: dict[str, _ToolSpec] = {
    "calculator": {
        "description": "Evaluate arithmetic: + - * / // % ** and parentheses.",
        "args": {"expression": "string"},
        "run": calculator,
    },
    "days_between": {
        "description": "Whole days between two ISO dates (YYYY-MM-DD).",
        "args": {"start": "string", "end": "string"},
        "run": days_between,
    },
    "convert": {
        "description": (
            "Convert units: length (m/km/cm/mm/mi/ft/in/yd), mass "
            "(g/kg/mg/lb/oz), data (b/kb/mb/gb/tb), temperature (c/f/k)."
        ),
        "args": {"value": "number", "from_unit": "string", "to_unit": "string"},
        "run": convert,
    },
    "word_count": {
        "description": "Count words and characters in a short piece of text.",
        "args": {"text": "string"},
        "run": word_count,
    },
    "count_in_sources": {
        "description": "Count occurrences of a term across the notebook's sources.",
        "args": {"term": "string"},
        "run": count_in_sources,
        "contextual": True,
    },
    "find_in_sources": {
        "description": "Find short passages containing a term across the notebook's sources.",
        "args": {"term": "string", "limit": "integer (default 5)"},
        "run": find_in_sources,
        "contextual": True,
    },
}


def list_tools() -> list[dict]:
    """Tool catalog for clients (name, description, argument shapes, scope)."""
    return [
        {
            "name": name,
            "description": tool["description"],
            "args": tool["args"],
            "scope": "sources" if tool.get("contextual") else "local",
        }
        for name, tool in TOOLS.items()
    ]


def run_tool(name: str, args: dict | None = None, context: dict | None = None):
    """Run a named tool, validating the call.

    Contextual (source-scoped) tools require a `{sources: [...]}` context, which
    only the research path supplies; the bare API refuses them with a clear 422.
    """
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("args must be an object")
    unexpected = set(args) - set(tool["args"])
    if unexpected:
        raise ToolError(f"unexpected argument(s): {', '.join(sorted(unexpected))}")
    try:
        if tool.get("contextual"):
            if context is None:
                raise ToolError(f"{name} is available only inside a research answer")
            return tool["run"](args, context)
        return tool["run"](**args)
    except ToolError:
        raise
    except TypeError as e:
        raise ToolError(f"invalid arguments: {e}") from e
