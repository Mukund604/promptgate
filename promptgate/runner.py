"""Runs a suite and returns a JSON serializable result."""
import pathlib
import statistics
import string
from concurrent.futures import ThreadPoolExecutor

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


def _sample(provider, case, prompt, model, rates, judge):
    reply = provider.complete(case["id"], prompt, model)
    checks = [run_judge(reply.text, spec, provider, model) if spec.get("type") == "judge" else run_check(reply.text, spec)
              for spec in case.get("checks", [])]
    return dict(passed=all(c["passed"] for c in checks), checks=checks, output=reply.text, input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens, latency_ms=reply.latency_ms,
                cost_usd=(reply.input_tokens * rates[0] + reply.output_tokens * rates[1]) / 1e6)


def run_judge(output, spec, provider, model):
    """Asks a model to grade the output against a rubric. Replies must start with PASS or FAIL."""
    prompt = f"Grade this output against the rubric. Reply PASS or FAIL.\nRubric: {spec['rubric']}\nOutput: {output}"
    verdict = provider.complete(spec.get("case_id", "judge"), prompt, spec.get("model", model)).text.strip().upper()
    return {"type": "judge", "passed": verdict.startswith("PASS"), "detail": f"judge said {verdict[:40]!r} for rubric {spec['rubric']!r}"}


def run_suite(path, provider=None, repeats=None, workers=1):
    """Runs every case repeats times. Repeats let the gate separate real regressions from model noise."""
    suite, base = load_suite(path)
    provider = provider or build_provider(suite["provider"], base)
    template = suite["prompt"]
    if isinstance(template, dict):
        template = (base / template["file"]).read_text()
    price = suite.get("price", {})
    rates = (price.get("input_per_mtok", 0.0), price.get("output_per_mtok", 0.0))
    repeats = repeats or suite.get("repeats", 1)

    def run_case(case):
        prompt = render(template, case.get("vars", {}))
        samples = [_sample(provider, case, prompt, suite["model"], rates, None) for _ in range(repeats)]
        passes = sum(s["passed"] for s in samples)
        first_failure = next((s for s in samples if not s["passed"]), samples[0])
        return {
            "id": case["id"], "passed": passes == repeats, "passes": passes, "repeats": repeats,
            "flaky": 0 < passes < repeats, "checks": first_failure["checks"], "output": samples[0]["output"],
            "input_tokens": sum(s["input_tokens"] for s in samples) / repeats,
            "output_tokens": sum(s["output_tokens"] for s in samples) / repeats,
            "cost_usd": sum(s["cost_usd"] for s in samples) / repeats,
            "latency_ms": statistics.median(s["latency_ms"] for s in samples),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        cases = list(pool.map(run_case, suite["cases"]))
    latencies = [c["latency_ms"] for c in cases]
    return {
        "suite": suite.get("name", pathlib.Path(path).stem), "model": suite["model"], "gate": suite.get("gate", {}), "cases": cases,
        "summary": {
            "total": len(cases), "passed": sum(c["passed"] for c in cases),
            "pass_rate": sum(c["passes"] for c in cases) / (len(cases) * repeats) if cases else 0.0,
            "flaky": sum(c["flaky"] for c in cases), "cost_usd": sum(c["cost_usd"] for c in cases),
            "median_latency_ms": statistics.median(latencies) if latencies else 0.0,
        },
    }
