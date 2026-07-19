"""Export derived CSV summaries from immutable raw JSONL records."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from experiments.jsonl_io import export_csv, read_jsonl


ROOT = Path(__file__).resolve().parents[1]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export experiment JSONL records to CSV.")
    parser.add_argument("--raw-jsonl", type=Path, default=ROOT / "experiments" / "raw" / "results.jsonl")
    parser.add_argument("--csv-out", type=Path, default=ROOT / "experiments" / "summaries" / "results.csv")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing derived CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    records = read_jsonl(args.raw_jsonl)
    export_csv(records, args.csv_out, overwrite=args.overwrite)
    print(f"exported {len(records)} records to {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
