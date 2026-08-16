#!/usr/bin/env python3
"""Fail on high-confidence credential formats in version-controlled candidates."""

from __future__ import annotations

import re
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
}
MAX_FILE_BYTES = 8 * 1024 * 1024


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def main() -> int:
    findings = []
    scanned = 0
    for path in candidate_paths():
        if path.relative_to(ROOT).parts[0].startswith(".tmp-ppt-review."):
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        scanned += 1
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((path.relative_to(ROOT), label))
    if findings:
        for path, label in findings:
            print(f"potential {label}: {path}", file=sys.stderr)
        return 1
    print(f"Secret scan passed: {scanned} text files, high-confidence formats only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
