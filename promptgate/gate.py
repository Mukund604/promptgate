"""Compares a run against a baseline and decides whether the change may ship."""

DEFAULTS = {
    "no_new_failures": True,
    "min_pass_rate": 0.0,
    "max_cost_increase_pct": 20.0,
    "max_latency_increase_pct": 50.0,
}


def _pct_change(old, new):
    if old == 0:
        return 0.0 if new == 0 else float("inf")
    return (new - old) / old * 100


def compare(baseline, current, overrides=None):
    rules = {**DEFAULTS, **current.get("gate", {}), **(overrides or {})}
    old_cases = {c["id"]: c for c in baseline["cases"]}
    new_cases = {c["id"]: c for c in current["cases"]}
    regressions = [i for i, c in new_cases.items() if not c["passed"] and i in old_cases and old_cases[i]["passed"]]
    fixed = [i for i, c in new_cases.items() if c["passed"] and i in old_cases and not old_cases[i]["passed"]]
    failing_new = [i for i, c in new_cases.items() if not c["passed"] and i not in old_cases]
    removed = sorted(set(old_cases) - set(new_cases))
    old, new = baseline["summary"], current["summary"]
    cost = _pct_change(old["cost_usd"], new["cost_usd"])
    latency = _pct_change(old["median_latency_ms"], new["median_latency_ms"])

    violations = []
    if rules["no_new_failures"] and regressions:
        violations.append(f"{len(regressions)} case(s) regressed: {', '.join(sorted(regressions))}")
    if rules["no_new_failures"] and failing_new:
        violations.append(f"new case(s) fail: {', '.join(sorted(failing_new))}")
    if removed:
        # Deleting a failing test is the easiest way to fake a green build.
        violations.append(f"case(s) removed from the suite: {', '.join(removed)}")
    if new["pass_rate"] < rules["min_pass_rate"]:
        violations.append(f"pass rate {new['pass_rate']:.1%} is below {rules['min_pass_rate']:.1%}")
    if cost > rules["max_cost_increase_pct"]:
        violations.append(f"cost rose {cost:.1f}%, limit {rules['max_cost_increase_pct']:.1f}%")
    if latency > rules["max_latency_increase_pct"]:
        violations.append(f"median latency rose {latency:.1f}%, limit {rules['max_latency_increase_pct']:.1f}%")
    return {
        "passed": not violations,
        "violations": violations,
        "regressions": sorted(regressions),
        "fixed": sorted(fixed),
        "baseline": old,
        "current": new,
        "cost_change_pct": cost,
        "latency_change_pct": latency,
    }


def to_markdown(decision, current):
    icon = "PASS" if decision["passed"] else "FAIL"
    old, new = decision["baseline"], decision["current"]
    lines = [
        f"## promptgate: {icon}",
        "",
        "| Metric | Baseline | This change | Delta |",
        "| :--- | ---: | ---: | ---: |",
        f"| Pass rate | {old['pass_rate']:.1%} | {new['pass_rate']:.1%} | "
        f"{(new['pass_rate'] - old['pass_rate']) * 100:+.1f} pts |",
        f"| Cost per run | ${old['cost_usd']:.6f} | ${new['cost_usd']:.6f} | {decision['cost_change_pct']:+.1f}% |",
        f"| Median latency | {old['median_latency_ms']:.0f} ms | {new['median_latency_ms']:.0f} ms | "
        f"{decision['latency_change_pct']:+.1f}% |",
    ]
    if decision["violations"]:
        lines += ["", "**Blocking:**"] + [f"- {v}" for v in decision["violations"]]
    if decision["fixed"]:
        lines += ["", f"**Fixed:** {', '.join(decision['fixed'])}"]
    failures = [c for c in current["cases"] if c["id"] in decision["regressions"]]
    if failures:
        lines += ["", "**Regressed cases:**", ""]
        for case in failures:
            broken = next(c for c in case["checks"] if not c["passed"])
            lines.append(f"- `{case['id']}`: {broken['type']} failed, {broken['detail']}")
    return "\n".join(lines) + "\n"
