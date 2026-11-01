"""Runs a suite and returns a JSON serializable result."""
import pathlib
import statistics
import string

import yaml

from .checks import run_check
from .providers import build_provider


def load_suite(path):
    path = pathlib.Path(path)
    suite = yaml.safe_load(path.read_text())
    for key in ("prompt", "model", "provider", "cases"):
        if key not in suite:
            raise ValueError(f"{path}: missing required key {key!r}")
    ids = [c["id"] for c in suite["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate case ids")
    return suite, path.parent


def render(template, variables):
    """Fills {name} placeholders and fails loudly when a variable is missing."""
    fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    missing = fields - set(variables)
    if missing:
        raise KeyError(f"prompt needs variables {sorted(missing)}")
    return template.format(**variables)


def run_suite(path, provider=None):
    suite, base = load_suite(path)
    provider = provider or build_provider(suite["provider"], base)
    template = suite["prompt"]
    if isinstance(template, dict):
        template = (base / template["file"]).read_text()
    price = suite.get("price", {})
    in_rate, out_rate = price.get("input_per_mtok", 0.0), price.get("output_per_mtok", 0.0)
    cases = []
    for case in suite["cases"]:
        reply = provider.complete(case["id"], render(template, case.get("vars", {})), suite["model"])
        checks = [run_check(reply.text, spec) for spec in case.get("checks", [])]
        cases.append({
            "id": case["id"],
            "passed": all(c["passed"] for c in checks),
            "checks": checks,
            "output": reply.text,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "cost_usd": (reply.input_tokens * in_rate + reply.output_tokens * out_rate) / 1e6,
            "latency_ms": reply.latency_ms,
        })
    latencies = [c["latency_ms"] for c in cases]
    return {
        "suite": suite.get("name", pathlib.Path(path).stem),
        "model": suite["model"],
        "gate": suite.get("gate", {}),
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": sum(c["passed"] for c in cases),
            "pass_rate": sum(c["passed"] for c in cases) / len(cases) if cases else 0.0,
            "cost_usd": sum(c["cost_usd"] for c in cases),
            "median_latency_ms": statistics.median(latencies) if latencies else 0.0,
        },
    }
