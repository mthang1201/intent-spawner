"""Offline Protocol-v5 reproduction. Exit 2 preserves and reports audit failures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

from . import SCHEMA_VERSION
from .checks import inspect
from .common import Inputs, ROOT, RESULTS, file_sha256, read_json, seal, verify_seal, write_json, write_bytes
from .reproduce import analyze
from .reporting import figures, render_report


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def implementation_files(root: Path) -> dict:
    paths = [p for name in ("evaluation_v5", "evaluation_v4") for p in (root / name).rglob("*.py")]
    paths += [root / p for p in ("scripts/validate-portable-evidence.py", "requirements-analysis.txt", "requirements-dev.txt")]
    return {str(p.relative_to(root)): file_sha256(p) for p in sorted(paths)}


def ensure_run(inputs: Inputs, run_id: str, output_root: Path) -> tuple[Path, dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", run_id):
        raise ValueError("run ID must be a safe single directory name")
    target = output_root.resolve() / run_id
    for relative in inputs.lock["packages"]:
        source = inputs.root / relative
        if target == source or target in source.parents or source in target.parents:
            raise ValueError("output must not overlap source evidence")
    if any(target == inputs.root / name or target in (inputs.root / name).parents for name in inputs.files):
        raise ValueError("output overlaps reviewed inputs")
    contract = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "protocol_version": "5.0.0",
                "input_inventory_sha256": file_sha256(inputs.root / inputs.lock_path),
                "implementation_sha256": implementation_files(inputs.root)}
    run_file = target / "run.json"
    if target.exists():
        previous = read_json(run_file)
        if any(previous.get(k) != value for k, value in contract.items()):
            raise ValueError("run identity/input/code changed; choose a new V5_RUN_ID")
        return target, previous
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=inputs.root, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=inputs.root, capture_output=True, text=True)
    metadata = {**contract, "created_at_utc": utc_now(), "audit_git_revision": revision.stdout.strip(),
                "audit_git_dirty": bool(dirty.stdout), "python_version": platform.python_version(),
                "platform": platform.platform(), "dependencies": {p: importlib.metadata.version(p)
                 for p in ("numpy", "scipy", "pandas", "statsmodels", "matplotlib", "PyYAML", "jsonschema")},
                "backend_semantics_modified": False, "new_experiments_executed": False,
                "command": "python -m evaluation_v5.final_audit", "evidence_boundary": "reviewed current snapshot"}
    target.mkdir(parents=True, exist_ok=False)
    write_json(run_file, metadata)
    return target, metadata


def stage_metadata(run: dict, stage: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "protocol_version": "5.0.0", "stage": stage,
            "run_id": run["run_id"], "created_at_utc": utc_now(),
            "input_inventory_sha256": run["input_inventory_sha256"],
            "audit_git_revision": run["audit_git_revision"],
            "implementation_sha256": run["implementation_sha256"],
            "source_run_manifest": "../run.json"}


def run_workflow(inputs: Inputs, command: str, run_id: str, output_root: Path,
                 *, report_path: Path | None = None) -> dict:
    target, run = ensure_run(inputs, run_id, output_root)
    # Never reuse output against changed evidence, even if the inventory file itself is unchanged.
    current_errors = inputs.verify()
    if current_errors and (target / "validation").exists():
        raise ValueError("reviewed evidence changed; prior stages cannot be reused")
    validation_dir = target / "validation"
    if validation_dir.exists():
        verify_seal(validation_dir)
        audit = read_json(validation_dir / "audit.json")
    else:
        audit = inspect(inputs)
        validation_dir.mkdir()
        write_json(validation_dir / "audit.json", audit)
        seal(validation_dir, stage_metadata(run, "validate"))
    if command == "validate":
        return {"run_id": run_id, "output": str(target), "audit_status": audit["audit_status"],
                "exit_code": 2 if audit["audit_status"] == "FAIL" else 0}
    analysis_dir = target / "analysis"
    if analysis_dir.exists():
        verify_seal(analysis_dir)
        derived = read_json(analysis_dir / "regeneration.json")
    else:
        derived = analyze(inputs, audit, analysis_dir)
        seal(analysis_dir, stage_metadata(run, "analyze"))
    if command == "analyze":
        return {"run_id": run_id, "output": str(target), "regeneration_status": derived["status"],
                "exit_code": 2 if audit["audit_status"] == "FAIL" or derived["status"] == "FAIL" else 0}
    figures_dir = target / "figures"
    if figures_dir.exists():
        verify_seal(figures_dir)
        rendered = read_json(figures_dir / "figure-regeneration.json")
    else:
        rendered = figures(inputs, derived, analysis_dir, figures_dir)
        seal(figures_dir, stage_metadata(run, "figures"))
    if command == "figures":
        return {"run_id": run_id, "output": str(target), "figure_status": rendered["status"],
                "exit_code": 2 if any(x == "FAIL" for x in (audit["audit_status"], derived["status"], rendered["status"])) else 0}
    final_dir = target / "report"
    if final_dir.exists():
        verify_seal(final_dir)
        final = read_json(final_dir / "audit.json")
    else:
        final = json.loads(json.dumps(audit))
        final["audit_run_id"] = run_id
        if target.is_relative_to(inputs.root):
            final["audit_output_relative_path"] = str(target.relative_to(inputs.root))
        for index, phase in ((6, derived), (7, rendered)):
            unavailable = index == 6 and any(p["status"] == "UNVERIFIED" for p in derived["packages"])
            verdict = "FAIL" if phase["status"] == "FAIL" else "UNVERIFIED" if unavailable or audit.get("input_integrity_blocked") else "PASS"
            final["checks"][index].update(verdict=verdict,
                reason=("Available current-schema raw-to-derived outputs regenerated; unavailable and legacy analyses remain explicitly bounded."
                        if index == 6 else "Current derived tables/figures regenerated; differences from preserved historical artifacts are retained."),
                details=phase["comparisons"] + ([{"unverified_legacy_packages": [p["path"] for p in derived["packages"] if p["status"] == "UNVERIFIED"]}] if unavailable else []))
        final["audit_status"] = "FAIL" if any(c["verdict"] == "FAIL" for c in final["checks"]) else "INCOMPLETE"
        # Bind all completed stages; never permit a report based on an unchecked partial directory.
        final["stage_manifest_sha256"] = {name: file_sha256(target / name / "manifest.json") for name in ("validation", "analysis", "figures")}
        final_dir.mkdir()
        write_json(final_dir / "audit.json", final)
        destination = final_dir / "PROTOCOL_V5_FINAL_REPORT.md"
        write_bytes(destination, render_report(inputs, final, derived, rendered, destination).encode())
        seal(final_dir, stage_metadata(run, "audit"))
    if report_path is not None:
        if any((inputs.root / relative) == report_path or (inputs.root / relative) in report_path.parents
               for relative in inputs.lock["packages"]):
            raise ValueError("report publication must not modify a source evidence package")
        write_bytes(report_path, render_report(inputs, final, derived, rendered, report_path).encode())
    if inputs.verify() and not audit.get("input_integrity_blocked"):
        raise ValueError("source input hashes changed during reproduction")
    return {"run_id": run_id, "output": str(target), "audit_status": final["audit_status"],
            "confirmatory_status": final["confirmatory_status"], "exit_code": 2 if final["audit_status"] == "FAIL" else 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "analyze", "figures", "audit", "verify"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", type=Path, default=ROOT / RESULTS / "final-audit")
    parser.add_argument("--report", type=Path, help="Also exclusively create a report at this path (audit only).")
    parser.add_argument("--package", type=Path, help="Sealed stage to verify without creating outputs.")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            if not args.package:
                raise ValueError("verify requires --package")
            verify_seal(args.package.resolve())
            print(json.dumps({"status": "PASS"}))
            return 0
        if args.report and args.command != "audit":
            raise ValueError("--report is only valid with audit")
        run_id = args.run_id or "final-audit-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        result = run_workflow(Inputs(), args.command, run_id, args.output_root,
                              report_path=args.report.resolve() if args.report else None)
        print(json.dumps(result, indent=2, sort_keys=True))
        return result["exit_code"]
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc).replace(str(ROOT) + "/", "")}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
