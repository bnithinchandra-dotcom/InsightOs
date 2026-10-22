from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

from app.agent.evaluation import run_evaluation


def main() -> int:
    parser = ArgumentParser(description="Run the synthetic InsightOS model evaluation.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional UTF-8 JSON report path, including raw synthetic model responses.",
    )
    arguments = parser.parse_args()
    report = run_evaluation()
    serialized = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 1 if report.fail_count or report.skipped_count else 0


if __name__ == "__main__":
    sys.exit(main())
