"""Verify every tracked raw-evidence file against the committed SHA-256 manifest."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "evaluation" / "RAW_EVIDENCE_SHA256SUMS.txt"
BASELINE_MANIFEST = (
    ROOT / "docs" / "evaluation" / "RAW_EVIDENCE_SHA256SUMS.before-0ffbd9a.txt"
)
RAW_PREFIXES = ("experiments/raw/", "results/cluster/raw/")


def tracked_raw_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "experiments/raw", "results/cluster/raw"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line.startswith(RAW_PREFIXES))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_lines(paths: list[str]) -> list[str]:
    return [f"{sha256(ROOT / path)}  {path}" for path in paths]


def read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: malformed checksum line") from exc
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{path}:{line_number}: invalid SHA-256")
        if relative in entries:
            raise ValueError(f"{path}:{line_number}: duplicate path {relative}")
        entries[relative] = digest
    return entries


def verify(path: Path = DEFAULT_MANIFEST) -> dict[str, object]:
    expected = read_manifest(path)
    tracked = tracked_raw_paths()
    missing_from_manifest = sorted(set(tracked) - set(expected))
    untracked_manifest_entries = sorted(set(expected) - set(tracked))
    mismatches = [
        relative
        for relative in sorted(set(tracked) & set(expected))
        if sha256(ROOT / relative) != expected[relative]
    ]
    if missing_from_manifest or untracked_manifest_entries or mismatches:
        raise ValueError(
            "raw integrity failure: "
            f"missing_from_manifest={missing_from_manifest}, "
            f"untracked_manifest_entries={untracked_manifest_entries}, "
            f"checksum_mismatches={mismatches}"
        )
    return {
        "status": "pass",
        "manifest": str(path.relative_to(ROOT)),
        "verified_files": len(tracked),
        "manifest_sha256": sha256(path),
    }


def verify_baseline(path: Path = BASELINE_MANIFEST) -> dict[str, object]:
    expected = read_manifest(path)
    tracked = set(tracked_raw_paths())
    missing_or_untracked = sorted(set(expected) - tracked)
    mismatches = [
        relative
        for relative, digest in sorted(expected.items())
        if relative in tracked and sha256(ROOT / relative) != digest
    ]
    if missing_or_untracked or mismatches:
        raise ValueError(
            "baseline raw integrity failure: "
            f"missing_or_untracked={missing_or_untracked}, "
            f"checksum_mismatches={mismatches}"
        )
    return {
        "baseline_manifest": str(path.relative_to(ROOT)),
        "baseline_verified_files": len(expected),
        "baseline_manifest_sha256": sha256(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline-manifest", type=Path, default=BASELINE_MANIFEST)
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve()
    if args.generate:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("\n".join(manifest_lines(tracked_raw_paths())) + "\n", encoding="utf-8")
        print(f"wrote {manifest}")
        return 0
    try:
        summary = verify(manifest)
        summary.update(verify_baseline(args.baseline_manifest.resolve()))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
