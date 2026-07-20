"""Validate the preserved Kubernetes evaluation corpus without rewriting it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

from cluster_evaluation.result_compat import cpu_reconciliation, normalize_cpu_measurement
from cluster_evaluation.policies import PROFILE_RESOURCES


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUND = ROOT / "results/cluster/raw/ground-truth-39b6973-seed20260720"
DEFAULT_COMPARATIVE = ROOT / "results/cluster/raw/comparative-39b6973-seed20260720"
DEFAULT_CAPACITY = ROOT / "results/cluster/raw/capacity-39b6973-seed20260721"
RESOURCE_FIELDS = (
    "cpu_request_m",
    "cpu_limit_m",
    "memory_request_mi",
    "memory_limit_mi",
)


class ArtifactIntegrityError(RuntimeError):
    """The preserved corpus is incomplete or internally inconsistent."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactIntegrityError(f"cannot read JSON artifact {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArtifactIntegrityError(f"cannot read JSONL artifact {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError(f"{path}:{line_number}: invalid JSON") from exc
    return records


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ArtifactIntegrityError(message)


def _resource_values(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in RESOURCE_FIELDS}


def _expected_resources(profile: str) -> dict[str, Any]:
    policy = PROFILE_RESOURCES[profile]
    return {field: policy[field] for field in RESOURCE_FIELDS}


def _validate_git_commit(commit: str) -> None:
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    _require(exists.returncode == 0, f"evaluated commit is absent from repository: {commit}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    _require(ancestor.returncode == 0, f"evaluated commit is not an ancestor of HEAD: {commit}")


def _validate_pod_matrix(
    directory: Path,
    *,
    expected_count: int,
    expected_kind: str,
) -> tuple[list[dict[str, Any]], str]:
    environment = _read_json(directory / "environment.json")
    plan = _read_jsonl(directory / "matrix.jsonl")
    records = _read_jsonl(directory / "results.jsonl")
    _require(len(plan) == expected_count, f"{directory.name}: planned {len(plan)}, expected {expected_count}")
    _require(len(records) == expected_count, f"{directory.name}: recorded {len(records)}, expected {expected_count}")

    plan_ids = [item.get("run_id") for item in plan]
    record_ids = [item.get("run_id") for item in records]
    _require(len(set(plan_ids)) == len(plan_ids), f"{directory.name}: duplicate plan run IDs")
    _require(len(set(record_ids)) == len(record_ids), f"{directory.name}: duplicate result run IDs")
    _require(set(plan_ids) == set(record_ids), f"{directory.name}: plan/result run IDs do not reconcile")

    commit = str(environment.get("git_commit"))
    _require(environment.get("git_dirty") is False, f"{directory.name}: evaluation environment was dirty")
    _require(bool(commit) and commit != "None", f"{directory.name}: missing evaluated commit")

    for record in records:
        run_id = str(record["run_id"])
        _require(record.get("experiment_kind") == expected_kind, f"{run_id}: wrong experiment kind")
        _require(record.get("git_commit") == commit, f"{run_id}: commit does not match environment")
        _require(record.get("cleanup_status") in {"completed", "failed"}, f"{run_id}: cleanup status is hidden")
        run_dir = directory / "runs" / run_id
        sidecar_record = _read_json(run_dir / "record.json")
        evidence = _read_json(run_dir / "pod-evidence.json")
        metrics = _read_json(run_dir / "metrics-server-snapshots.json")
        _require(sidecar_record == record, f"{run_id}: JSONL and record sidecar differ")
        _require(evidence == record.get("kubernetes_evidence"), f"{run_id}: pod evidence differs from record")
        _require(evidence.get("requests_limits") == _resource_values(record), f"{run_id}: applied resources do not match pod evidence")
        _require(_resource_values(record) == _expected_resources(record["applied_profile"]), f"{run_id}: resources do not match profile definition")
        snapshots = metrics.get("snapshots", [])
        _require(len(snapshots) == record.get("metrics_server_snapshot_count"), f"{run_id}: metrics snapshot count differs")
        for supporting in record.get("supporting_log_paths", []):
            supporting_path = Path(supporting)
            _require(not supporting_path.is_absolute() and ".." not in supporting_path.parts, f"{run_id}: unsafe supporting path")
            _require((ROOT / supporting_path).is_file(), f"{run_id}: missing supporting artifact {supporting}")
    return records, commit


def _validate_capacity_v1(
    directory: Path, *, expected_commit: str
) -> tuple[list[dict[str, Any]], int, str]:
    environment = _read_json(directory / "environment.json")
    plan = _read_jsonl(directory / "matrix.jsonl")
    batches = _read_jsonl(directory / "results.jsonl")
    _require(environment.get("git_dirty") is False, f"{directory.name}: evaluation environment was dirty")
    _require(environment.get("git_commit") == expected_commit, f"{directory.name}: commit differs from pod matrices")
    _require(len(plan) == 108, f"{directory.name}: planned {len(plan)}, expected 108 pods")
    _require(len(batches) == 9, f"{directory.name}: recorded {len(batches)}, expected 9 batches")

    plan_ids = [item.get("run_id") for item in plan]
    pod_ids: list[str] = []
    batch_ids: list[str] = []
    for batch in batches:
        batch_id = str(batch["batch_id"])
        batch_ids.append(batch_id)
        _require(_read_json(directory / f"{batch_id}.json") == batch, f"{batch_id}: batch sidecar differs from JSONL")
        _require(batch.get("git_commit") == expected_commit, f"{batch_id}: commit differs")
        _require(batch.get("population_size") == 12, f"{batch_id}: population is not 12")
        _require(batch.get("completed", 0) + batch.get("failed", 0) == 12, f"{batch_id}: outcome count does not reconcile")
        _require(batch.get("cleanup_status") in {"completed", "failed"}, f"{batch_id}: cleanup status is hidden")
        pods = batch.get("pods", [])
        _require(len(pods) == 12, f"{batch_id}: does not retain 12 pod outcomes")
        for pod in pods:
            pod_ids.append(str(pod["run_id"]))
            expected = _expected_resources(pod["applied_profile"])
            _require(pod.get("requests_limits") == expected, f"{pod['run_id']}: capacity resources differ from profile")

    _require(len(set(batch_ids)) == len(batch_ids), f"{directory.name}: duplicate batch IDs")
    _require(len(set(plan_ids)) == len(plan_ids), f"{directory.name}: duplicate plan run IDs")
    _require(len(set(pod_ids)) == len(pod_ids), f"{directory.name}: duplicate recorded pod run IDs")
    _require(set(plan_ids) == set(pod_ids), f"{directory.name}: plan and recorded pod IDs do not reconcile")
    return batches, len(pod_ids), expected_commit


def _validate_capacity_v2(directory: Path) -> tuple[list[dict[str, Any]], int, str]:
    environment = _read_json(directory / "environment.json")
    plan = _read_jsonl(directory / "matrix.jsonl")
    batches = _read_jsonl(directory / "results.jsonl")
    commit = str(environment.get("git_commit") or "")
    _require(environment.get("protocol_version") == "2.0.0", f"{directory.name}: wrong protocol")
    _require(environment.get("git_dirty") is False, f"{directory.name}: environment was dirty")
    _require(bool(commit), f"{directory.name}: missing evaluated commit")
    _require(len(plan) == 108, f"{directory.name}: planned {len(plan)}, expected 108 pods")
    _require(len(batches) == 9, f"{directory.name}: recorded {len(batches)}, expected 9 batches")
    _require(environment.get("population_size") == 12, f"{directory.name}: wrong population")
    _require(environment.get("launch_concurrency") == 12, f"{directory.name}: wrong concurrency")
    _require(environment.get("repeats") == 3, f"{directory.name}: wrong repeat count")
    _require(environment.get("namespace") == "z2jh-context-demo", f"{directory.name}: wrong namespace")
    _require(
        environment.get("required_context") == "intent-spawner-capacity-v2",
        f"{directory.name}: wrong disposable context",
    )
    _require(
        environment.get("namespace_safety_label")
        == "z2jh-context-demo.local/disposable-capacity-v2=true",
        f"{directory.name}: missing namespace safety control",
    )
    _require(environment.get("node_count") == 1, f"{directory.name}: node count is not controlled")
    _require(
        environment.get("node_allocatable", {}).get("cpu") == "6",
        f"{directory.name}: allocatable CPU differs",
    )
    _require(
        environment.get("node_allocatable", {}).get("memory") == "6088560Ki",
        f"{directory.name}: allocatable memory differs",
    )
    _require(environment.get("profile_resources") == PROFILE_RESOURCES, f"{directory.name}: profile table differs")
    _require(environment.get("capacity_hold_seconds") == 20.0, f"{directory.name}: wrong hold")
    _require(
        environment.get("phase_sample_interval_seconds") == 0.3,
        f"{directory.name}: wrong sample interval",
    )
    _require(environment.get("resource_quota") == "none", f"{directory.name}: quota differs")
    _require(
        environment.get("method_order_by_repeat")
        == [
            ["static_default", "intent_only", "context_aware"],
            ["intent_only", "context_aware", "static_default"],
            ["context_aware", "static_default", "intent_only"],
        ],
        f"{directory.name}: method order differs",
    )
    profile = environment.get("minikube_profile", {})
    _require(
        {
            "name": profile.get("name"),
            "cpus": profile.get("cpus"),
            "memory_mb": profile.get("memory_mb"),
            "disk_size_mb": profile.get("disk_size_mb"),
            "driver": profile.get("driver"),
            "kubernetes_version": profile.get("kubernetes_version"),
            "container_runtime": profile.get("container_runtime"),
        }
        == {
            "name": "intent-spawner-capacity-v2",
            "cpus": 6,
            "memory_mb": 6144,
            "disk_size_mb": 20480,
            "driver": "docker",
            "kubernetes_version": "v1.33.1",
            "container_runtime": "containerd",
        },
        f"{directory.name}: Minikube profile differs",
    )
    image = str(environment.get("container_image") or "")
    image_metadata = environment.get("container_image_metadata", {})
    _require(commit[:12] in image, f"{directory.name}: image is not tied to commit")
    _require(
        image_metadata.get("reference") == image
        and str(image_metadata.get("local_image_id") or "").startswith("sha256:"),
        f"{directory.name}: exact local image identity is missing",
    )

    plan_ids = [str(item.get("run_id")) for item in plan]
    pod_ids: list[str] = []
    for batch in batches:
        batch_id = str(batch["batch_id"])
        sidecar = directory / "batches" / f"{batch_id}.json"
        _require(_read_json(sidecar) == batch, f"{batch_id}: batch sidecar differs from JSONL")
        _require(batch.get("capacity_schema_version") == "2.0.0", f"{batch_id}: wrong schema")
        _require(batch.get("protocol_version") == "2.0.0", f"{batch_id}: wrong protocol")
        _require(batch.get("git_commit") == commit, f"{batch_id}: commit differs")
        _require(batch.get("population_size") == 12, f"{batch_id}: population is not 12")
        _require(batch.get("launch_concurrency") == 12, f"{batch_id}: concurrency is not 12")
        _require(batch.get("hold_seconds") == 20.0, f"{batch_id}: hold differs")
        _require(
            batch.get("phase_sample_interval_seconds") == 0.3,
            f"{batch_id}: sample interval differs",
        )
        _require(batch.get("completed", 0) + batch.get("failed", 0) == 12, f"{batch_id}: outcomes do not reconcile")
        _require(batch.get("cleanup_status") == "completed", f"{batch_id}: cleanup failed")
        _require(batch.get("pending_sample_count") == len(batch.get("pending_samples", [])), f"{batch_id}: sample count differs")
        pods = batch.get("pods", [])
        _require(len(pods) == 12, f"{batch_id}: does not retain 12 pods")
        for pod in pods:
            pod_ids.append(str(pod["run_id"]))
            expected = _expected_resources(pod["applied_profile"])
            _require(pod.get("requests_limits") == expected, f"{pod['run_id']}: resources differ")
            _require(pod.get("container_image") == image, f"{pod['run_id']}: image differs")
            _require(bool(pod.get("container_image_id")), f"{pod['run_id']}: image ID missing")
            for supporting in pod.get("supporting_log_paths", []):
                supporting_path = Path(supporting)
                _require(not supporting_path.is_absolute() and ".." not in supporting_path.parts, f"{pod['run_id']}: unsafe path")
                _require((ROOT / supporting_path).is_file(), f"{pod['run_id']}: missing {supporting}")
    _require(len(set(plan_ids)) == len(plan_ids), f"{directory.name}: duplicate plan IDs")
    _require(len(set(pod_ids)) == len(pod_ids), f"{directory.name}: duplicate pod IDs")
    _require(set(plan_ids) == set(pod_ids), f"{directory.name}: plan and pods differ")
    return batches, len(pod_ids), commit


def _validate_capacity(
    directory: Path, *, historical_commit: str
) -> tuple[list[dict[str, Any]], int, str, str]:
    environment = _read_json(directory / "environment.json")
    if environment.get("protocol_version") == "2.0.0":
        batches, pods, commit = _validate_capacity_v2(directory)
        return batches, pods, commit, "reproducible_v2"
    batches, pods, commit = _validate_capacity_v1(
        directory, expected_commit=historical_commit
    )
    return batches, pods, commit, "historical_runner_unavailable"


def validate(
    ground: Path = DEFAULT_GROUND,
    comparative: Path = DEFAULT_COMPARATIVE,
    capacity: Path = DEFAULT_CAPACITY,
) -> dict[str, Any]:
    ground_records, ground_commit = _validate_pod_matrix(
        ground, expected_count=108, expected_kind="ground-truth"
    )
    comparative_records, comparative_commit = _validate_pod_matrix(
        comparative, expected_count=180, expected_kind="comparative"
    )
    _require(ground_commit == comparative_commit, "ground-truth and comparative commits differ")
    for record in ground_records + comparative_records:
        try:
            normalize_cpu_measurement(record, root=ROOT)
        except ValueError as exc:
            raise ArtifactIntegrityError(f"{record.get('run_id')}: {exc}") from exc
    reconciliation = cpu_reconciliation(ground_records + comparative_records)
    capacity_batches, capacity_pods, capacity_commit, capacity_provenance = _validate_capacity(
        capacity, historical_commit=ground_commit
    )
    _validate_git_commit(ground_commit)
    _validate_git_commit(capacity_commit)

    pod_records = ground_records + comparative_records
    return {
        "evaluated_git_commit": ground_commit,
        "ground_truth_records": len(ground_records),
        "comparative_records": len(comparative_records),
        "capacity_batches": len(capacity_batches),
        "capacity_pods": capacity_pods,
        "capacity_git_commit": capacity_commit,
        "capacity_provenance": capacity_provenance,
        "failed_pod_runs": sum(record.get("success") is not True for record in pod_records),
        "timed_out_pod_runs": sum(bool(record.get("timeout")) for record in pod_records),
        "oom_killed_pod_runs": sum(bool(record.get("oom_killed")) for record in pod_records),
        "genuine_cgroup_cpu_peak_records": reconciliation["genuine_cgroup_peak"],
        "full_window_average_cpu_records": reconciliation["average"],
        "sampled_cpu_records": reconciliation["sampled_instantaneous"],
        "unavailable_cpu_records": reconciliation["unavailable"],
        "cleanup_failures": sum(record.get("cleanup_status") != "completed" for record in pod_records)
        + sum(batch.get("cleanup_status") != "completed" for batch in capacity_batches),
        "status": "pass",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground", type=Path, default=DEFAULT_GROUND)
    parser.add_argument("--comparative", type=Path, default=DEFAULT_COMPARATIVE)
    parser.add_argument("--capacity", type=Path, default=DEFAULT_CAPACITY)
    args = parser.parse_args()
    try:
        summary = validate(args.ground.resolve(), args.comparative.resolve(), args.capacity.resolve())
    except ArtifactIntegrityError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
