from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from evaluation_v4.dataset import file_sha256
from evaluation_v5.offline.recommenders import P1FrozenAdapter
from evaluation_v5.offline.runner import COMPLETION_FILENAME, run_offline_recommendations
from evaluation_v5.offline.validate_evidence import (
    OfflineEvidenceValidationError,
    validate_offline_evidence,
)
from evaluation_v5.split_dataset import load_development_split


class FailingP1Adapter(P1FrozenAdapter):
    def recommend(self, case, *, seed):
        del case, seed
        raise RuntimeError("provider detail must remain outside evidence")


@pytest.fixture(scope="module")
def valid_evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    result_dir = tmp_path_factory.mktemp("v5-offline-valid") / "run"
    run_offline_recommendations(
        load_development_split(),
        result_dir=result_dir,
        system_ids=("P1", "P2"),
        repeats=5,
        seed=8128,
        frozen_configuration={"snapshot": "validator-test-v1"},
    )
    return result_dir


def _copy_evidence(source: Path, tmp_path: Path) -> Path:
    target = tmp_path / "run"
    shutil.copytree(source, target)
    return target


def _records_path(evidence_dir: Path) -> Path:
    return evidence_dir / "raw" / "recommendations.jsonl"


def _read_records(evidence_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in _records_path(evidence_dir).read_text(encoding="utf-8").splitlines()
    ]


def _write_records(evidence_dir: Path, records: list[dict]) -> None:
    records_path = _records_path(evidence_dir)
    records_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    completion_path = evidence_dir / "report" / COMPLETION_FILENAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["records"] = len(records)
    completion["error_records"] = sum(record["status"] == "error" for record in records)
    completion["recommendations_jsonl_sha256"] = file_sha256(records_path)
    completion_path.write_text(
        json.dumps(completion, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_validator_accepts_complete_production_p1_p2_evidence(valid_evidence: Path):
    result = validate_offline_evidence(valid_evidence)

    assert result["status"] == "PASS"
    assert result["systems"] == ["P1", "P2"]
    assert result["requested_repeats"] == 5
    assert result["effective_repeats"] == {"P1": 1, "P2": 1}
    assert result["records_validated"] == 36
    assert result["metric_input_sufficiency"] == "PASS"
    assert result["statistical_interpretation_performed"] is False


def test_validator_accepts_complete_failure_rows(tmp_path: Path):
    result_dir = tmp_path / "failed-run"
    split = load_development_split()
    run_offline_recommendations(
        split,
        result_dir=result_dir,
        adapters={"P1": FailingP1Adapter()},
        system_ids=("P1",),
        frozen_configuration={"snapshot": "failure-evidence-test-v1"},
    )

    result = validate_offline_evidence(result_dir)
    assert result["status"] == "PASS"
    assert result["records_validated"] == len(split.bundle.cases)
    assert result["error_records"] == len(split.bundle.cases)


def test_validator_rejects_duplicate_logical_row(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    records.append(records[0])
    _write_records(evidence, records)

    with pytest.raises(OfflineEvidenceValidationError, match="duplicate logical"):
        validate_offline_evidence(evidence)


def test_validator_rejects_missing_expected_row(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    _write_records(evidence, records[:-1])

    with pytest.raises(OfflineEvidenceValidationError, match="incomplete"):
        validate_offline_evidence(evidence)


def test_validator_rejects_record_provenance_mismatch(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    records[0]["provenance_fingerprint"] = "0" * 64
    _write_records(evidence, records)

    with pytest.raises(OfflineEvidenceValidationError, match="provenance"):
        validate_offline_evidence(evidence)


def test_validator_rejects_malformed_jsonl(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    with _records_path(evidence).open("ab") as handle:
        handle.write(b'{"malformed":}\n')

    with pytest.raises(OfflineEvidenceValidationError, match="malformed JSON"):
        validate_offline_evidence(evidence)


def test_validator_rejects_truncated_jsonl(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records_path = _records_path(evidence)
    records_path.write_bytes(records_path.read_bytes().rstrip(b"\n"))

    with pytest.raises(OfflineEvidenceValidationError, match="unterminated"):
        validate_offline_evidence(evidence)


def test_validator_rejects_invalid_candidate_id(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    record = records[0]
    record["predicted_candidate_id"] = "small-untrusted-image"
    record["predicted_profile_id"] = "small"
    record["predicted_image_id"] = "untrusted-image"
    record["metric_inputs"]["predicted_candidate_id"] = "small-untrusted-image"
    record["metric_inputs"]["predicted_profile_id"] = "small"
    record["metric_inputs"]["predicted_image_id"] = "untrusted-image"
    _write_records(evidence, records)

    with pytest.raises(OfflineEvidenceValidationError, match="unknown candidate"):
        validate_offline_evidence(evidence)


def test_validator_rejects_incomplete_p2_trace(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    p2_record = next(
        record
        for record in records
        if record["system_id"] == "P2" and not record["fallback"]["used"]
    )
    p2_record["structured_intent"] = None
    _write_records(evidence, records)

    with pytest.raises(OfflineEvidenceValidationError, match="StructuredIntent"):
        validate_offline_evidence(evidence)


def test_validator_rejects_wrong_dataset_checksum(valid_evidence: Path):
    wrong_split = replace(
        load_development_split(), source_file_sha256="0" * 64
    )

    with pytest.raises(OfflineEvidenceValidationError, match="dataset/split"):
        validate_offline_evidence(valid_evidence, split=wrong_split)


def test_validator_rejects_mismatched_case_input_checksum(valid_evidence: Path, tmp_path: Path):
    evidence = _copy_evidence(valid_evidence, tmp_path)
    records = _read_records(evidence)
    records[0]["input_identity"]["case_sha256"] = "0" * 64
    _write_records(evidence, records)

    with pytest.raises(OfflineEvidenceValidationError, match="case/input checksum"):
        validate_offline_evidence(evidence)
