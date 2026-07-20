"""Compatibility views for immutable cluster result schema versions."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable


CURRENT_CLUSTER_SCHEMA_VERSION = "2.0.0"
LEGACY_CLUSTER_SCHEMA_VERSION = "1.0.0"
CPU_RECONCILIATION_CATEGORIES = (
    "genuine_cgroup_peak",
    "average",
    "sampled_instantaneous",
    "unavailable",
)


def _legacy_measurement_window(record: dict[str, Any], root: Path | None) -> float | None:
    if root is None:
        return None
    for supporting in record.get("supporting_log_paths", []):
        if not str(supporting).endswith("pod.log"):
            continue
        path = root / str(supporting)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = payload.get("container_elapsed_seconds")
            return None if value is None else float(value)
    return None


def normalize_cpu_measurement(
    record: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    """Map a raw record to explicit CPU semantics without mutating the record."""

    version = record.get("cluster_schema_version")
    if version == CURRENT_CLUSTER_SCHEMA_VERSION:
        required = (
            "cpu_usage_m",
            "cpu_measurement_statistic",
            "cpu_sampling_interval_seconds",
            "cpu_measurement_window_seconds",
            "cpu_measurement_source",
        )
        missing = [field for field in required if field not in record]
        if missing:
            raise ValueError(f"cluster schema 2.0 record lacks {', '.join(missing)}")
        statistic = record["cpu_measurement_statistic"]
        category = {
            "genuine_cgroup_peak": "genuine_cgroup_peak",
            "full_window_average": "average",
            "sample_maximum": "sampled_instantaneous",
            "sampled_instantaneous": "sampled_instantaneous",
            "unavailable": "unavailable",
        }.get(statistic)
        if category is None:
            raise ValueError(f"unsupported CPU statistic {statistic!r}")
        return {
            "cpu_usage_m": record["cpu_usage_m"],
            "cpu_measurement_statistic": statistic,
            "cpu_sampling_interval_seconds": record["cpu_sampling_interval_seconds"],
            "cpu_measurement_window_seconds": record["cpu_measurement_window_seconds"],
            "cpu_measurement_source": record["cpu_measurement_source"],
            "cpu_reconciliation_category": category,
            "legacy_source_field": None,
        }

    if version != LEGACY_CLUSTER_SCHEMA_VERSION:
        raise ValueError(f"unsupported cluster_schema_version {version!r}")

    legacy_value = record.get("peak_cpu_m")
    sample_count = int(record.get("cgroup_sample_count") or 0)
    if legacy_value is None:
        return {
            "cpu_usage_m": None,
            "cpu_measurement_statistic": "unavailable",
            "cpu_sampling_interval_seconds": None,
            "cpu_measurement_window_seconds": None,
            "cpu_measurement_source": "not_available",
            "cpu_reconciliation_category": "unavailable",
            "legacy_source_field": "peak_cpu_m",
        }
    if sample_count > 0:
        return {
            "cpu_usage_m": legacy_value,
            "cpu_measurement_statistic": "sample_maximum",
            "cpu_sampling_interval_seconds": record.get("cgroup_sample_interval_seconds"),
            "cpu_measurement_window_seconds": None,
            "cpu_measurement_source": "cgroup_v2_cpu_stat_interval_delta",
            "cpu_reconciliation_category": "sampled_instantaneous",
            "legacy_source_field": "peak_cpu_m",
        }
    return {
        "cpu_usage_m": legacy_value,
        "cpu_measurement_statistic": "full_window_average",
        "cpu_sampling_interval_seconds": None,
        "cpu_measurement_window_seconds": _legacy_measurement_window(record, root),
        "cpu_measurement_source": "cgroup_v2_cpu_stat_full_window_delta",
        "cpu_reconciliation_category": "average",
        "legacy_source_field": "peak_cpu_m",
    }


def normalized_cluster_record(
    record: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    normalized = dict(record)
    normalized.update(normalize_cpu_measurement(record, root=root))
    return normalized


def cpu_reconciliation(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        normalize_cpu_measurement(record)["cpu_reconciliation_category"] for record in records
    )
    result = {category: counts[category] for category in CPU_RECONCILIATION_CATEGORIES}
    result["total_records"] = sum(result.values())
    return result
