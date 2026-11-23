"""Tests for argrepair."""

import json

import pytest

from argrepair import (
    ArgProblem,
    check,
    coerce,
    feedback,
    parse_and_check,
    parse_args,
    repair_text,
)

SCHEMA = {
    "path": {"type": "string", "required": True},
    "bytes": {"type": "integer", "minimum": 0},
    "mode": {"type": "string", "enum": ["fast", "safe"]},
}


class TestRepairText:
    def test_single_quotes_become_double(self):
        assert repair_text("{'a': 1}") == '{"a": 1}'

    def test_python_literals(self):
        assert repair_text('{"a": True, "b": False, "c": None}') == (
            '{"a": true, "b": false, "c": null}'
        )

    def test_trailing_commas(self):
        assert repair_text('{"a": 1,}') == '{"a": 1}'
        assert repair_text('{"a": [1, 2,]}') == '{"a": [1, 2]}'

    def test_comments_are_dropped(self):
        assert repair_text('{"a": 1} // note') == '{"a": 1}'

    def test_smart_quotes_are_normalised(self):
        assert repair_text("{\u201ca\u201d: 1}") == '{"a": 1}'

    def test_a_code_fence_is_removed(self):
        assert repair_text('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_python_style_quotes_are_left_alone_when_json_quotes_exist(self):
        assert repair_text("{\"a\": 'x'}") == "{\"a\": 'x'}"

    def test_a_non_string_is_rejected(self):
        with pytest.raises(TypeError):
            repair_text({"a": 1})  # type: ignore[arg-type]


class TestParseArgs:
    def test_plain_json(self):
        assert parse_args('{"a": 1}') == {"a": 1}

    def test_a_dict_is_passed_through_copied(self):
        source = {"a": 1}
        parsed = parse_args(source)
        assert parsed == source
        assert parsed is not source

    def test_bytes(self):
        assert parse_args(b'{"a": 1}') == {"a": 1}

    def test_python_literals_inside_json(self):
        assert parse_args('{"a": True, "b": None}') == {"a": True, "b": None}

    def test_a_python_dict_literal(self):
        assert parse_args("{'path': 'a.txt', 'n': 2}") == {"path": "a.txt", "n": 2}

    def test_trailing_comma_and_single_quotes(self):
        assert parse_args("{'path': 'a.txt',}") == {"path": "a.txt"}

    def test_none_and_empty_are_empty_arguments(self):
        assert parse_args(None) == {}
        assert parse_args("   ") == {}

    def test_a_json_array_is_rejected(self):
        with pytest.raises(ValueError, match="must be an object"):
            parse_args("[1, 2]")

    def test_garbage(self):
        with pytest.raises(ValueError, match="not readable"):
            parse_args("this is not json")

    def test_a_non_string_type(self):
        with pytest.raises(ValueError, match="cannot read"):
            parse_args(42)  # type: ignore[arg-type]


class TestCheck:
    def test_a_valid_call_has_no_problems(self):
        assert check({"path": "a.txt", "bytes": 10}, SCHEMA) == []

    def test_a_missing_required_field(self):
        problems = check({"bytes": 1}, SCHEMA)
        assert [(problem.field, problem.code) for problem in problems] == [("path", "missing")]
        assert problems[0].message == "'path' is required"

    def test_an_optional_field_may_be_missing(self):
        assert check({"path": "a"}, SCHEMA) == []

    def test_a_wrong_type(self):
        problems = check({"path": 3}, SCHEMA)
        assert problems[0].code == "type"
        assert "must be string, not integer" in problems[0].message

    def test_a_boolean_is_not_an_integer(self):
        assert check({"path": "a", "bytes": True}, SCHEMA)[0].code == "type"

    def test_a_float_is_not_an_integer(self):
        assert check({"path": "a", "bytes": 1.5}, SCHEMA)[0].code == "type"

    def test_an_enum(self):
        problems = check({"path": "a", "mode": "turbo"}, SCHEMA)
        assert problems[0].code == "enum"
        assert "must be one of" in problems[0].message

    def test_a_minimum(self):
        problems = check({"path": "a", "bytes": -1}, SCHEMA)
        assert problems[0].code == "minimum"

    def test_a_maximum(self):
        schema = {"n": {"type": "number", "maximum": 10}}
        assert check({"n": 11}, schema)[0].code == "maximum"

    def test_an_unexpected_field(self):
        problems = check({"path": "a", "colour": "red"}, SCHEMA)
        assert [(problem.field, problem.code) for problem in problems] == [("colour", "unexpected")]

    def test_no_schema_means_no_checks(self):
        assert check({"anything": object()}, None) == []

    def test_an_array_of_items(self):
        schema = {"tags": {"type": "array", "items": {"type": "string"}}}
        problems = check({"tags": ["a", 2]}, schema)
        assert problems[0].field == "tags[1]"
        assert problems[0].code == "type"

    def test_arguments_that_are_not_an_object(self):
        assert check(["a"], SCHEMA)[0].code == "type"

    def test_several_problems_at_once(self):
        problems = check({"mode": "turbo", "bytes": "many"}, SCHEMA)
        assert {problem.code for problem in problems} == {"missing", "type", "enum"}

    def test_an_empty_schema_accepts_anything(self):
        assert check({"a": 1}, {}) == []

    def test_a_null_value_is_allowed_for_a_nullable_type(self):
        schema = {"value": {"type": "null"}}
        assert check({"value": None}, schema) == []

    def test_a_problem_prints_its_message(self):
        assert str(ArgProblem("a", "missing", "'a' is required")) == "'a' is required"


class TestCoerce:
    def test_a_numeric_string_to_int(self):
        assert coerce("10", "integer") == 10

    def test_a_numeric_string_to_float(self):
        assert coerce("1.5", "number") == 1.5

    def test_a_non_numeric_string_stays(self):
        assert coerce("ten", "integer") == "ten"

    def test_the_usual_truthy_strings(self):
        for value in ["true", "True", "yes", "on", "1"]:
            assert coerce(value, "boolean") is True

    def test_the_usual_falsy_strings(self):
        for value in ["false", "no", "off", "0"]:
            assert coerce(value, "boolean") is False

    def test_an_unknown_boolean_string_stays(self):
        assert coerce("maybe", "boolean") == "maybe"

    def test_a_number_to_string(self):
        assert coerce(3, "string") == "3"

    def test_a_scalar_to_an_array(self):
        assert coerce("a", "array") == ["a"]

    def test_an_unknown_type_is_untouched(self):
        assert coerce("a", "widget") == "a"


class TestFeedback:
    def test_no_problems_means_no_message(self):
        assert feedback([]) == ""

    def test_the_message_names_the_tool_and_the_problem(self):
        problems = [ArgProblem("path", "missing", "'path' is required")]
        message = feedback(problems, tool="read_file")
        assert message.splitlines()[0] == "The arguments for `read_file` were rejected:"
        assert "- 'path' is required" in message
        assert message.splitlines()[-1] == "Send the call again with corrected arguments."

    def test_many_problems_are_capped(self):
        problems = [ArgProblem(f"f{index}", "type", "bad") for index in range(12)]
        message = feedback(problems, limit=3)
        assert "... and 9 more" in message

    def test_an_unparsable_message_says_so(self):
        problem = ArgProblem("", "unparsable", "arguments could not be read: nope")
        message = feedback([problem], parsed=False)
        assert "- the arguments were not valid JSON" in message


class TestParseAndCheck:
    def test_a_clean_call(self):
        arguments, problems, message = parse_and_check('{"path": "a.txt"}', SCHEMA, tool="read")
        assert arguments == {"path": "a.txt"}
        assert problems == []
        assert message == ""

    def test_values_are_coerced_before_validation(self):
        arguments, problems, message = parse_and_check(
            '{"path": "a", "bytes": "42"}', SCHEMA, tool="read"
        )
        assert arguments["bytes"] == 42
        assert problems == []

    def test_a_repairable_payload_still_validates(self):
        arguments, problems, _ = parse_and_check("{'path': 'a.txt', 'mode': 'fast'}", SCHEMA)
        assert arguments == {"path": "a.txt", "mode": "fast"}
        assert problems == []

    def test_a_missing_field_produces_a_message(self):
        arguments, problems, message = parse_and_check('{"bytes": 1}', SCHEMA, tool="read_file")
        assert arguments == {"bytes": 1}
        assert problems[0].code == "missing"
        assert "`read_file`" in message

    def test_unparsable_arguments_produce_a_message_rather_than_an_exception(self):
        arguments, problems, message = parse_and_check("not json at all", SCHEMA, tool="read")
        assert arguments == {}
        assert problems[0].code == "unparsable"
        assert "could not be read" in message

    def test_a_null_payload_is_an_empty_call(self):
        arguments, problems, _ = parse_and_check(None, {"path": {"required": True}})
        assert arguments == {}
        assert problems[0].code == "missing"

    def test_the_message_is_json_safe_for_a_tool_result(self):
        _, _, message = parse_and_check("{}", SCHEMA, tool="read")
        assert json.loads(json.dumps({"error": message}))["error"] == message


class TestSchemaShapes:
    def test_a_realistic_tool_schema(self):
        schema = {
            "query": {"type": "string", "required": True},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "filters": {"type": "array", "items": {"type": "string"}},
            "sort": {"type": "string", "enum": ["relevance", "date"]},
        }
        good = {"query": "python", "limit": 10, "filters": ["a"], "sort": "date"}
        assert check(good, schema) == []
        bad = {"query": 1, "limit": 0, "filters": ["a", 2], "sort": "size"}
        codes = [problem.code for problem in check(bad, schema)]
        assert codes.count("type") == 2
        assert "maximum" not in codes
        assert "minimum" in codes
        assert "enum" in codes
