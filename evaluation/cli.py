from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.compare import compare_frozen_run
from evaluation.freeze import freeze_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated VALLEYEYE evaluation tools")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_parser = commands.add_parser("freeze", help="Verify and freeze a completed job")
    freeze_parser.add_argument("job_dir", type=Path)
    freeze_parser.add_argument("destination", type=Path)
    compare_parser = commands.add_parser("compare", help="Compare a frozen mask with local truth")
    compare_parser.add_argument("frozen_dir", type=Path)
    compare_parser.add_argument("reference", type=Path)
    compare_parser.add_argument("report", type=Path)
    compare_parser.add_argument("--prediction-artifact", default="stages/INFERENCE/mask.tif")
    args = parser.parse_args()
    if args.command == "freeze":
        print(freeze_run(args.job_dir, args.destination))
        return
    result = compare_frozen_run(
        args.frozen_dir,
        args.reference,
        args.report,
        args.prediction_artifact,
    )
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
