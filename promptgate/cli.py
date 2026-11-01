import argparse
import json
import pathlib
import sys

from .gate import compare, to_markdown
from .runner import run_suite


def main(argv=None):
    parser = argparse.ArgumentParser(prog="promptgate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a suite and save the result as JSON")
    run.add_argument("suite")
    run.add_argument("--out", required=True)
    check = sub.add_parser("check", help="run a suite and gate it against a baseline")
    check.add_argument("suite")
    check.add_argument("--baseline", required=True)
    check.add_argument("--report", help="write the Markdown report here, for example a PR comment file")
    args = parser.parse_args(argv)

    result = run_suite(args.suite)
    if args.command == "run":
        pathlib.Path(args.out).write_text(json.dumps(result, indent=2))
        s = result["summary"]
        print(f"{s['passed']}/{s['total']} passed, ${s['cost_usd']:.6f}, wrote {args.out}")
        return 0
    decision = compare(json.loads(pathlib.Path(args.baseline).read_text()), result)
    report = to_markdown(decision, result)
    print(report)
    if args.report:
        pathlib.Path(args.report).write_text(report)
    return 0 if decision["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
