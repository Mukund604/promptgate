"""Deterministic checks applied to a model's output. Each returns (passed, detail)."""
import json
import re


def _contains(output, spec):
    ok = spec["value"].lower() in output.lower()
    return ok, f"expected to contain {spec['value']!r}"


def _not_contains(output, spec):
    ok = spec["value"].lower() not in output.lower()
    return ok, f"must not contain {spec['value']!r}"


def _equals(output, spec):
    return output.strip() == spec["value"], f"expected exactly {spec['value']!r}"


def _one_of(output, spec):
    return output.strip() in spec["values"], f"expected one of {spec['values']}"


def _regex(output, spec):
    return re.search(spec["pattern"], output) is not None, f"expected to match /{spec['pattern']}/"


def _max_words(output, spec):
    count = len(output.split())
    return count <= spec["value"], f"{count} words, limit {spec['value']}"


def _json_fields(output, spec):
    """Output must parse as a JSON object with these fields, optionally at exact values."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError as error:
        return False, f"invalid JSON: {error.msg}"
    if not isinstance(data, dict):
        return False, "expected a JSON object"
    for field, expected in spec["fields"].items():
        if field not in data:
            return False, f"missing field {field!r}"
        if expected is not None and data[field] != expected:
            return False, f"{field}: expected {expected!r}, got {data[field]!r}"
    return True, "fields match"


CHECKS = {
    "contains": _contains,
    "not_contains": _not_contains,
    "equals": _equals,
    "one_of": _one_of,
    "regex": _regex,
    "max_words": _max_words,
    "json_fields": _json_fields,
}


def run_check(output, spec):
    kind = spec.get("type")
    if kind not in CHECKS:
        raise ValueError(f"unknown check type {kind!r}, expected one of {sorted(CHECKS)}")
    passed, detail = CHECKS[kind](output, spec)
    return {"type": kind, "passed": bool(passed), "detail": detail}
