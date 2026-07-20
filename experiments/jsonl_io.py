"""Append-only raw result storage and derived CSV export."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from experiments.result_schema import CSV_FIELDS, migrate_record, validate_record


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one validated record to a JSONL file without truncating it."""

    validate_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL record") from exc
            migrated = migrate_record(record)
            validate_record(migrated)
            records.append(migrated)
    return records


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def export_csv(records: Iterable[dict[str, Any]], path: Path, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing derived CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for record in records:
            validate_record(record)
            writer.writerow({field: _csv_value(record[field]) for field in CSV_FIELDS})
