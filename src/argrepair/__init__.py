"""Turn malformed or invalid tool-call arguments into a message the model can act on.

A model that writes ``{'path': 'a.txt',}`` or sends ``count: "10"`` when the tool wants an
integer does not need a stack trace, it needs to be told what was wrong, in one sentence,
and given another turn. Doing that without an exception loop is the whole job.

    >>> from argrepair import check, feedback, parse_args
    >>> args = parse_args("{'path': 'a.txt',}")
    >>> args
    {'path': 'a.txt'}
    >>> schema = {"path": {"type": "string", "required": True}, "bytes": {"type": "integer"}}
    >>> problems = check(args, schema)
    >>> problems
    [ArgProblem(field='bytes', code='missing', message="'bytes' is required")]
    >>> print(feedback(problems, tool="read_file"))
    The arguments for `read_file` were rejected:
    - 'bytes' is required
    Send the call again with corrected arguments.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ArgProblem",
    "check",
    "coerce",
    "feedback",
    "parse_args",
    "parse_and_check",
    "repair_text",
]

__version__ = "0.1.0"

_SINGLE_QUOTED = re.compile(r"'([^'\\]*(?:\\.[^'\\]*)*)'")
_PYTHON_LITERALS = (
    (re.compile(r"\bTrue\b"), "true"),
    (re.compile(r"\bFalse\b"), "false"),
    (re.compile(r"\bNone\b"), "null"),
    (re.compile(r"\bNaN\b"), "null"),
    (re.compile(r"\bInfinity\b"), "null"),
)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_COMMENT = re.compile(r"//[^\n\r]*")
_SMART_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, (list, tuple)),
    "null": lambda value: value is None,
}


@dataclass(frozen=True)
class ArgProblem:
    """One thing wrong with the arguments of a call."""

    field: str
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def repair_text(text: str) -> str:
    """Best-effort rewrite of almost-JSON into JSON: smart quotes, Python literals,
    single quotes, trailing commas and ``//`` comments."""
    if not isinstance(text, str):
        raise TypeError(f"expected a string, got {type(text).__name__}")
    repaired = text.translate(_SMART_QUOTES).strip()
    if repaired.startswith("```"):
        repaired = re.sub(r"^```[a-zA-Z]*\s*", "", repaired)
        repaired = re.sub(r"\s*```$", "", repaired)
    repaired = _COMMENT.sub("", repaired)
    for pattern, replacement in _PYTHON_LITERALS:
        repaired = pattern.sub(replacement, repaired)
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    if '"' not in repaired:
        repaired = _SINGLE_QUOTED.sub(r'"\1"', repaired)
    return repaired.strip()


def parse_args(text: str | bytes | dict | None) -> dict:
    """Parse tool arguments leniently and return a dict.

    Accepts a dict as-is, JSON, almost-JSON and a Python literal such as
    ``{'path': 'a.txt', 'mode': None}``. Raises :class:`ValueError` when nothing works,
    because at that point a repair message is not possible and the model must rewrite.
    """
    if text is None:
        return {}
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    if isinstance(text, dict):
        return dict(text)
    if not isinstance(text, str):
        raise ValueError(f"cannot read arguments from {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        return {}
    for candidate in (stripped, repair_text(stripped)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            raise ValueError(f"arguments must be an object, not {type(parsed).__name__}")
        return parsed
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    raise ValueError("arguments are not readable as JSON or Python literals")


def coerce(value: Any, type_name: str) -> Any:
    """Convert a value to the declared type where that is unambiguous.

    ``"10"`` becomes ``10`` for ``integer``, ``"true"``/``"yes"``/``1`` become booleans,
    and a bare value becomes a one-item array. Anything else is returned unchanged, so
    the caller still sees the validation error.
    """
    if type_name == "integer" and isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    if type_name == "number" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return value
    if type_name == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
        return value
    if type_name == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if type_name == "array" and isinstance(value, (str, int, float, bool)):
        return [value]
    return value


def check(arguments: dict, schema: dict[str, dict] | None) -> list[ArgProblem]:
    """Validate arguments against a small schema and list what is wrong.

    A schema is ``{"field": {"type": ..., "required": ..., "enum": ..., "minimum": ...,
    "maximum": ..., "items": {...}, "default": ...}}``. ``type`` is one of ``string``,
    ``integer``, ``number``, ``boolean``, ``object``, ``array``, ``null``.
    """
    if schema is None:
        return []
    if not isinstance(arguments, dict):
        return [
            ArgProblem("", "type", f"arguments must be an object, not {type(arguments).__name__}")
        ]
    problems: list[ArgProblem] = []
    for field, spec in schema.items():
        spec = spec or {}
        present = field in arguments
        if not present:
            if spec.get("required"):
                problems.append(ArgProblem(field, "missing", f"'{field}' is required"))
            continue
        value = arguments[field]
        expected = spec.get("type")
        if expected and expected in _TYPE_CHECKS and not _TYPE_CHECKS[expected](value):
            problems.append(
                ArgProblem(field, "type", f"'{field}' must be {expected}, not {_type_name(value)}")
            )
            continue
        if "enum" in spec and value not in spec["enum"]:
            allowed = ", ".join(json.dumps(item) for item in spec["enum"])
            problems.append(
                ArgProblem(field, "enum", f"'{field}' must be one of {allowed}, not {value!r}")
            )
        if "minimum" in spec and _is_number(value) and value < spec["minimum"]:
            problems.append(
                ArgProblem(field, "minimum", f"'{field}' must be at least {spec['minimum']}")
            )
        if "maximum" in spec and _is_number(value) and value > spec["maximum"]:
            problems.append(
                ArgProblem(field, "maximum", f"'{field}' must be at most {spec['maximum']}")
            )
        items = spec.get("items")
        if items and isinstance(value, (list, tuple)):
            for position, item in enumerate(value):
                item_type = items.get("type")
                if item_type in _TYPE_CHECKS and not _TYPE_CHECKS[item_type](item):
                    problems.append(
                        ArgProblem(
                            f"{field}[{position}]",
                            "type",
                            f"'{field}[{position}]' must be {item_type}, not {_type_name(item)}",
                        )
                    )
    for field in arguments:
        if field not in schema and schema:
            problems.append(
                ArgProblem(field, "unexpected", f"'{field}' is not a parameter of this tool")
            )
    return problems


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def feedback(
    problems: list[ArgProblem],
    *,
    tool: str = "the tool",
    parsed: bool = True,
    limit: int = 8,
) -> str:
    """A short message to send back to the model instead of raising."""
    if not problems:
        return ""
    lines = [f"The arguments for `{tool}` were rejected:"]
    for problem in problems[:limit]:
        lines.append(f"- {problem.message}")
    if len(problems) > limit:
        lines.append(f"- ... and {len(problems) - limit} more")
    if not parsed:
        lines.insert(1, "- the arguments were not valid JSON")
    lines.append("Send the call again with corrected arguments.")
    return "\n".join(lines)


def parse_and_check(
    text: str | bytes | dict | None,
    schema: dict[str, dict] | None = None,
    *,
    tool: str = "the tool",
) -> tuple[dict, list[ArgProblem], str]:
    """Parse, coerce and validate in one step.

    Returns ``(arguments, problems, message)``. The message is empty when there are no
    problems, so ``args, problems, message = parse_and_check(...)`` is a single call in a
    middleware: send ``message`` back when ``problems`` is non-empty.
    """
    try:
        arguments = parse_args(text)
    except (ValueError, UnicodeDecodeError) as error:
        problem = ArgProblem("", "unparsable", f"arguments could not be read: {error}")
        return {}, [problem], feedback([problem], tool=tool, parsed=False)
    if schema:
        for field, spec in schema.items():
            if field in arguments and spec and spec.get("type"):
                arguments[field] = coerce(arguments[field], spec["type"])
    problems = check(arguments, schema)
    return arguments, problems, feedback(problems, tool=tool)
