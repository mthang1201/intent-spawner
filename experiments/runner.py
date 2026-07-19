"""Orchestrate comparable benchmark evaluation runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any, Iterable
from uuid import uuid4

import yaml

from experiments.jsonl_io import append_jsonl, export_csv, read_jsonl
from experiments.methods import METHODS
from experiments.recorder import build_record, load_workloads, run_local_workload
from experiments.result_schema import current_git_commit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks" / "workloads.yaml"
DEFAULT_RAW_ROOT = ROOT / "experiments" / "raw"
DEFAULT_SUMMARY_ROOT = ROOT / "experiments" / "summaries"
SMOKE_WORKLOAD_ID = "light_basic_python"
SMOKE_METHOD = "context_aware"


class InfrastructureFailure(RuntimeError):
    """An experiment could not be attempted because orchestration failed."""


@dataclass(frozen=True)
class MatrixItem:
    method: str
    workload_id: str
    repeat_index: int
    seed: int
    run_id: str

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.method, self.workload_id, self.repeat_index)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


def create_experiment_id(prefix: str = "experiment") -> str:
    return f"{_utc_stamp()}-{prefix}-{uuid4().hex[:8]}"


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def select_workloads(
    workloads: list[dict[str, Any]],
    *,
    workload_ids: Iterable[str] | None = None,
    categories: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    ids = set(workload_ids or [])
    category_set = set(categories or [])
    known_ids = {workload["workload_id"] for workload in workloads}
    unknown = sorted(ids - known_ids)
    if unknown:
        raise ValueError(f"unknown workload_id values: {', '.join(unknown)}")

    selected = []
    for workload in workloads:
        id_match = not ids or workload["workload_id"] in ids
        category_match = not category_set or workload["category"] in category_set
        if id_match and category_match:
            selected.append(workload)
    if not selected:
        raise ValueError("no workloads matched the requested selection")
    return selected


def generate_matrix(
    workloads: list[dict[str, Any]],
    methods: Iterable[str],
    *,
    repeats: int,
    seed: int,
    experiment_id: str,
) -> list[MatrixItem]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    method_list = list(methods)
    unsupported = [method for method in method_list if method not in METHODS]
    if unsupported:
        raise ValueError(f"unsupported methods: {', '.join(unsupported)}")

    matrix: list[MatrixItem] = []
    for workload in workloads:
        base_seed = int(workload["deterministic_seed"])
        for method in method_list:
            for repeat_index in range(repeats):
                run_seed = base_seed + seed * 10_000 + repeat_index
                run_id = f"{experiment_id}-{method}-{workload['workload_id']}-r{repeat_index:02d}"
                matrix.append(
                    MatrixItem(
                        method=method,
                        workload_id=workload["workload_id"],
                        repeat_index=repeat_index,
                        seed=run_seed,
                        run_id=run_id,
                    )
                )
    run_ids = [item.run_id for item in matrix]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("matrix produced duplicate run IDs")
    return matrix


def _write_json_new(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_matrix_new(path: Path, matrix: list[MatrixItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for item in matrix:
            handle.write(json.dumps(asdict(item), sort_keys=True, separators=(",", ":")) + "\n")


def load_matrix(path: Path) -> list[MatrixItem]:
    matrix = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid matrix JSONL") from exc
            matrix.append(MatrixItem(**payload))
    return matrix


def _run_text(command: list[str]) -> str | None:
    if not command or shutil.which(command[0]) is None:
        return None
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or None
    return result.stdout.strip()


def environment_metadata(
    *,
    environment_id: str,
    manifest_path: Path,
    matrix: list[MatrixItem],
    timeout_seconds: float | None,
) -> dict[str, Any]:
    status = _run_text(["git", "status", "--short"])
    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "environment_id": environment_id,
        "git_commit": current_git_commit(ROOT),
        "git_branch": _run_text(["git", "branch", "--show-current"]),
        "git_dirty": bool(status),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "kubectl_context": _run_text(["kubectl", "config", "current-context"]),
        "helm_version": _run_text(["helm", "version", "--short"]),
        "manifest_path": str(manifest_path),
        "timeout_seconds": timeout_seconds,
        "planned_run_count": len(matrix),
        "methods": sorted({item.method for item in matrix}),
        "workload_count": len({item.workload_id for item in matrix}),
    }


def completed_keys(results_jsonl: Path) -> set[tuple[str, str, int]]:
    if not results_jsonl.exists():
        return set()
    return {
        (record["method"], record["workload_id"], int(record["repeat_index"]))
        for record in read_jsonl(results_jsonl)
    }


def execute_item(
    *,
    item: MatrixItem,
    workload: dict[str, Any],
    experiment_dir: Path,
    environment_id: str,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    artifact_dir = experiment_dir / item.run_id / item.workload_id
    if artifact_dir.exists():
        raise InfrastructureFailure(f"artifact directory already exists for incomplete run: {artifact_dir}")

    local_result = run_local_workload(
        workload,
        item.seed,
        artifact_dir,
        timeout_seconds=timeout_seconds,
    )
    return build_record(
        workload=workload,
        method=item.method,
        repeat_index=item.repeat_index,
        seed=item.seed,
        environment_id=environment_id,
        run_id=item.run_id,
        local_result=local_result,
    )


def run_matrix(
    *,
    matrix: list[MatrixItem],
    workloads_by_id: dict[str, dict[str, Any]],
    experiment_dir: Path,
    environment_id: str,
    timeout_seconds: float | None = None,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    results_jsonl = experiment_dir / "results.jsonl"
    done = completed_keys(results_jsonl) if resume else set()
    attempted = 0
    skipped = 0
    timed_out = 0

    if dry_run:
        return {
            "experiment_dir": str(experiment_dir),
            "planned": len(matrix),
            "attempted": 0,
            "skipped_completed": len(done),
            "timed_out": 0,
            "dry_run": True,
        }

    for item in matrix:
        if item.key in done:
            skipped += 1
            continue
        workload = workloads_by_id.get(item.workload_id)
        if workload is None:
            raise InfrastructureFailure(f"matrix references missing workload_id {item.workload_id!r}")
        record = execute_item(
            item=item,
            workload=workload,
            experiment_dir=experiment_dir,
            environment_id=environment_id,
            timeout_seconds=timeout_seconds,
        )
        append_jsonl(results_jsonl, record)
        attempted += 1
        if record["timeout"]:
            timed_out += 1

    return {
        "experiment_dir": str(experiment_dir),
        "planned": len(matrix),
        "attempted": attempted,
        "skipped_completed": skipped,
        "timed_out": timed_out,
        "dry_run": False,
    }


def _resolve_methods(args: argparse.Namespace) -> list[str]:
    if args.smoke and not args.method:
        return [SMOKE_METHOD]
    return args.method or list(METHODS)


def _resolve_selection(args: argparse.Namespace, workloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if args.smoke and not args.workload_id and not args.category:
        return select_workloads(workloads, workload_ids=[SMOKE_WORKLOAD_ID])
    return select_workloads(workloads, workload_ids=args.workload_id, categories=args.category)


def _prepare_new_experiment_dir(raw_root: Path, explicit_dir: Path | None, *, smoke: bool) -> Path:
    if explicit_dir:
        if explicit_dir.exists():
            raise InfrastructureFailure(f"experiment directory already exists: {explicit_dir}")
        explicit_dir.mkdir(parents=True)
        return explicit_dir

    prefix = "smoke" if smoke else "matrix"
    for _ in range(20):
        experiment_dir = raw_root / create_experiment_id(prefix)
        try:
            experiment_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return experiment_dir
    raise InfrastructureFailure("could not allocate a unique experiment directory")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run comparable benchmark experiment matrices.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--experiment-dir", type=Path)
    parser.add_argument("--environment-id", default="local-smoke")
    parser.add_argument("--method", action="append", choices=METHODS)
    parser.add_argument("--workload-id", action="append")
    parser.add_argument("--category", action="append")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full-matrix", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--csv-out", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.aggregate:
            if not args.experiment_dir:
                raise InfrastructureFailure("--aggregate requires --experiment-dir")
            raw_jsonl = args.experiment_dir / "results.jsonl"
            csv_out = args.csv_out or DEFAULT_SUMMARY_ROOT / f"{args.experiment_dir.name}.csv"
            records = read_jsonl(raw_jsonl)
            export_csv(records, csv_out, overwrite=args.overwrite)
            print(json.dumps({"records": len(records), "csv_out": str(csv_out)}, indent=2, sort_keys=True))
            return 0

        manifest = load_manifest(args.manifest)
        all_workloads = list(manifest["workloads"])
        methods = list(METHODS) if args.full_matrix else _resolve_methods(args)
        workloads = all_workloads if args.full_matrix else _resolve_selection(args, all_workloads)
        repeats = args.repeats if args.repeats is not None else (5 if args.full_matrix else 1)

        if args.resume:
            if not args.experiment_dir:
                raise InfrastructureFailure("--resume requires --experiment-dir")
            experiment_dir = args.experiment_dir
            matrix = load_matrix(experiment_dir / "matrix.jsonl")
        else:
            experiment_dir = _prepare_new_experiment_dir(args.raw_root, args.experiment_dir, smoke=args.smoke)
            matrix = generate_matrix(
                workloads,
                methods,
                repeats=repeats,
                seed=args.seed,
                experiment_id=experiment_dir.name,
            )
            _write_json_new(
                experiment_dir / "environment.json",
                environment_metadata(
                    environment_id=args.environment_id,
                    manifest_path=args.manifest,
                    matrix=matrix,
                    timeout_seconds=args.timeout,
                ),
            )
            _write_matrix_new(experiment_dir / "matrix.jsonl", matrix)

        summary = run_matrix(
            matrix=matrix,
            workloads_by_id={workload["workload_id"]: workload for workload in all_workloads},
            experiment_dir=experiment_dir,
            environment_id=args.environment_id,
            timeout_seconds=args.timeout,
            resume=args.resume,
            dry_run=args.dry_run,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, InfrastructureFailure) as exc:
        print(f"experiment infrastructure failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
