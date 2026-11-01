"""How often does the gate block a harmless change, and catch a real one, when the model is noisy?

Simulated: each case passes with some probability, so ground truth is known. These are not
measurements of any real model. Real pass rate variance would need to be estimated per suite.
"""
import json
import pathlib

import numpy as np

from promptgate.gate import _regressed
from .charts import save

CASES, TRIALS = 50, 2000


def blocked(rng, base, drop, repeats, alpha=0.05):
    """True if at least one of the changed or unchanged cases trips the gate."""
    old = rng.binomial(repeats, base, CASES)
    new_rates = base - drop
    new = rng.binomial(repeats, np.clip(new_rates, 0, 1), CASES)
    for a, b in zip(old, new):
        if _regressed({"repeats": repeats, "passes": int(a), "passed": a == repeats},
                      {"repeats": repeats, "passes": int(b), "passed": b == repeats}, alpha):
            return True
    return False


def main():
    rng = np.random.default_rng(0)
    # Harmless change: every case keeps its pass rate. Real regression: 5 cases fall to 30%.
    base = np.full(CASES, 0.95)
    real_drop = np.zeros(CASES)
    real_drop[:5] = 0.65
    repeats = [1, 3, 5, 10, 20]
    false_block, detect = [], []
    for r in repeats:
        # A Bonferroni style alpha keeps 50 per case tests from adding up to a false block.
        alpha = 0.05 / CASES
        false_block.append(float(np.mean([blocked(rng, base, np.zeros(CASES), r, alpha) for _ in range(TRIALS // 4)])))
        detect.append(float(np.mean([blocked(rng, base, real_drop, r, alpha) for _ in range(TRIALS // 4)])))
        print(r, false_block[-1], detect[-1], flush=True)
    out = dict(cases=CASES, base_pass_rate=0.95, regressed_cases=5, regressed_to=0.30, repeats=repeats,
               false_block_rate=false_block, detection_rate=detect)
    pathlib.Path("results/noise.json").write_text(json.dumps(out, indent=2))

    def draw(fig, theme):
        ax = fig.add_subplot()
        c = theme["series"]
        x = range(len(repeats))
        ax.plot(x, [v * 100 for v in detect], color=c[0], lw=2, marker="o", ms=7, label="Real regression caught")
        ax.plot(x, [v * 100 for v in false_block], color=c[1], lw=2, marker="o", ms=7, label="Harmless change blocked")
        ax.set_xticks(list(x), [str(r) for r in repeats])
        ax.set_ylim(-3, 105)
        ax.set_xlabel("Runs per case")
        ax.set_ylabel("Share of simulated pull requests (%)")
        ax.set_title("A strict gate needs about 10 runs per case to work")
        ax.legend(loc="upper center", ncol=2, bbox_to_anchor=(0.5, -0.17))
    save(draw, "noise")


if __name__ == "__main__":
    main()
