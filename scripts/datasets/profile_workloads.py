#!/usr/bin/env python3
"""Profile workloads against container profiles with cgroups v2 resource measurement.

This script determines the MINIMAL profile that completes each problem without OOM
by executing inside a Jupyter ipykernel within a container matching the catalog image digest,
under increasing cgroup v2 memory and CPU limits (small -> medium -> large).
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

import yaml

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from recommender.candidate_corpus import DEFAULT_PROFILE_DEFINITIONS, parse_memory_to_bytes
from recommender.rule_based import DEFAULT_CATALOG_PATH, load_image_catalog

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("profile_workloads")

RUNNER_VERSION = "profile-workloads-v1.0.0"

# Ordered profiles by memory limit ascending (small -> medium -> large)
PROFILE_ORDER = ("small", "medium", "large")

# Fallback imports for known image IDs when measuring baseline
DEFAULT_IMAGE_IMPORTS: Dict[str, str] = {
    "minimal-python": "import sys, os",
    "scipy-data-science": "import numpy as np\nimport pandas as pd\nimport scipy\nimport sklearn\nimport matplotlib.pyplot as plt\n",
    "pytorch-deep-learning": "import torch\nimport torchvision\nimport numpy as np\n",
    "tensorflow-deep-learning": "import tensorflow as tf\nimport keras\nimport numpy as np\n",
}


@dataclass
class ProfileSpec:
    slug: str
    display_name: str
    cpu_limit: float
    mem_limit_bytes: int
    mem_limit_raw: str


def get_sorted_profiles(
    custom_profiles: Optional[Dict[str, Any]] = None,
) -> List[ProfileSpec]:
    """Return profiles sorted strictly ascending by memory limit bytes."""
    defs = custom_profiles if custom_profiles is not None else DEFAULT_PROFILE_DEFINITIONS
    specs: List[ProfileSpec] = []
    for slug in PROFILE_ORDER:
        if slug in defs:
            p = defs[slug]
            cpu_limit = float(p.get("cpu_limit", 1.0))
            mem_raw = str(p.get("mem_limit", "1G"))
            mem_bytes = parse_memory_to_bytes(mem_raw)
            specs.append(
                ProfileSpec(
                    slug=slug,
                    display_name=str(p.get("display_name", slug)),
                    cpu_limit=cpu_limit,
                    mem_limit_bytes=mem_bytes,
                    mem_limit_raw=mem_raw,
                )
            )
    specs.sort(key=lambda s: (s.mem_limit_bytes, s.cpu_limit))
    return specs


@dataclass
class TrialResult:
    profile_tried: str
    outcome: str  # "completed" | "oom" | "timeout" | "error" | "infra_error"
    peak_mib: Optional[float]
    cpu_seconds: Optional[float]
    wall_seconds: Optional[float]
    peak_method: str  # "memory.peak" | "memory.current_sample_max"
    error_traceback: Optional[str] = None
    oom_kill_count: int = 0
    anon_peak_mib: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "profile_tried": self.profile_tried,
            "outcome": self.outcome,
            "peak_mib": self.peak_mib,
            "anon_peak_mib": self.anon_peak_mib,
            "cpu_seconds": self.cpu_seconds,
            "wall_seconds": self.wall_seconds,
        }
        if self.outcome in ("error", "infra_error") and self.error_traceback:
            data["error_traceback"] = self.error_traceback[:500]
        return data


class BaseWorkloadRunner:
    """Interface for workload runner (real Docker or fake/mock for tests)."""

    def run_trial(
        self,
        *,
        problem_id: str,
        image_reference: str,
        profile: ProfileSpec,
        code_to_run: str,
        timeout_seconds: int,
        work_dir: Optional[Path] = None,
        input_files: Optional[Dict[str, Any]] = None,
    ) -> TrialResult:
        raise NotImplementedError

    def measure_baseline(
        self,
        *,
        image_id: str,
        image_reference: str,
        import_code: str,
        profile: ProfileSpec,
        runs: int = 5,
        timeout_seconds: int = 60,
    ) -> float:
        raise NotImplementedError


# In-container Python harness code that launches ipykernel, executes code, and monitors cgroups v2
IN_CONTAINER_HARNESS_CODE = r'''import json
import os
from pathlib import Path
import sys
import threading
import time

CGROUP_ROOT = Path("/sys/fs/cgroup")

# 1. Determine cgroup v2 memory peak availability
has_peak = (CGROUP_ROOT / "memory.peak").is_file()
peak_method = "memory.peak" if has_peak else "memory.current_sample_max"
if not has_peak:
    sys.stderr.write("WARNING: /sys/fs/cgroup/memory.peak not available, falling back to 50ms sampling of memory.current\n")
    sys.stderr.flush()

# Fallback sampling thread & anon memory monitor
sample_max = 0
anon_sample_max = 0
stop_event = threading.Event()

def _read_anon_bytes() -> int:
    try:
        content = (CGROUP_ROOT / "memory.stat").read_text()
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "anon":
                return int(parts[1])
    except Exception:
        pass
    return 0

def _sample_loop():
    global sample_max, anon_sample_max
    cur_file = CGROUP_ROOT / "memory.current"
    stat_file = CGROUP_ROOT / "memory.stat"
    while not stop_event.wait(0.05):
        try:
            val = int(cur_file.read_text().strip())
            if val > sample_max:
                sample_max = val
        except Exception:
            pass
        try:
            content = stat_file.read_text()
            for line in content.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "anon":
                    an_val = int(parts[1])
                    if an_val > anon_sample_max:
                        anon_sample_max = an_val
                    break
        except Exception:
            pass

sampler_thread = threading.Thread(target=_sample_loop, daemon=True)
sampler_thread.start()

def _read_cpu_usec() -> int:
    try:
        content = (CGROUP_ROOT / "cpu.stat").read_text()
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "usage_usec":
                return int(parts[1])
    except Exception:
        pass
    return 0

def _read_oom_kill() -> int:
    try:
        content = (CGROUP_ROOT / "memory.events").read_text()
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "oom_kill":
                return int(parts[1])
    except Exception:
        pass
    return 0

cpu_before = _read_cpu_usec()
oom_before = _read_oom_kill()
time_start = time.monotonic()

# 2. Read payload from /workload/payload.json
with open("/workload/payload.json", "r", encoding="utf-8") as f:
    payload = json.load(f)

code_to_run = payload["code"]
timeout_sec = payload.get("timeout_seconds", 120)

outcome = "completed"
error_tb = None

# 3. Launch ipykernel via jupyter_client
import jupyter_client

km = jupyter_client.KernelManager(kernel_name="python3")
kc = None
try:
    km.start_kernel()
    kc = km.blocking_client()
    kc.start_channels()
    kc.wait_for_ready(timeout=30)

    # Execute code in kernel
    msg_id = kc.execute(code_to_run)
    reply = None
    start_wait = time.monotonic()
    while time.monotonic() - start_wait < timeout_sec:
        if not km.is_alive():
            break
        try:
            reply = kc.get_shell_msg(timeout=0.5)
            break
        except Exception:
            continue

    if reply is None:
        if not km.is_alive():
            outcome = "error"
            error_tb = "Kernel process died unexpectedly"
        else:
            outcome = "timeout"
            error_tb = f"Kernel execution timed out after {timeout_sec}s"
    else:
        content = reply.get("content", {})
        status = content.get("status")
        if status == "error":
            outcome = "error"
            ename = content.get("ename", "ExecutionError")
            evalue = content.get("evalue", "")
            traceback_lines = content.get("traceback", [])
            clean_tb = "\n".join(traceback_lines[-6:]) if traceback_lines else f"{ename}: {evalue}"
            error_tb = f"{ename}: {evalue}\n{clean_tb}"
except (TimeoutError, Exception) as exc:
    exc_type = type(exc).__name__
    if "Timeout" in exc_type or "Empty" in exc_type:
        outcome = "timeout"
        error_tb = f"Kernel execution timed out after {timeout_sec}s: {exc}"
    else:
        outcome = "error"
        error_tb = str(exc)
finally:
    if kc:
        try:
            kc.stop_channels()
        except Exception:
            pass
    try:
        km.shutdown_kernel(now=True)
    except Exception:
        pass

# 4. Stop sampler & compute metrics
time_end = time.monotonic()
stop_event.set()
sampler_thread.join(timeout=1.0)

cpu_after = _read_cpu_usec()
oom_after = _read_oom_kill()
oom_delta = max(0, oom_after - oom_before)
cpu_seconds = max(0.0, (cpu_after - cpu_before) / 1_000_000.0)
wall_seconds = max(0.0, time_end - time_start)

# Memory peak
peak_bytes = 0
if has_peak:
    try:
        peak_bytes = int((CGROUP_ROOT / "memory.peak").read_text().strip())
    except Exception:
        peak_bytes = sample_max
else:
    peak_bytes = sample_max

peak_mib = round(peak_bytes / (1024 * 1024), 4)

# Anon peak
anon_peak_bytes = anon_sample_max
try:
    final_anon = _read_anon_bytes()
    if final_anon > anon_peak_bytes:
        anon_peak_bytes = final_anon
except Exception:
    pass
anon_peak_mib = round(anon_peak_bytes / (1024 * 1024), 4) if anon_peak_bytes > 0 else None

if oom_delta > 0:
    outcome = "oom"

result = {
    "outcome": outcome,
    "peak_mib": peak_mib,
    "anon_peak_mib": anon_peak_mib,
    "cpu_seconds": round(cpu_seconds, 6),
    "wall_seconds": round(wall_seconds, 6),
    "peak_method": peak_method,
    "error_traceback": error_tb,
    "oom_kill_count": oom_delta,
}

with open("/workload/result.json", "w", encoding="utf-8") as f:
    json.dump(result, f)

print(json.dumps(result))
'''


def check_docker_daemon(timeout: float = 3.0) -> Tuple[bool, str]:
    """Check whether the Docker daemon is responding within timeout seconds.

    Returns:
        (True, version_or_info) if daemon is reachable and responding.
        (False, error_message) if Docker CLI is missing, daemon is stopped,
        socket is unreachable, or health check timed out.
    """
    if not shutil.which("docker"):
        return False, "Docker CLI binary not found on PATH"
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        err_msg = (proc.stderr or proc.stdout).strip()
        return False, f"Docker daemon not ready (exit code {proc.returncode}): {err_msg}"
    except subprocess.TimeoutExpired:
        return False, f"Docker daemon health check timed out after {timeout}s"
    except Exception as exc:
        return False, f"Docker daemon health check error: {exc}"


class DockerWorkloadRunner(BaseWorkloadRunner):
    """Executes workload in actual Docker container with cgroup v2 limits."""

    def __init__(self, cgroup_version_check: bool = True):
        self.cgroup_version_check = cgroup_version_check

    def _cleanup_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    def run_trial(
        self,
        *,
        problem_id: str,
        image_reference: str,
        profile: ProfileSpec,
        code_to_run: str,
        timeout_seconds: int,
        work_dir: Optional[Path] = None,
        input_files: Optional[Dict[str, Any]] = None,
    ) -> TrialResult:
        # Lazy daemon connectivity check with fast timeout before container lifecycle
        daemon_ok, daemon_msg = check_docker_daemon(timeout=3.0)
        if not daemon_ok:
            logger.warning(
                "Docker daemon check failed for problem '%s' profile '%s': %s",
                problem_id,
                profile.slug,
                daemon_msg,
            )
            return TrialResult(
                profile_tried=profile.slug,
                outcome="infra_error",
                peak_mib=None,
                cpu_seconds=None,
                wall_seconds=0.0,
                peak_method="memory.peak",
                error_traceback=f"Infrastructure error (Docker daemon unavailable): {daemon_msg}",
            )

        container_name = f"profile-{problem_id.replace('_', '-').lower()}-{profile.slug}-{uuid.uuid4().hex[:6]}"
        tmpdir = Path(tempfile.mkdtemp(prefix="profile_run_"))

        try:
            # 1. Write in-container harness and payload
            harness_path = tmpdir / "harness.py"
            harness_path.write_text(IN_CONTAINER_HARNESS_CODE, encoding="utf-8")

            payload = {
                "code": code_to_run,
                "timeout_seconds": timeout_seconds,
            }
            (tmpdir / "payload.json").write_text(json.dumps(payload), encoding="utf-8")

            # 2. Write any input files
            if input_files:
                for rel_path, content in input_files.items():
                    target_file = tmpdir / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(content, bytes):
                        target_file.write_bytes(content)
                    else:
                        target_file.write_text(str(content), encoding="utf-8")

            # 3. Formulate docker run command with cgroup v2 limits
            cmd = [
                "docker",
                "run",
                "--name",
                container_name,
                "--memory",
                str(profile.mem_limit_bytes),
                "--memory-swap",
                str(profile.mem_limit_bytes),
                "--cpus",
                str(profile.cpu_limit),
                "-v",
                f"{tmpdir}:/workload",
                "-w",
                "/workload",
                image_reference,
                "python3",
                "/workload/harness.py",
            ]

            t_start = time.monotonic()
            proc = None
            timed_out = False
            stdout = ""
            stderr = ""

            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout_seconds + 30,  # Grace period beyond in-container timeout
                )
                stdout = proc.stdout
                stderr = proc.stderr
                exit_code = proc.returncode
            except subprocess.TimeoutExpired as te:
                timed_out = True
                exit_code = -1
                stdout = te.stdout or ""
                stderr = te.stderr or ""
                self._cleanup_container(container_name)

            wall_seconds = time.monotonic() - t_start

            if timed_out:
                return TrialResult(
                    profile_tried=profile.slug,
                    outcome="timeout",
                    peak_mib=None,
                    cpu_seconds=None,
                    wall_seconds=round(wall_seconds, 6),
                    peak_method="memory.peak",
                    error_traceback="Container execution timed out",
                )

            # Check if container died of OOM (exit code 137 or docker inspect)
            if exit_code == 137:
                return TrialResult(
                    profile_tried=profile.slug,
                    outcome="oom",
                    peak_mib=round(profile.mem_limit_bytes / (1024 * 1024), 4),
                    cpu_seconds=None,
                    wall_seconds=round(wall_seconds, 6),
                    peak_method="memory.peak",
                    error_traceback=f"OOM killed with exit code {exit_code}",
                    oom_kill_count=1,
                )

            # Check if result.json was produced
            result_json_path = tmpdir / "result.json"
            if result_json_path.is_file():
                try:
                    res_data = json.loads(result_json_path.read_text(encoding="utf-8"))
                    return TrialResult(
                        profile_tried=profile.slug,
                        outcome=res_data.get("outcome", "completed"),
                        peak_mib=res_data.get("peak_mib"),
                        cpu_seconds=res_data.get("cpu_seconds"),
                        wall_seconds=res_data.get("wall_seconds", round(wall_seconds, 6)),
                        peak_method=res_data.get("peak_method", "memory.peak"),
                        error_traceback=res_data.get("error_traceback"),
                        oom_kill_count=res_data.get("oom_kill_count", 0),
                        anon_peak_mib=res_data.get("anon_peak_mib"),
                    )
                except Exception as exc:
                    logger.warning("Failed parsing result.json: %s", exc)

            # Fallback if exit code non-zero without result.json
            if exit_code != 0:
                # Distinguish Infra error vs OOM vs Code Error
                infra_keywords = (
                    "cannot connect to the docker daemon",
                    "is the docker daemon running",
                    "error during connect",
                    "unable to find image",
                    "error response from daemon",
                    "connection refused",
                    "broken pipe",
                    "no space left on device",
                    "failed to create shim task",
                )
                stderr_lower = stderr.lower()
                if any(kw in stderr_lower for kw in infra_keywords):
                    return TrialResult(
                        profile_tried=profile.slug,
                        outcome="infra_error",
                        peak_mib=None,
                        cpu_seconds=None,
                        wall_seconds=round(wall_seconds, 6),
                        peak_method="memory.peak",
                        error_traceback=f"Infrastructure error: {stderr.strip()[:1000]}",
                    )

                # Distinguish OOM vs Error
                if "OOM" in stderr or "Killed" in stderr or exit_code == 137:
                    return TrialResult(
                        profile_tried=profile.slug,
                        outcome="oom",
                        peak_mib=round(profile.mem_limit_bytes / (1024 * 1024), 4),
                        cpu_seconds=None,
                        wall_seconds=round(wall_seconds, 6),
                        peak_method="memory.peak",
                        error_traceback=stderr.strip() or f"Process killed with code {exit_code}",
                        oom_kill_count=1,
                    )
                return TrialResult(
                    profile_tried=profile.slug,
                    outcome="error",
                    peak_mib=None,
                    cpu_seconds=None,
                    wall_seconds=round(wall_seconds, 6),
                    peak_method="memory.peak",
                    error_traceback=(stderr or stdout).strip()[:1000] or f"Process failed with exit code {exit_code}",
                )

            # Exit code 0 but no result.json: parse stdout
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        res_data = json.loads(line)
                        return TrialResult(
                            profile_tried=profile.slug,
                            outcome=res_data.get("outcome", "completed"),
                            peak_mib=res_data.get("peak_mib"),
                            cpu_seconds=res_data.get("cpu_seconds"),
                            wall_seconds=res_data.get("wall_seconds", round(wall_seconds, 6)),
                            peak_method=res_data.get("peak_method", "memory.peak"),
                            error_traceback=res_data.get("error_traceback"),
                            oom_kill_count=res_data.get("oom_kill_count", 0),
                            anon_peak_mib=res_data.get("anon_peak_mib"),
                        )
                    except Exception:
                        pass

            return TrialResult(
                profile_tried=profile.slug,
                outcome="error",
                peak_mib=None,
                cpu_seconds=None,
                wall_seconds=round(wall_seconds, 6),
                peak_method="memory.peak",
                error_traceback=f"Unexpected termination without metrics: {stderr}",
            )
        finally:
            self._cleanup_container(container_name)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def measure_baseline(
        self,
        *,
        image_id: str,
        image_reference: str,
        import_code: str,
        profile: ProfileSpec,
        runs: int = 5,
        timeout_seconds: int = 60,
    ) -> float:
        """Measure baseline memory peak for idle kernel + imports 5 times and return median."""
        daemon_ok, daemon_msg = check_docker_daemon(timeout=3.0)
        if not daemon_ok:
            raise RuntimeError(f"Cannot measure baseline: Docker daemon unavailable ({daemon_msg})")
        peaks: List[float] = []
        logger.info(
            "Measuring BASELINE for image '%s' (%s) across %d runs...",
            image_id,
            image_reference,
            runs,
        )
        for i in range(runs):
            res = self.run_trial(
                problem_id=f"baseline-{image_id}-{i}",
                image_reference=image_reference,
                profile=profile,
                code_to_run=import_code,
                timeout_seconds=timeout_seconds,
            )
            if res.outcome == "completed" and res.peak_mib is not None:
                peaks.append(res.peak_mib)
            else:
                logger.warning(
                    "Baseline run %d for %s did not complete cleanly: outcome=%s, err=%s",
                    i + 1,
                    image_id,
                    res.outcome,
                    res.error_traceback,
                )

        if not peaks:
            logger.warning("All baseline runs failed for image %s, defaulting to 100.0 MiB", image_id)
            return 100.0

        median_peak = round(float(statistics.median(peaks)), 4)
        logger.info("Baseline peak for %s: %s MiB (runs: %s)", image_id, median_peak, peaks)
        return median_peak


class FakeWorkloadRunner(BaseWorkloadRunner):
    """Deterministic mock runner for unit tests."""

    def __init__(
        self,
        behavior_map: Optional[Dict[Tuple[str, str], TrialResult]] = None,
        default_baseline: float = 120.0,
    ):
        # behavior_map: (problem_id, profile_slug) -> TrialResult
        self.behavior_map = behavior_map or {}
        self.default_baseline = default_baseline
        self.history: List[Dict[str, Any]] = []

    def run_trial(
        self,
        *,
        problem_id: str,
        image_reference: str,
        profile: ProfileSpec,
        code_to_run: str,
        timeout_seconds: int,
        work_dir: Optional[Path] = None,
        input_files: Optional[Dict[str, Any]] = None,
    ) -> TrialResult:
        key = (problem_id, profile.slug)
        self.history.append(
            {
                "problem_id": problem_id,
                "profile": profile.slug,
                "image_reference": image_reference,
            }
        )
        if key in self.behavior_map:
            return self.behavior_map[key]

        # Default fallback behavior: completed
        return TrialResult(
            profile_tried=profile.slug,
            outcome="completed",
            peak_mib=150.0,
            cpu_seconds=0.5,
            wall_seconds=1.0,
            peak_method="memory.peak",
            anon_peak_mib=110.0,
        )

    def measure_baseline(
        self,
        *,
        image_id: str,
        image_reference: str,
        import_code: str,
        profile: ProfileSpec,
        runs: int = 5,
        timeout_seconds: int = 60,
    ) -> float:
        return self.default_baseline


def extract_imports_from_code(code: str) -> str:
    """Extract all import statements from code using regex."""
    import_lines = []
    for line in code.splitlines():
        line_s = line.strip()
        if line_s.startswith("import ") or line_s.startswith("from "):
            import_lines.append(line_s)
    return "\n".join(import_lines) + "\n" if import_lines else "import sys, os\n"


def prepare_problem_execution(
    source: str,
    item: Dict[str, Any],
    catalog: Dict[str, Any],
    repo_root: Path = PROJECT_ROOT,
) -> Tuple[str, str, str, str, Optional[Dict[str, Any]]]:
    """Prepare problem metadata: (problem_id, image_id, image_ref, code_to_run, input_files)."""
    problem_id = str(item.get("problem_id") or item.get("id"))
    image_id = str(item.get("image_id") or item.get("image") or "scipy-data-science")

    # Image reference from catalog
    images = catalog.get("images", {})
    if image_id in images:
        image_reference = images[image_id]["reference"]
    else:
        # Fallback to minimal
        default_img = catalog.get("default_image", "minimal-python")
        image_reference = images.get(default_img, {}).get("reference", "")

    input_files: Optional[Dict[str, Any]] = None
    code_to_run = ""
    import_code = ""

    if source == "ds1000":
        # Check if individual JSON file exists
        ds1000_json_path = repo_root / "benchmarks_v5" / "raw_sources" / "ds1000" / f"{problem_id}.json"
        if ds1000_json_path.is_file():
            try:
                full_prob = json.loads(ds1000_json_path.read_text(encoding="utf-8"))
            except Exception:
                full_prob = item
        else:
            full_prob = item

        reference_code = full_prob.get("reference_code", "")
        test_code = full_prob.get("test_code") or full_prob.get("code_context", "")
        prompt = full_prob.get("prompt", "")

        # Cross-framework upgrade check: if problem code requires tensorflow or torch,
        # ensure container image is upgraded from scipy-data-science
        full_code_context = f"{test_code}\n{reference_code}\n{prompt}"
        if ("tensorflow" in full_code_context or "import tensorflow" in full_code_context or "tf." in full_code_context) and image_id == "scipy-data-science":
            image_id = "tensorflow-deep-learning"
            image_reference = images.get(image_id, {}).get("reference", image_reference)
        elif ("torch" in full_code_context or "import torch" in full_code_context) and image_id == "scipy-data-science":
            image_id = "pytorch-deep-learning"
            image_reference = images.get(image_id, {}).get("reference", image_reference)

        # Formulate execution script in kernel
        # Run test_code, then test_execution(reference_code) and test_string(reference_code) if defined
        code_to_run = f"""# DS-1000 Official Evaluation Wrapper
import sys

# Backward compatibility shims for DS-1000 tests on modern library versions
try:
    import matplotlib.axes
    import matplotlib.patches
    import numpy as _np
    _orig_axvspan = matplotlib.axes.Axes.axvspan
    def _compat_axvspan(self, xmin, xmax, ymin=0, ymax=1, **kwargs):
        verts = _np.array([[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin]])
        poly = matplotlib.patches.Polygon(verts, **kwargs)
        self.add_patch(poly)
        return poly
    matplotlib.axes.Axes.axvspan = _compat_axvspan
except Exception:
    pass

try:
    import numpy as _np
    if not hasattr(_np, 'NAN'):
        _np.NAN = _np.nan
    if not hasattr(_np, 'in1d'):
        _np.in1d = _np.isin
except Exception:
    pass

try:
    import pandas as _pd
    if not hasattr(_pd.DataFrame, 'append'):
        def _compat_append(self, other, ignore_index=False, verify_integrity=False, sort=False):
            return _pd.concat([self, other], ignore_index=ignore_index)
        _pd.DataFrame.append = _compat_append

    _orig_read_csv = _pd.read_csv
    def _compat_read_csv(*args, **kwargs):
        if kwargs.get('delim_whitespace'):
            del kwargs['delim_whitespace']
            kwargs['sep'] = '\\s+'
        return _orig_read_csv(*args, **kwargs)
    _pd.read_csv = _compat_read_csv

    _orig_df_replace = _pd.DataFrame.replace
    def _compat_df_replace(self, *args, **kwargs):
        if 'method' in kwargs:
            m = kwargs.pop('method')
            if m in ('ffill', 'pad'):
                return self.ffill()
            elif m == 'bfill':
                return self.bfill()
        return _orig_df_replace(self, *args, **kwargs)
    _pd.DataFrame.replace = _compat_df_replace

    _orig_set_axis = _pd.DataFrame.set_axis
    def _compat_set_axis(self, labels, axis=0, copy=None, inplace=False):
        res = _orig_set_axis(self, labels, axis=axis, copy=copy)
        if inplace:
            self._mgr = res._mgr
            return None
        return res
    _pd.DataFrame.set_axis = _compat_set_axis
except Exception:
    pass

try:
    import scipy.integrate as _sp_int
    if not hasattr(_sp_int, 'quadrature') and hasattr(_sp_int, 'fixed_quad'):
        _sp_int.quadrature = _sp_int.fixed_quad
except Exception:
    pass

{test_code}

# Execute reference solution through test harness
if 'test_string' in globals():
    try:
        test_string({repr(reference_code)})
    except Exception:
        pass

if 'test_execution' in globals():
    test_execution({repr(reference_code)})
else:
    exec({repr(reference_code)}, globals())
"""
        import_code = extract_imports_from_code(test_code + "\n" + reference_code)

    elif source == "arcade":
        input_files = item.get("input_files", {})
        setup_cells = item.get("setup_cells", [])
        target_cell = item.get("target_cell") or item.get("reference_code", "")
        cells = list(setup_cells) + [target_cell]
        code_to_run = "\n\n".join(cells)
        import_code = extract_imports_from_code(code_to_run)
    else:
        # Generic python
        code_to_run = item.get("reference_code") or item.get("code", "")
        import_code = extract_imports_from_code(code_to_run)

    return problem_id, image_id, image_reference, code_to_run, input_files, import_code


def profile_single_problem(
    *,
    problem_id: str,
    source: str,
    image_id: str,
    image_reference: str,
    code_to_run: str,
    input_files: Optional[Dict[str, Any]],
    runner: BaseWorkloadRunner,
    profiles: Sequence[ProfileSpec],
    repeats: int = 3,
    timeout_seconds: int = 120,
    baseline_median: float = 0.0,
    baseline_by_profile: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Execute profile stepping across repeats for one problem."""
    all_repeat_records: List[Dict[str, Any]] = []
    min_passing_per_repeat: List[str] = []
    problem_had_error = False
    problem_had_timeout = False
    problem_had_infra_error = False
    all_peaks: List[float] = []
    all_anon_peaks: List[float] = []
    last_peak_method = "memory.peak"

    for r_idx in range(1, repeats + 1):
        repeat_min_passing: Optional[str] = None
        for profile in profiles:
            trial = runner.run_trial(
                problem_id=f"{problem_id}-r{r_idx}",
                image_reference=image_reference,
                profile=profile,
                code_to_run=code_to_run,
                timeout_seconds=timeout_seconds,
                input_files=input_files,
            )

            rec = trial.to_dict()
            rec["repeat_index"] = r_idx
            all_repeat_records.append(rec)
            last_peak_method = trial.peak_method

            if trial.peak_mib is not None:
                all_peaks.append(trial.peak_mib)
            if trial.anon_peak_mib is not None:
                all_anon_peaks.append(trial.anon_peak_mib)

            if trial.outcome == "completed":
                repeat_min_passing = profile.slug
                # Stop on first passing profile for this repeat
                break
            elif trial.outcome == "oom":
                # Advance to next larger profile
                continue
            elif trial.outcome == "infra_error":
                # Infrastructure failure (daemon down, socket disconnected): abort repeat
                problem_had_infra_error = True
                break
            elif trial.outcome == "error":
                # Non-memory code error: DO NOT step up, stop repeat
                problem_had_error = True
                break
            elif trial.outcome == "timeout":
                problem_had_timeout = True
                break

        if repeat_min_passing:
            min_passing_per_repeat.append(repeat_min_passing)
        if problem_had_infra_error:
            # Stop remaining repeats immediately if infrastructure failed
            break

    # Determine final_min_passing_profile (= largest profile among passing repeats)
    # Order: small (0) < medium (1) < large (2)
    profile_ranks = {p.slug: i for i, p in enumerate(profiles)}
    final_min_passing: Optional[str] = None

    if len(min_passing_per_repeat) == repeats:
        # All repeats completed with some profile: pick the maximum
        final_min_passing = max(
            min_passing_per_repeat, key=lambda s: profile_ranks.get(s, -1)
        )
        status = "ok"
    elif problem_had_infra_error:
        status = "infra_error"
    elif problem_had_error:
        status = "error"
    elif problem_had_timeout:
        status = "timeout"
    else:
        # Tried all profiles up to large and still OOM on some/all repeats
        status = "infeasible_over_capacity"

    peak_mib_max = round(max(all_peaks), 4) if all_peaks else None
    anon_peak_mib_max = round(max(all_anon_peaks), 4) if all_anon_peaks else None

    # Determine baseline for the matching profile (to avoid pagecache skew between small vs large)
    target_profile_slug = final_min_passing or profiles[0].slug
    effective_baseline = baseline_median
    if baseline_by_profile and target_profile_slug in baseline_by_profile:
        effective_baseline = baseline_by_profile[target_profile_slug]
    elif baseline_median > 0:
        effective_baseline = baseline_median
    elif baseline_by_profile:
        effective_baseline = next(iter(baseline_by_profile.values()), 0.0)

    # Check import_dominated: true if peak_mib_max < 1.10 * effective_baseline
    import_dominated = False
    if peak_mib_max is not None and effective_baseline > 0:
        import_dominated = peak_mib_max < (1.10 * effective_baseline)

    return {
        "problem_id": problem_id,
        "source": source,
        "image_id": image_id,
        "repeats": all_repeat_records,
        "min_passing_profile_per_repeat": min_passing_per_repeat,
        "final_min_passing_profile": final_min_passing,
        "peak_mib_max": peak_mib_max,
        "anon_peak_mib_max": anon_peak_mib_max,
        "baseline_profile": target_profile_slug,
        "baseline_peak_mib_median": round(effective_baseline, 4),
        "import_dominated": import_dominated,
        "status": status,
        "peak_method": last_peak_method,
        "runner_version": RUNNER_VERSION,
    }


def generate_pilot_markdown_report(
    *,
    source: str,
    records: List[Dict[str, Any]],
    output_path: Path,
) -> str:
    """Generate summary Markdown report for pilot runs."""
    total = len(records)
    if total == 0:
        content = f"# Pilot Profile Report - {source.upper()}\n\nNo records processed.\n"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return content

    final_profiles = Counter([r.get("final_min_passing_profile") or r.get("status") for r in records])
    status_counts = Counter([r.get("status") for r in records])
    import_dom_count = sum(1 for r in records if r.get("import_dominated"))
    image_counts = Counter([r.get("image_id") for r in records])

    # Image x Final Profile breakdown
    img_profile_table: Dict[str, Counter] = {}
    for r in records:
        img = r.get("image_id", "unknown")
        fp = r.get("final_min_passing_profile") or r.get("status")
        img_profile_table.setdefault(img, Counter())[fp] += 1

    lines = [
        f"# Profile Pilot Workload Report — `{source}`",
        "",
        f"- **Generated at:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"- **Total Workloads:** {total}",
        f"- **Runner Version:** `{RUNNER_VERSION}`",
        "",
        "## 1. Final Min Passing Profile Distribution",
        "",
        "| Profile / Status | Count | Share (%) |",
        "| :--- | :---: | :---: |",
    ]

    for p, c in sorted(final_profiles.items(), key=lambda x: -x[1]):
        share = round((c / total) * 100, 2)
        lines.append(f"| **`{p}`** | {c} | {share}% |")

    lines.extend([
        "",
        "## 2. Import Overhead & Dominance",
        "",
        f"- **Import-dominated workloads (`peak < 1.10 × baseline`):** {import_dom_count} / {total} ({round(import_dom_count / total * 100, 2)}%)",
        "",
        "## 3. Breakdown by Image Catalog ID",
        "",
        "| Image ID | Total | small | medium | large | non-ok / infeasible |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ])

    for img, counts in sorted(img_profile_table.items()):
        tot_img = sum(counts.values())
        s = counts.get("small", 0)
        m = counts.get("medium", 0)
        l = counts.get("large", 0)
        other = tot_img - (s + m + l)
        lines.append(f"| `{img}` | {tot_img} | {s} | {m} | {l} | {other} |")

    lines.extend([
        "",
        "## 4. Overall Status Summary",
        "",
        "| Status | Count |",
        "| :--- | :---: |",
    ])
    for st, sc in sorted(status_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| `{st}` | {sc} |")

    content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return content


def run_pipeline(
    *,
    input_file: Path,
    source: str,
    output_file: Path,
    repeats: int = 3,
    timeout_seconds: int = 120,
    limit: Optional[int] = None,
    resume: bool = False,
    pilot: bool = False,
    pilot_report_path: Optional[Path] = None,
    runner: Optional[BaseWorkloadRunner] = None,
    custom_profiles: Optional[Dict[str, Any]] = None,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
) -> List[Dict[str, Any]]:
    """Run profiling pipeline over problems in input JSONL."""
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    profiles = get_sorted_profiles(custom_profiles)
    catalog = load_image_catalog(catalog_path)
    if runner is None:
        runner = DockerWorkloadRunner()

    # Determine already processed problem_ids if resume is active
    completed_ids = set()
    existing_records: List[Dict[str, Any]] = []
    if resume and output_file.is_file():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        pid = rec.get("problem_id")
                        if pid:
                            completed_ids.add(pid)
                            existing_records.append(rec)
            logger.info("Resume active: loaded %d existing problem records", len(completed_ids))
        except Exception as exc:
            logger.warning("Could not read existing output for resume: %s", exc)

    # Read input items
    with open(input_file, "r", encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    if limit is not None and limit > 0:
        items = items[:limit]

    logger.info("Total items to process: %d (skipping %d completed)", len(items), len(completed_ids))

    # Pre-measure baseline per (image_id, profile.slug) encountered
    baseline_cache: Dict[Tuple[str, str], float] = {}

    def get_baseline_map(img_id: str, img_ref: str) -> Dict[str, float]:
        b_map: Dict[str, float] = {}
        base_code = DEFAULT_IMAGE_IMPORTS.get(img_id, "import sys, os\n")
        for prof in profiles:
            key = (img_id, prof.slug)
            if key not in baseline_cache:
                b_med = runner.measure_baseline(
                    image_id=img_id,
                    image_reference=img_ref,
                    import_code=base_code,
                    profile=prof,
                    runs=5,
                    timeout_seconds=min(60, timeout_seconds),
                )
                baseline_cache[key] = b_med
            b_map[prof.slug] = baseline_cache[key]
        return b_map

    output_file.parent.mkdir(parents=True, exist_ok=True)
    out_mode = "a" if (resume and output_file.is_file()) else "w"

    processed_records: List[Dict[str, Any]] = list(existing_records)

    with open(output_file, out_mode, encoding="utf-8") as out_f:
        for idx, item in enumerate(items, start=1):
            pid, image_id, image_ref, code, in_files, import_code = prepare_problem_execution(
                source, item, catalog, repo_root=PROJECT_ROOT
            )

            if resume and pid in completed_ids:
                logger.info("[%d/%d] Skipping already completed problem '%s'", idx, len(items), pid)
                continue

            baseline_by_prof = get_baseline_map(image_id, image_ref)

            logger.info(
                "[%d/%d] Profiling problem '%s' (image: %s, repeats: %d)...",
                idx,
                len(items),
                pid,
                image_id,
                repeats,
            )

            record = profile_single_problem(
                problem_id=pid,
                source=source,
                image_id=image_id,
                image_reference=image_ref,
                code_to_run=code,
                input_files=in_files,
                runner=runner,
                profiles=profiles,
                repeats=repeats,
                timeout_seconds=timeout_seconds,
                baseline_by_profile=baseline_by_prof,
            )

            out_f.write(json.dumps(record) + "\n")
            out_f.flush()
            completed_ids.add(pid)
            processed_records.append(record)

            logger.info(
                "[%d/%d] Problem '%s' done: final_profile=%s, status=%s, peak_max=%.2f MiB",
                idx,
                len(items),
                pid,
                record["final_min_passing_profile"],
                record["status"],
                record["peak_mib_max"] or 0.0,
            )

    if pilot:
        report_path = (
            pilot_report_path
            if pilot_report_path is not None
            else PROJECT_ROOT / "docs" / "reports" / f"profile_pilot_{source}.md"
        )
        report_content = generate_pilot_markdown_report(
            source=source,
            records=processed_records,
            output_path=report_path,
        )
        print("\n" + "=" * 60)
        print("PILOT EXECUTION SUMMARY")
        print("=" * 60)
        print(report_content)
        print("=" * 60 + "\n")

    return processed_records


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark workloads under container profiles to find min passing profile without OOM."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input JSONL problem file")
    parser.add_argument("--source", required=True, choices=["ds1000", "arcade"], help="Benchmark source")
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL profile results")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repeat trials per problem (default: 3)")
    parser.add_argument(
        "--timeout-seconds", type=int, default=120, help="Timeout in seconds per execution (default: 120)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of problems to evaluate")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output JSONL")
    parser.add_argument("--pilot", action="store_true", help="Print pilot distribution statistics and write markdown report")
    parser.add_argument(
        "--pilot-report-path",
        type=Path,
        default=None,
        help="Custom output path for pilot markdown report (default: docs/reports/profile_pilot_{source}.md)",
    )

    args = parser.parse_args(argv)

    try:
        run_pipeline(
            input_file=args.input,
            source=args.source,
            output_file=args.output,
            repeats=args.repeats,
            timeout_seconds=args.timeout_seconds,
            limit=args.limit,
            resume=args.resume,
            pilot=args.pilot,
            pilot_report_path=args.pilot_report_path,
        )
        return 0
    except Exception as exc:
        logger.exception("Profiling pipeline failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
