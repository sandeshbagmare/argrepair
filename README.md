# argrepair

Turn a bad tool call into one sentence the model can act on, instead of an exception that
ends the run.

[![PyPI](https://img.shields.io/pypi/v/argrepair.svg)](https://pypi.org/project/argrepair/)
[![Python](https://img.shields.io/pypi/pyversions/argrepair.svg)](https://pypi.org/project/argrepair/)
[![tests](https://github.com/sandeshbagmare/argrepair/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeshbagmare/argrepair/actions/workflows/ci.yml)
[![license](https://img.shields.io/pypi/l/argrepair.svg)](https://github.com/sandeshbagmare/argrepair/blob/main/LICENSE)

```python
>>> from argrepair import parse_and_check
>>> schema = {"path": {"type": "string", "required": True}, "bytes": {"type": "integer"}}
>>> args, problems, message = parse_and_check("{'path': 'a.txt', 'bytes': '42'}", schema, tool="read_file")
>>> args
{'path': 'a.txt', 'bytes': 42}
>>> problems
[]
```

And when the call really is wrong:

```python
>>> _, problems, message = parse_and_check('{"bytes": "many"}', schema, tool="read_file")
>>> print(message)
The arguments for `read_file` were rejected:
- 'path' is required
- 'bytes' must be integer, not string
Send the call again with corrected arguments.
```

Models produce almost-JSON constantly: single quotes, `True`/`None`, a trailing comma, a
`//` comment, a fenced code block, a JSON string where an integer belongs. A tool runtime
that raises on the first of those turns a recoverable mistake into a failed run.
`argrepair` repairs what is unambiguously repairable, coerces what is unambiguous,
validates against a small schema, and hands back a message designed for the next turn.

## Install

```console
pip install argrepair
```

## Use

```python
from argrepair import parse_args, check, coerce, feedback, parse_and_check

parse_args("{'a': 1,}")                       # {'a': 1}
parse_args("{\"a\": True}")                   # {'a': True}
parse_args('```json\n{"a": 1}\n```')          # {'a': 1}

problems = check({"mode": "turbo"}, {"mode": {"type": "string", "enum": ["fast", "safe"]}})
print(feedback(problems, tool="search"))
```

The one call a middleware needs:

```python
def run_tool(name, raw_arguments, schema, tool):
    arguments, problems, message = parse_and_check(raw_arguments, schema, tool=name)
    if problems:
        return {"error": message}        # the model gets another turn
    return tool(**arguments)
```

## Schema

```python
{
    "path":   {"type": "string", "required": True},
    "bytes":  {"type": "integer", "minimum": 0},
    "limit":  {"type": "number", "maximum": 100},
    "mode":   {"type": "string", "enum": ["fast", "safe"]},
    "tags":   {"type": "array", "items": {"type": "string"}},
    "dry_run": {"type": "boolean"},
}
```

`type` is one of `string`, `integer`, `number`, `boolean`, `object`, `array`, `null`.
A boolean is **not** an integer here, and `1.5` is not an integer — the two mistakes that
turn into silent data loss downstream. Unknown fields are reported as `unexpected`, so a
model that invents `colour: "red"` is told so instead of the value being dropped.

## What is repaired, and what is refused

| input | result |
| ----- | ------ |
| `{'a': 1}` | parsed |
| `{"a": True, "b": None}` | parsed |
| `{"a": 1,}` | parsed |
| `{"a": 1} // note` | parsed |
| ` ```json {…} ``` ` | parsed |
| `{"a": 'x'}` (single quotes inside JSON quotes) | refused, so a real `'` in a value is never mangled |
| `[1, 2]` | refused: arguments must be an object |
| `not json at all` | a message, not an exception, via `parse_and_check` |

Coercion is deliberately narrow: `"42"` → `42`, `"1.5"` → `1.5`, `"true"/"yes"/"on"/"1"`
→ `True`, `3` → `"3"` for a string field, a lone value → a one-item array. Anything else
stays as it was and is reported by `check`.

## Limits

- This is a small schema, not JSON Schema: no `oneOf`, no `pattern`, no nested objects.
  Reach for `jsonschema` when you need that; this library is about the message.
- Repairs are heuristic. A payload that is genuinely ambiguous is refused rather than
  guessed at.
- It validates the shape of the arguments, not their meaning — whether the path exists is
  still the tool's job.

## Development

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

## License

MIT.
