"""Read-only aggregate validators needed by the final completion audit."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from evaluation_v5.image_storage.validate_evidence import (
    validate_e5_evidence,
    validate_e5_storage_evidence,
)

from .common import ROOT, RESULTS


def validate_e5_packages(root: Path = ROOT) -> dict[str, Any]:
    evidence_root = root / RESULTS / "E5"
    packages = []
    for directory in sorted(
        path
        for path in evidence_root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    ):
        relative = str(directory.relative_to(root))
        package_type = (
            "storage"
            if (directory / "derived/storage_metrics.json").is_file()
            and not (directory / "derived/functional_metrics.json").is_file()
            else "functional"
        )
        try:
            result = (
                validate_e5_storage_evidence(directory)
                if package_type == "storage"
                else validate_e5_evidence(directory)
            )
            packages.append(
                {
                    "path": relative,
                    "type": package_type,
                    "status": "PASS",
                    "validator_status": result.get("validator_status"),
                    "validation_profile": result.get("validation_profile"),
                    "execution_status": result.get("execution_status"),
                    "claim_eligible": result.get("eligible_as_current_e5_evidence", False),
                }
            )
        except Exception as exc:
            packages.append(
                {
                    "path": relative,
                    "type": package_type,
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "reason": str(exc).replace(str(root) + "/", ""),
                    "claim_eligible": False,
                }
            )
    passed = sum(row["status"] == "PASS" for row in packages)
    failed = len(packages) - passed
    return {
        "schema_version": "protocol-v5-e5-validator-summary-v1.0.0",
        "status": "PASS" if failed == 0 else "FAIL_WITH_PRESERVED_INVALID_HISTORY",
        "packages_checked": len(packages),
        "passed": passed,
        "failed": failed,
        "packages": packages,
        "is_experiment_evidence": False,
        "supports_thesis_claim": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        raise SystemExit("validator suite takes no arguments")
    result = validate_e5_packages()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
