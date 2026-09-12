from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from evaluation_v5 import freeze as freeze_module
from evaluation_v5.freeze import FreezeValidationError
from evaluation_v5.resource.preflight import _load_readiness_attestation


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _installed_descendant_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, str, dict]:
    manifest = freeze_module.build_freeze_manifest(
        freeze_id="fixture-final-freeze",
        p3_gate_status="not_retained",
        dry_run=True,
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Protocol v5 test")
    _git(repository, "config", "user.email", "protocol-v5-test@example.invalid")
    (repository / "execution.py").write_text("FROZEN = True\n", encoding="utf-8")
    _git(repository, "add", "execution.py")
    _git(repository, "commit", "-q", "-m", "frozen execution")
    frozen_sha = _git(repository, "rev-parse", "HEAD")
    frozen_tree = _git(repository, "rev-parse", "HEAD^{tree}")

    manifest["status"] = "FROZEN"
    manifest["source_control"] = {
        "frozen_execution_sha": frozen_sha,
        "frozen_execution_tree": frozen_tree,
        "git_worktree_clean": True,
        "freeze_artifact_commit_recorded_in_manifest": False,
    }
    artifact = (
        repository
        / "results_v5/protocol-v5.0.0/freezes/fixture-final-freeze/freeze-manifest.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", str(artifact.relative_to(repository)))
    _git(repository, "commit", "-q", "-m", "add freeze artifact")
    artifact_commit = _git(repository, "rev-parse", "HEAD")
    handoff = repository / "docs/evaluation/PROTOCOL_V5_FINAL_EXECUTION_HANDOFF.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("# Safe handoff\n", encoding="utf-8")
    _git(repository, "add", str(handoff.relative_to(repository)))
    _git(repository, "commit", "-q", "-m", "add safe handoff")

    monkeypatch.setattr(freeze_module, "ROOT", repository)
    monkeypatch.setattr(
        freeze_module,
        "DEFAULT_FREEZE_ROOT",
        repository / "results_v5/protocol-v5.0.0/freezes",
    )
    monkeypatch.setattr(freeze_module, "FREEZE_CUSTODY_ROOT", repository)
    snapshot = copy.deepcopy(manifest["configuration_snapshot"])
    monkeypatch.setattr(
        freeze_module,
        "build_configuration_snapshot",
        lambda **_kwargs: copy.deepcopy(snapshot),
    )
    return artifact, frozen_sha, artifact_commit, manifest


def test_final_freeze_discovers_non_circular_artifact_commit_and_allows_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, frozen_sha, artifact_commit, _ = _installed_descendant_freeze(
        tmp_path, monkeypatch
    )
    verified = freeze_module.verify_production_freeze(artifact)
    assert verified.frozen_execution_sha == frozen_sha
    assert verified.freeze_artifact_commit_sha == artifact_commit
    assert verified.identity["freeze_artifact_commit_sha"] == artifact_commit


def test_final_freeze_rejects_post_freeze_executable_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, _, _, _ = _installed_descendant_freeze(tmp_path, monkeypatch)
    repository = freeze_module.ROOT
    (repository / "execution.py").write_text("FROZEN = False\n", encoding="utf-8")
    _git(repository, "add", "execution.py")
    _git(repository, "commit", "-q", "-m", "prohibited mutation")
    with pytest.raises(FreezeValidationError, match="post-freeze executable"):
        freeze_module.verify_production_freeze(artifact)


def test_external_e4_attestation_is_bound_to_both_freeze_shas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, frozen_sha, artifact_commit, _ = _installed_descendant_freeze(
        tmp_path, monkeypatch
    )
    verified = freeze_module.verify_production_freeze(artifact)
    attestation = {
        "schema_version": "protocol-v5-e4-readiness-attestation-v1.0.0",
        "protocol_version": "5.0.0",
        "attestation_id": "synthetic-readiness-fixture",
        "attested_at_utc": "2026-09-11T00:00:00Z",
        "attestor_pseudonym": "custodian-fixture",
        "freeze": {
            "freeze_id": verified.freeze_id,
            "freeze_manifest_sha256": verified.artifact_sha256,
            "frozen_execution_sha": frozen_sha,
            "freeze_artifact_commit_sha": artifact_commit,
        },
        "oracle": {
            "package_id": "oracle-fixture",
            "package_path": "/external/oracle-fixture",
            "sha256sums_sha256": "a" * 64,
            "manual_review_status": "APPROVED",
        },
        "execution_image": {
            "reference": "example.invalid/intent-spawner@sha256:" + "b" * 64,
            "runtime_verified": True,
        },
        "cluster": {
            "context": "intent-spawner-eval-v5",
            "cluster_identity": "intent-spawner-eval-v5",
            "namespace": "z2jh-context-demo",
            "kubernetes_version": "v1.30.0",
            "kubernetes_version_sha256": "c" * 64,
            "disposable_nonproduction": True,
            "api_access_verified": True,
        },
        "node_capacity": {
            "node_identity": "e4-node-v1",
            "node_name": "e4-node",
            "node_uid": "fixture-node-uid",
            "node_count": 1,
            "allocatable_cpu_millicores": 4000,
            "allocatable_memory_mib": 8192,
            "allocatable_gpu_count": 0,
            "allocatable_gpu_resource": None,
            "dedicated": True,
        },
        "cgroup": {
            "version": "v2",
            "controllers": ["cpu", "memory", "pids"],
            "required_files_verified": True,
        },
        "assertions": {
            "facts_collected_read_only": True,
            "no_experiment_trials_executed": True,
            "attestation_external_to_frozen_source": True,
            "not_confirmatory_dataset_material": True,
        },
    }
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")
    loaded = _load_readiness_attestation(path, freeze=verified)
    assert loaded["freeze"]["frozen_execution_sha"] == frozen_sha

    forged = copy.deepcopy(attestation)
    forged["freeze"]["freeze_artifact_commit_sha"] = "f" * 40
    path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="freeze mismatch"):
        _load_readiness_attestation(path, freeze=verified)

    duplicate_key_document = json.dumps(attestation).replace(
        '"schema_version":',
        '"schema_version": "forged-duplicate", "schema_version":',
        1,
    )
    path.write_text(duplicate_key_document, encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        _load_readiness_attestation(path, freeze=verified)
