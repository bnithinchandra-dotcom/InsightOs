from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import (
    apply_decisions,
    build_training_bundle,
    review_template,
    verify_training_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="insightos_training.training_export")
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build")
    build.add_argument("--source", type=Path)
    build.add_argument("--extraction-root", type=Path)
    build.add_argument("--destination", type=Path)
    build.add_argument("--decisions", type=Path)

    template = subcommands.add_parser("review-template")
    template.add_argument("--bundle", type=Path)
    template.add_argument("--output", type=Path, required=True)

    apply = subcommands.add_parser("apply-decisions")
    apply.add_argument("--decisions", type=Path, required=True)
    apply.add_argument("--bundle", type=Path)
    apply.add_argument("--persistent", type=Path)

    verify = subcommands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_training_bundle(
                source_root=args.source,
                extraction_root=args.extraction_root,
                destination=args.destination,
                decisions_path=args.decisions,
            )
            print(result["bundle_sha256"])
            return 0
        if args.command == "review-template":
            output = review_template(args.bundle, args.output)
            print(output)
            return 0
        if args.command == "apply-decisions":
            result = apply_decisions(args.decisions, args.bundle, args.persistent)
            print(result["decision_sha256"])
            return 0
        result = verify_training_bundle(args.bundle)
        print(result["status"])
        return 0 if result["valid"] else 1
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
