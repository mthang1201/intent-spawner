#!/usr/bin/env python3
"""
Task E Final Validation Live Test Matrix Runner.
Executes and captures evidence for all 9 required test matrix items against
JupyterHub deployed on Kubernetes (orbstack) in namespace z2jh-context-demo.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT_DIR / "results" / "task-e-validation-2026-08-07"
NAMESPACE = os.environ.get("NAMESPACE", "z2jh-context-demo")
RELEASE = os.environ.get("RELEASE", "context-demo")

def redact(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = re.sub(r'(_xsrf=)[^;\s"&]+', r'\1[REDACTED_COOKIE]', text)
    text = re.sub(r'(jupyterhub-session-id=)[^;\s"&]+', r'\1[REDACTED_SESSION]', text)
    text = re.sub(r'("X-XSRFToken":\s*")[^"]+', r'\1[REDACTED_TOKEN]', text)
    text = re.sub(r'(_xsrf=)[^&\s"]+', r'\1[REDACTED_XSRF]', text)
    text = re.sub(r'(password=)[^&\s"]+', r'\1[REDACTED_PASSWORD]', text)
    text = re.sub(r'(token=)[^&\s"]+', r'\1[REDACTED_TOKEN]', text)
    return text

def run_cmd(cmd: list[str], check: bool = True, cwd: Path = ROOT_DIR) -> str:
    print(f"+ {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and res.returncode != 0:
        print(f"Command failed (exit {res.returncode}):\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
        raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
    return res.stdout

def write_evidence(folder: str, filename: str, content: str):
    path = EVIDENCE_DIR / folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(content), encoding="utf-8")
    print(f"Saved evidence: {path.relative_to(ROOT_DIR)}")

def helm_render(values_files: list[str]) -> str:
    cmd = [
        "helm", "template", RELEASE, "jupyterhub/jupyterhub",
        "--version", "4.0.0",
        "--namespace", NAMESPACE
    ]
    for vf in values_files:
        cmd.extend(["--values", str(ROOT_DIR / vf)])
    return run_cmd(cmd)

print("Starting Task E Live Validation Suite...")
print(f"Evidence Directory: {EVIDENCE_DIR}")
