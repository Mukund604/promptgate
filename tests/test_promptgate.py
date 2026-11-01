import http.server
import json
import pathlib
import threading

import pytest

from promptgate.checks import run_check
from promptgate.cli import main
from promptgate.gate import compare, to_markdown
from promptgate.providers import OpenAICompatibleProvider
from promptgate.runner import render, run_suite

EXAMPLE = pathlib.Path(__file__).parent.parent / "examples/support_triage"


def result(cases, cost=1.0, latency=100.0):
    n = len(cases)
    passed = sum(cases.values())
    return {"gate": {}, "cases": [{"id": i, "passed": p, "checks": [{"type": "x", "passed": p, "detail": "d"}]}
                                  for i, p in cases.items()],
            "summary": {"total": n, "passed": passed, "pass_rate": passed / n, "cost_usd": cost,
                        "median_latency_ms": latency}}


@pytest.mark.parametrize("output,spec,expected", [
    ("Hello World", {"type": "contains", "value": "world"}, True),
    ("Hello", {"type": "not_contains", "value": "hello"}, False),
    (" yes ", {"type": "equals", "value": "yes"}, True),
    ("b", {"type": "one_of", "values": ["a", "b"]}, True),
    ("order 123", {"type": "regex", "pattern": r"\d{3}"}, True),
    ("a b c", {"type": "max_words", "value": 2}, False),
    ('{"a": 1}', {"type": "json_fields", "fields": {"a": 1}}, True),
    ('{"a": 2}', {"type": "json_fields", "fields": {"a": 1}}, False),
    ('{"a": 2}', {"type": "json_fields", "fields": {"a": None}}, True),
    ('{"a": 2}', {"type": "json_fields", "fields": {"b": None}}, False),
    ("not json", {"type": "json_fields", "fields": {}}, False),
    ("[1]", {"type": "json_fields", "fields": {}}, False),
])
def test_checks(output, spec, expected):
    assert run_check(output, spec)["passed"] is expected


def test_unknown_check_is_an_error():
    with pytest.raises(ValueError):
        run_check("x", {"type": "nope"})


def test_render_reports_missing_variables():
    assert render("hi {name}", {"name": "a"}) == "hi a"
    with pytest.raises(KeyError):
        render("hi {name}", {})


def test_identical_runs_pass():
    assert compare(result({"a": True}), result({"a": True}))["passed"]


def test_regression_blocks_even_if_pass_rate_is_high():
    decision = compare(result({"a": True, "b": True}), result({"a": True, "b": False}))
    assert not decision["passed"] and decision["regressions"] == ["b"]


def test_fixing_a_failure_passes_and_is_reported():
    decision = compare(result({"a": False}), result({"a": True}))
    assert decision["passed"] and decision["fixed"] == ["a"]


def test_cost_and_latency_limits():
    assert not compare(result({"a": True}), result({"a": True}, cost=1.5))["passed"]
    assert compare(result({"a": True}), result({"a": True}, cost=1.1))["passed"]
    assert not compare(result({"a": True}), result({"a": True}, latency=200))["passed"]


def test_zero_baseline_cost_does_not_crash():
    assert not compare(result({"a": True}, cost=0.0), result({"a": True}, cost=1.0))["passed"]


def test_removing_a_case_blocks():
    decision = compare(result({"a": True, "b": False}), result({"a": True}))
    assert not decision["passed"]
    assert "removed" in decision["violations"][0]


def test_new_failing_case_blocks_but_new_passing_case_does_not():
    assert not compare(result({"a": True}), result({"a": True, "b": False}))["passed"]
    assert compare(result({"a": True}), result({"a": True, "b": True}))["passed"]


def test_example_demo_end_to_end(tmp_path):
    baseline = tmp_path / "baseline.json"
    report = tmp_path / "report.md"
    assert main(["run", str(EXAMPLE / "suite_v1.yaml"), "--out", str(baseline)]) == 0
    assert json.loads(baseline.read_text())["summary"]["pass_rate"] == 1.0
    assert main(["check", str(EXAMPLE / "suite_v1.yaml"), "--baseline", str(baseline)]) == 0
    assert main(["check", str(EXAMPLE / "suite_v2.yaml"), "--baseline", str(baseline), "--report", str(report)]) == 1
    text = report.read_text()
    assert "FAIL" in text and "outage" in text and "injection_attempt" in text


def test_markdown_lists_each_violation():
    decision = compare(result({"a": True}), result({"a": False}, cost=9))
    text = to_markdown(decision, result({"a": False}, cost=9))
    assert "regressed" in text and "cost rose" in text


class Stub(http.server.BaseHTTPRequestHandler):
    failures = 0
    seen = []

    def do_POST(self):
        Stub.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if Stub.failures:
            Stub.failures -= 1
            self.send_response(429)
            self.end_headers()
            return
        body = json.dumps({"choices": [{"message": {"content": "pong"}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    Stub.seen, Stub.failures = [], 0
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_http_provider_parses_usage(server):
    reply = OpenAICompatibleProvider(server).complete("c", "ping", "m")
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("pong", 3, 1)
    assert Stub.seen[0]["messages"][0]["content"] == "ping"


def test_http_provider_retries_rate_limits(server):
    Stub.failures = 2
    assert OpenAICompatibleProvider(server, backoff=0).complete("c", "p", "m").text == "pong"
    assert len(Stub.seen) == 3


def test_http_provider_gives_up_eventually(server):
    Stub.failures = 9
    with pytest.raises(Exception):
        OpenAICompatibleProvider(server, max_retries=1, backoff=0).complete("c", "p", "m")


def test_run_suite_uses_replay_costs():
    summary = run_suite(EXAMPLE / "suite_v1.yaml")["summary"]
    assert summary["total"] == 5
    assert summary["cost_usd"] == pytest.approx((298 * 1 + 70 * 5) / 1e6)
