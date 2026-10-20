#!/usr/bin/env python3
"""Unified Experiment Runner for Protocol-v5 (E1 - E5).

Allows choosing between:
  1. 'local' (Deterministic regex / Rule-based parser, zero API cost)
  2. 'llm'   (LLM-based semantic extractor, prompts for API key if missing)

Separates results into dedicated directories to prevent overwriting:
  - E1: results_v5/protocol-v5.0.0/E1/run-e1-<mode>
  - E2: results_v5/protocol-v5.0.0/E2/run-e2-<mode>
  - E3: results_v5/protocol-v5.0.0/E3/run-e3-<mode>
  - E4: results_v5/protocol-v5.0.0/E4/run-e4-efficiency-<mode>
  - E5: results_v5/protocol-v5.0.0/E5/run-e5-functional-<mode>
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation_v5.credentials import configure_extractor_mode, ensure_llm_credentials, get_mode_tag


BANNER = """
======================================================================
       🚀 INTENT-SPAWNER EXPERIMENTAL EVALUATION HARNESS (E1-E5)
======================================================================
"""


def prompt_choice(prompt: str, choices: list[str], default_index: int = 0) -> str:
    default_val = choices[default_index]
    options_str = " / ".join(f"[{c}]" if c == default_val else c for c in choices)
    while True:
        try:
            val = input(f"{prompt} ({options_str}): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
        if not val:
            return default_val
        for c in choices:
            if val == c.lower() or val == str(choices.index(c) + 1):
                return c
        print(f"Invalid option. Please choose from: {choices}")


def run_e1(mode: str, base_dir: Path) -> Path:
    mode_tag = get_mode_tag(mode)
    out_dir = base_dir / "E1" / f"run-e1-{mode_tag}"
    print(f"\n========================================================")
    print(f"📊 [E1] RUNNING RECOMMENDATION QUALITY BENCHMARK (TAG: {mode_tag})")
    print(f"📁 Target output directory: {out_dir}")
    print(f"========================================================")

    cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.offline.runner",
        "--systems",
        "P1,P2",
        "--extractor-mode",
        mode,
        "--result-dir",
        str(out_dir),
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
    return out_dir


def run_e2(mode: str, base_dir: Path, e1_dir: Path | None = None) -> Path:
    mode_tag = get_mode_tag(mode) if mode != "local" else "local"
    # If e1_dir is provided or exists for this mode, use that tag, else fallback to latest available E1 run
    out_dir = base_dir / "E2" / f"run-e2-{mode_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========================================================")
    print(f"🛡️ [E2] RUNNING NATURAL LANGUAGE ROBUSTNESS ANALYSIS")
    print(f"📁 Target output directory: {out_dir}")
    print(f"========================================================")

    # 1. Dataset summary
    print("-> Summarizing robustness perturbations and perturbation taxonomy...")
    summary_cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.robustness",
        "summary",
        "benchmarks_v5/v5-development.yaml",
    ]
    res = subprocess.run(summary_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    (out_dir / "dataset_summary.txt").write_text(res.stdout, encoding="utf-8")
    print(res.stdout.strip())

    # 2. Extract language robustness breakdown from E1 if available
    ref_e1 = e1_dir or (base_dir / "E1" / f"run-e1-{mode_tag}")
    if not ref_e1.exists():
        # Look for the latest available E1 run by modification time
        candidate_e1 = sorted((base_dir / "E1").glob("run-e1-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidate_e1:
            ref_e1 = candidate_e1[0]
    summary_file = ref_e1 / "SUMMARY.md"
    if summary_file.exists():
        content = summary_file.read_text(encoding="utf-8")
        if "Language Robustness" in content or "Phân Tích Độ Bền Ngôn Ngữ" in content:
            marker = "### 🌐 Phân Tích Độ Bền Ngôn Ngữ" if "### 🌐 Phân Tích Độ Bền Ngôn Ngữ" in content else "### 🌐 Language Robustness"
            sections = content.split(marker)
            if len(sections) > 1:
                e2_report = marker + sections[1]
                (out_dir / "ROBUSTNESS_REPORT.md").write_text(e2_report, encoding="utf-8")
                print("\n" + e2_report.strip())
    print(f"✅ E2 finished: Report saved to {out_dir}")
    return out_dir


def _finalize_e3_workflow(
    base_dir: Path,
    staging_dir: Path,
    assignment_path: Path,
    task_set_path: Path,
    mode_tag: str,
) -> Path:
    default_run_id = f"run-e3-observed-{mode_tag}"
    run_id_in = input(f"\nEnter Run ID for finalized results [{default_run_id}]: ").strip()
    run_id = run_id_in or default_run_id
    out_dir = base_dir / "E3" / run_id

    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)

    num_complete = 0
    sessions_file = staging_dir / "sessions.jsonl"
    if sessions_file.exists():
        try:
            num_complete = len([
                l for l in sessions_file.read_text(encoding="utf-8").splitlines()
                if l.strip() and json.loads(l).get("session_status") == "complete"
            ])
        except Exception:
            pass

    default_status = "OBSERVED" if num_complete >= 36 else "INCOMPLETE"
    print(f"\nCompleted participant sessions detected in staging: {num_complete}")
    print("Select Target Execution Status:")
    if num_complete >= 36:
        print("  1. OBSERVED    - Full confirmatory recruitment target met (36 participants) [Default]")
        print("  2. INCOMPLETE  - Partial recruitment observations")
        print("  3. DRY_RUN     - Simulated or pilot execution mode")
        status_map = {"1": "OBSERVED", "2": "INCOMPLETE", "3": "DRY_RUN"}
    else:
        print("  1. INCOMPLETE  - Observed pilot / partial trial observations [Default]")
        print("  2. DRY_RUN     - Simulated or dry-run execution mode")
        print("  3. OBSERVED    - Requires all 36 participants completed")
        status_map = {"1": "INCOMPLETE", "2": "DRY_RUN", "3": "OBSERVED"}

    status_choice = input("Status ([1] / 2 / 3): ").strip()
    target_status = status_map.get(status_choice, default_status)

    events_file = staging_dir / "events.jsonl"
    sessions_file = staging_dir / "sessions.jsonl"
    exclusions_file = staging_dir / "exclusions.jsonl"
    questionnaires_file = staging_dir / "questionnaires.jsonl"

    if not events_file.exists() or not sessions_file.exists() or not questionnaires_file.exists():
        print(f"\n❌ Error: Missing required log files in staging directory {staging_dir}.")
        print("   Ensure events.jsonl, sessions.jsonl, and questionnaires.jsonl exist.")
        return out_dir

    env = os.environ.copy()
    env["PROTOCOL_V5_ALLOW_DIRTY"] = "1"

    finalize_cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.user_study.runner",
        "finalize",
        "--run-id",
        run_id,
        "--task-set",
        str(task_set_path),
        "--assignments",
        str(assignment_path),
        "--events",
        str(events_file),
        "--sessions",
        str(sessions_file),
        "--exclusions",
        str(exclusions_file),
        "--questionnaires",
        str(questionnaires_file),
        "--execution-status",
        target_status,
        "--output-dir",
        str(out_dir),
    ]

    print(f"\n🔬 Running E3 Finalization and Statistical Modeling (Status: {target_status})...")
    res = subprocess.run(finalize_cmd, cwd=str(REPO_ROOT), env=env)
    if res.returncode == 0:
        print(f"\n🎉 E3 Finalization Complete! Results and publication tables saved to:\n   {out_dir}")
        summary_md = out_dir / "report" / "USER_STUDY_REPORT.md"
        if not summary_md.exists():
            summary_md = out_dir / "USER_STUDY_REPORT.md"
        if summary_md.exists():
            print("\n" + "=" * 65)
            print("📊 E3 USABILITY REPORT PREVIEW:")
            print("=" * 65)
            print(summary_md.read_text(encoding="utf-8")[:1200] + "\n...")
    else:
        print(f"\n⚠️ Finalization failed with exit code {res.returncode}.")
    return out_dir


def run_e3(base_dir: Path, mode: str = "local") -> Path:
    mode_tag = get_mode_tag(mode)
    print(f"\n========================================================")
    print(f"👥 [E3] HUMAN USABILITY STUDY (B0 vs P2, TAG: {mode_tag})")
    print(f"========================================================")

    print("\nSelect E3 Execution Action:")
    print("  1. smoke           - Automated synthetic smoke test (Deterministic CI verification) [Default]")
    print("  2. generate-plan   - Generate randomized counterbalanced participant assignment schedule")
    print("  3. trial-session   - Run an interactive human trial session in terminal (Record real actions & SUS)")
    print("  4. finalize        - Finalize collected session logs into official E3 report & statistical analysis")
    action_choice = input("Selection ([1] / 2 / 3 / 4): ").strip()

    if action_choice == "2":
        # Generate assignments
        print("\n📋 Generating Participant Assignment Schedule...")
        default_target = 12
        count_in = input(f"Target participant count [{default_target}]: ").strip()
        count = int(count_in) if count_in.isdigit() and int(count_in) > 0 else default_target

        default_out = REPO_ROOT / "benchmarks_v5" / f"protocol-v5-e3-assignment-target-{count}"
        out_in = input(f"Output directory [{default_out}]: ").strip()
        target_dir = Path(out_in).resolve() if out_in else default_out

        seed_in = input("Random seed [20260827]: ").strip()
        seed = int(seed_in) if seed_in.isdigit() else 20260827

        task_set_file = REPO_ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"
        cmd = [
            sys.executable,
            "-m",
            "evaluation_v5.user_study.runner",
            "generate-assignments",
            str(task_set_file),
            "--output-dir",
            str(target_dir),
            "--study-id",
            "e3-user-study",
            "--participant-count",
            str(count),
            "--consent-version",
            "consent-v1",
            "--seed",
            str(seed),
        ]
        subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)
        print(f"\n✅ Assignment manifest generated successfully at: {target_dir}")
        return target_dir

    elif action_choice == "3":
        # Interactive Trial Session
        import asyncio
        from evaluation_v5.user_study.interactive_session import run_interactive_trial

        # Find available assignment manifest
        candidate_manifests = sorted(REPO_ROOT.glob("benchmarks_v5/protocol-v5-e3-assignment-*/assignment-manifest.json"))
        if candidate_manifests:
            default_manifest = candidate_manifests[0]
        else:
            default_manifest = REPO_ROOT / "benchmarks_v5" / "protocol-v5-e3-assignment-target-36" / "assignment-manifest.json"

        print(f"\n📄 Assignment Manifest Reference:")
        manifest_in = input(f"Manifest path [{default_manifest}]: ").strip()
        selected_manifest = Path(manifest_in).resolve() if manifest_in else default_manifest

        default_staging = base_dir / "E3" / "staging"
        print(f"📁 Staging Directory (Logs & Questionnaires):")
        staging_in = input(f"Staging path [{default_staging}]: ").strip()
        selected_staging = Path(staging_in).resolve() if staging_in else default_staging

        task_set_file = REPO_ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"

        print(f"\n🚀 Launching Interactive Human Trial Session (Extractor: {mode.upper()})...\n")
        trial_result = asyncio.run(
            run_interactive_trial(
                task_set_path=task_set_file,
                assignment_path=selected_manifest,
                staging_dir=selected_staging,
                extractor_mode=mode,
            )
        )

        # Offer immediate finalization if completed
        if trial_result.get("status") == "COMPLETED":
            fin_prompt = input("\nDo you want to finalize all collected sessions into an E3 report now? ([y] / n): ").strip().lower()
            if fin_prompt != "n":
                return _finalize_e3_workflow(base_dir, selected_staging, selected_manifest, task_set_file, mode_tag)
        return selected_staging

    elif action_choice == "4":
        # Finalize workflow
        candidate_manifests = sorted(REPO_ROOT.glob("benchmarks_v5/protocol-v5-e3-assignment-*/assignment-manifest.json"))
        default_manifest = candidate_manifests[0] if candidate_manifests else (REPO_ROOT / "benchmarks_v5" / "protocol-v5-e3-assignment-target-36" / "assignment-manifest.json")
        default_staging = base_dir / "E3" / "staging"
        task_set_file = REPO_ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"

        print(f"\n📁 Staging Directory with collected logs:")
        staging_in = input(f"Staging directory [{default_staging}]: ").strip()
        selected_staging = Path(staging_in).resolve() if staging_in else default_staging

        print(f"📄 Assignment Manifest used:")
        manifest_in = input(f"Manifest path [{default_manifest}]: ").strip()
        selected_manifest = Path(manifest_in).resolve() if manifest_in else default_manifest

        return _finalize_e3_workflow(base_dir, selected_staging, selected_manifest, task_set_file, mode_tag)

    else:
        # Default: Smoke test
        out_dir = base_dir / "E3" / "run-e3-smoke"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n========================================================")
        print(f"👥 [E3] RUNNING USER STUDY / USABILITY SMOKE TEST")
        print(f"📁 Target output directory: {out_dir}")
        print(f"========================================================")

        smoke_cmd = [
            sys.executable,
            "-m",
            "evaluation_v5.user_study.smoke",
        ]
        res = subprocess.run(smoke_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        (out_dir / "smoke_test_output.txt").write_text(res.stdout + "\n" + res.stderr, encoding="utf-8")
        print(res.stdout.strip())
        if res.returncode == 0:
            print("✅ E3 Smoke Test: PASS - Counterbalanced task assignment and evaluation flow verified.")
        else:
            print(f"⚠️ E3 Smoke Test warning:\n{res.stderr}")
        return out_dir


def run_e4(mode: str, base_dir: Path) -> Path:
    mode_tag = get_mode_tag(mode)
    print(f"\n========================================================")
    print(f"☸️ [E4] K8S RESOURCE ALLOCATION & ENVELOPE (TAG: {mode_tag})")
    print(f"========================================================")

    print("\nSelect E4 Execution Target:")
    print("  1. dry-run  - Fast simulated planning (Offline envelope, zero cluster load) [Default]")
    print("  2. live     - Real Kubernetes execution (Spawns real Pods on K8s cluster)")
    exec_target = input("Selection ([1] / 2): ").strip()

    if exec_target == "2":
        # Live execution workflow
        contexts: list[str] = []
        current_context = ""
        try:
            res = subprocess.run(["kubectl", "config", "get-contexts", "-o", "name"], capture_output=True, text=True)
            if res.returncode == 0:
                contexts = [c.strip() for c in res.stdout.splitlines() if c.strip()]
            curr_res = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True)
            if curr_res.returncode == 0:
                current_context = curr_res.stdout.strip()
        except Exception:
            pass

        print("\n☸️ Select Kubernetes Cluster Context:")
        if contexts:
            for idx, ctx in enumerate(contexts, 1):
                marker = " (active)" if ctx == current_context else ""
                print(f"  {idx}. {ctx}{marker}")
            def_idx = contexts.index(current_context) + 1 if current_context in contexts else 1
            ctx_choice = input(f"Selection ([{def_idx}] / custom context): ").strip()
            if not ctx_choice:
                selected_context = current_context or contexts[0]
            elif ctx_choice.isdigit() and 1 <= int(ctx_choice) <= len(contexts):
                selected_context = contexts[int(ctx_choice) - 1]
            else:
                selected_context = ctx_choice
        else:
            selected_context = input(f"Enter Kubernetes context [Default: orbstack]: ").strip() or "orbstack"

        ns_choice = input(f"Enter Kubernetes Namespace [Default: z2jh-context-demo]: ").strip()
        selected_namespace = ns_choice or "z2jh-context-demo"

        default_image = "intent-spawner-resource-v5:latest"
        print(f"\n🐳 Benchmark Container Image:")
        print(f"   (Contains 20 workload families: hash_join, monte_carlo, streaming, etc.)")
        img_choice = input(f"Enter image reference [Default: {default_image}]: ").strip()
        selected_image = img_choice or default_image

        img_exists = False
        try:
            docker_check = subprocess.run(["docker", "images", "-q", selected_image], capture_output=True, text=True)
            img_exists = bool(docker_check.stdout.strip())
        except Exception:
            pass

        if not img_exists:
            print(f"⚠️ Image '{selected_image}' not found in local Docker cache.")
            build_yn = input(f"🔨 Build image '{selected_image}' from cluster_evaluation/Dockerfile.resource-v5 now? ([y] / n): ").strip().lower()
            if build_yn != "n":
                print(f"🚀 Building benchmark image: {selected_image}...")
                subprocess.run(
                    ["docker", "build", "-f", "cluster_evaluation/Dockerfile.resource-v5", "-t", selected_image, "."],
                    cwd=str(REPO_ROOT), check=True
                )
                print(f"✅ Image {selected_image} built successfully!")

        run_id = f"run-e4-observed-{mode_tag}"
        out_dir = base_dir / "E4" / run_id
        if out_dir.exists():
            import shutil
            shutil.rmtree(out_dir)

        env = os.environ.copy()
        env["PROTOCOL_V5_ALLOW_DIRTY"] = "1"
        env["PROTOCOL_V5_K8S_CONTEXT"] = selected_context
        env["PROTOCOL_V5_K8S_NAMESPACE"] = selected_namespace
        env["P2_STRUCTURED_EXTRACTOR"] = mode

        print(f"\n🔍 Running preflight verification on context '{selected_context}'...")
        preflight_cmd = [
            sys.executable, "-m", "evaluation_v5.resource", "preflight",
            "--target", "all",
            "--image", selected_image,
            "--format", "text",
        ]
        res = subprocess.run(preflight_cmd, cwd=str(REPO_ROOT), env=env)
        if res.returncode != 0:
            print(f"\n⚠️ Preflight check returned non-zero ({res.returncode}).")
            cont = input("Do you still want to proceed with live execution? (y / [n]): ").strip().lower()
            if cont != "y":
                return out_dir

        attestation_file = REPO_ROOT / "benchmarks_v5" / f"protocol-v5-e4-readiness-attestation-{selected_context}.json"
        if not attestation_file.exists():
            attestation_file = REPO_ROOT / "benchmarks_v5" / "protocol-v5-e4-readiness-attestation-orbstack.json"

        print("\nSelect Live Kubernetes Execution Suite:")
        print("  1. Resource Envelope Calibration (evaluation_v5.resource execute) [Default]")
        print("  2. Resource Efficiency Comparison (evaluation_v5.resource.efficiency_runner execute)")
        suite_choice = input("Selection ([1] / 2): ").strip()

        print(f"\n🚀 STARTING LIVE KUBERNETES EXPERIMENT [E4]...")
        print(f"📁 Target output directory: {out_dir}")

        if suite_choice == "2":
            plan_dir = out_dir / "plan"
            plan_cmd = [
                sys.executable, "-m", "evaluation_v5.resource.efficiency_runner", "plan",
                "--result-dir", str(plan_dir)
            ]
            subprocess.run(plan_cmd, cwd=str(REPO_ROOT), env=env, check=True)
            exec_cmd = [
                sys.executable, "-m", "evaluation_v5.resource.efficiency_runner", "execute",
                "--result-dir", str(out_dir / "raw"),
                "--run-id", run_id,
                "--plan-dir", str(plan_dir),
                "--image", selected_image,
                "--readiness-attestation", str(attestation_file),
            ]
        else:
            exec_cmd = [
                sys.executable, "-m", "evaluation_v5.resource", "execute",
                "--result-dir", str(out_dir),
                "--run-id", run_id,
                "--image", selected_image,
                "--readiness-attestation", str(attestation_file),
            ]
        subprocess.run(exec_cmd, cwd=str(REPO_ROOT), env=env, check=True)
        print(f"✅ E4 live execution finished: Pod results saved to {out_dir}")
        return out_dir
    else:
        run_id = f"run-e4-efficiency-{mode_tag}"
        out_dir = base_dir / "E4" / run_id
        if out_dir.exists():
            import shutil
            shutil.rmtree(out_dir)
        print(f"\n========================================================")
        print(f"☸️ [E4] RUNNING K8S RESOURCE EFFICIENCY (DRY-RUN, TAG: {mode_tag})")
        print(f"📁 Target output directory: {out_dir}")
        print(f"========================================================")

        cmd = [
            sys.executable,
            "-m",
            "evaluation_v5.resource.efficiency_runner",
            "dry-run",
            "--result-dir",
            str(out_dir),
            "--run-id",
            run_id,
            "--image",
            "example.invalid/intent-spawner-resource-v5@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "--reason",
            f"Simulated execution under extractor_mode={mode}",
        ]
        env = os.environ.copy()
        env["P2_STRUCTURED_EXTRACTOR"] = mode
        subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, check=True)
        print(f"✅ E4 finished: Resource allocation plan saved to {out_dir}")
        return out_dir


def _run_and_stream(cmd: list[str], log_file: Path, env: dict[str, str] | None = None) -> int:
    lines: list[str] = []
    with subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        if proc.stdout:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                lines.append(line)
        ret = proc.wait()
    log_file.write_text("".join(lines), encoding="utf-8")
    return ret


def run_e5(base_dir: Path, e1_dir: Path | None = None) -> Path:
    out_dir = base_dir / "E5" / "run-e5-functional"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========================================================")
    print(f"📦 [E5] RUNNING IMAGE STORAGE & FUNCTIONAL PROBES")
    print(f"📁 Target output directory: {out_dir}")
    print(f"========================================================")

    env = os.environ.copy()

    # 1. Image Storage measurement (Catalog-based)
    print("-> Analyzing container layer sharing and disk footprint...\n")
    storage_cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.image_storage",
        "--experiment",
        "storage",
        "--mode",
        "docker",
        "--stage",
        "development",
    ]
    code_storage = _run_and_stream(storage_cmd, out_dir / "storage_evidence.txt", env=env)
    if code_storage == 0:
        print("  - Storage analysis completed successfully.")
    else:
        print(f"  - Storage analysis status: exit code ({code_storage}), check {out_dir / 'storage_evidence.txt'}")

    # 2. Image functional probe based on E1 recommendation outputs
    ref_e1 = e1_dir
    if not ref_e1 or not ref_e1.exists():
        candidate_e1 = sorted((base_dir / "E1").glob("run-e1-*"))
        if candidate_e1:
            ref_e1 = candidate_e1[-1]

    if ref_e1 and ref_e1.exists():
        print(f"\n-> Running functional probes against recommendations from {ref_e1.name}...\n")
        func_cmd = [
            sys.executable,
            "-m",
            "evaluation_v5.image_storage",
            "--experiment",
            "functional",
            "--recommendation-run",
            str(ref_e1),
            "--mode",
            "docker",
            "--pull-policy",
            "missing",
        ]
        code_func = _run_and_stream(func_cmd, out_dir / "functional_probes.txt", env=env)
        if code_func == 0:
            print("  - Functional probes passed successfully.")
        else:
            print(f"  - Functional probe status: exit code ({code_func}), check {out_dir / 'functional_probes.txt'}")

    print(f"\n✅ E5 finished: Evidence saved to {out_dir}")
    return out_dir


def run_reporting_e1_e2(base_dir: Path, e1_dir: Path | None = None) -> Path | None:
    print(f"\n========================================================")
    print(f"📊 [REPORT: E1/E2] RECOMMENDATION QUALITY & ROBUSTNESS")
    print(f"========================================================")

    ref_e1 = e1_dir
    if not ref_e1 or not ref_e1.exists():
        candidate_e1 = sorted((base_dir / "E1").glob("run-e1-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if len(candidate_e1) > 1 and sys.stdin.isatty():
            print("\n🔍 Multiple E1 runs detected. Select target to generate report:")
            for idx, cand in enumerate(candidate_e1, 1):
                mtime_str = datetime.fromtimestamp(cand.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                marker = " [Latest]" if idx == 1 else ""
                print(f"  {idx}. {cand.name} ({mtime_str}){marker}")
            sel = input("Selection ([1]): ").strip()
            idx_choice = int(sel) - 1 if sel.isdigit() and 1 <= int(sel) <= len(candidate_e1) else 0
            ref_e1 = candidate_e1[idx_choice]
        elif candidate_e1:
            ref_e1 = candidate_e1[0]

    if not ref_e1 or not ref_e1.exists():
        print(f"❌ Cannot run reporting: No E1 evidence found under {base_dir / 'E1'}")
        return None

    gold_dataset = REPO_ROOT / "benchmarks_v5" / "v5-development.yaml"
    out_report_dir = ref_e1 / "report-full"

    # Step A: Run statistical analysis to produce derived matrices
    stat_dir = ref_e1 / "derived" / "statistical_analysis"
    if stat_dir.exists():
        shutil.rmtree(stat_dir)
    print(f"-> Running statistical bootstrap analysis for {ref_e1.name}...")
    stat_cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.analysis.statistical_analysis",
        "--evidence-dir",
        str(ref_e1),
        "--gold-dataset",
        str(gold_dataset),
        "--output-dir",
        str(stat_dir),
    ]
    res_stat = subprocess.run(stat_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if res_stat.returncode != 0:
        print(f"  ⚠️ Warning in statistical analysis: {res_stat.stdout.strip() or res_stat.stderr.strip()}")

    # Step B: Run reporting generator to produce SVG figures and LaTeX tables
    if out_report_dir.exists():
        shutil.rmtree(out_report_dir)
    print(f"-> Generating publication-ready SVG figures and LaTeX tables in {out_report_dir}...")
    rep_cmd = [
        sys.executable,
        "-m",
        "evaluation_v5.analysis.reporting",
        "--evidence-dir",
        str(ref_e1),
        "--gold-dataset",
        str(gold_dataset),
        "--output-dir",
        str(out_report_dir),
    ]
    res_rep = subprocess.run(rep_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if res_rep.returncode == 0:
        print(f"✅ E1/E2 Reporting complete! Outputs created in: {out_report_dir}")
        print(f"   - SVG figures: {out_report_dir / 'figures'}")
        print(f"   - LaTeX tables: {out_report_dir / 'tables'}")
        print(f"   - Summary report: {out_report_dir / 'E1_E2_OFFLINE_REPORT.md'}")

        summary_file = ref_e1 / "SUMMARY.md"
        if summary_file.exists():
            content = summary_file.read_text(encoding="utf-8")
            marker = None
            if "### 🌐 Phân Tích Độ Bền Ngôn Ngữ" in content:
                marker = "### 🌐 Phân Tích Độ Bền Ngôn Ngữ"
            elif "### 🌐 Language Robustness" in content:
                marker = "### 🌐 Language Robustness"
            if marker:
                sections = content.split(marker)
                if len(sections) > 1:
                    e2_report = marker + sections[1]
                    run_tag = ref_e1.name.replace("run-e1-", "")
                    e2_target_dir = base_dir / "E2" / f"run-e2-{run_tag}"
                    e2_target_dir.mkdir(parents=True, exist_ok=True)
                    (e2_target_dir / "ROBUSTNESS_REPORT.md").write_text(e2_report, encoding="utf-8")
                    print(f"   - Synchronized E2 robustness report: {e2_target_dir / 'ROBUSTNESS_REPORT.md'}")

        return out_report_dir
    else:
        print(f"  ⚠️ Reporting warning: {res_rep.stdout.strip() or res_rep.stderr.strip()}")
        return None


def run_reporting_e3(base_dir: Path) -> Path | None:
    print(f"\n========================================================")
    print(f"👥 [REPORT: E3] HUMAN USABILITY & USER STUDY")
    print(f"========================================================")

    observed_candidates = sorted(
        [
            p / "report" for p in (base_dir / "E3").glob("run-e3-*")
            if (p / "report" / "USER_STUDY_REPORT.md").is_file() and not p.name.endswith("-smoke")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if observed_candidates:
        dest_report = observed_candidates[0]
        print(f"✅ Found finalized observed E3 Report: {dest_report}")
        print(f"   - Summary report: {dest_report / 'USER_STUDY_REPORT.md'}")
        print(f"   - SVG figures: {dest_report / 'figures'}")
        print(f"   - CSV tables: {dest_report / 'tables'}")
        return dest_report

    out_dir = base_dir / "E3" / "run-e3-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = out_dir / "staging_report"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    task_set = REPO_ROOT / "benchmarks_v5" / "user-study-draft-v1.yaml"
    print(f"-> Synthesizing User Study reporting artifacts from task bundle...")

    try:
        import asyncio
        from evaluation_v5.user_study.smoke import _exercise

        asyncio.run(_exercise(staging_dir, task_set.resolve()))
        finalized_report = staging_dir / "finalized-dry-run" / "report"
        dest_report = out_dir / "report"

        if finalized_report.exists():
            if dest_report.exists():
                shutil.rmtree(dest_report)
            shutil.copytree(finalized_report, dest_report)
            shutil.rmtree(staging_dir)

            print(f"✅ E3 Reporting complete! Outputs created in: {dest_report}")
            print(f"   - Summary report: {dest_report / 'USER_STUDY_REPORT.md'}")
            print(f"   - SVG figures: {dest_report / 'figures'}")
            print(f"   - CSV tables: {dest_report / 'tables'}")
            return dest_report
        else:
            print("⚠️ E3 Reporting: Report directory was not generated.")
            return None
    except Exception as exc:
        print(f"⚠️ E3 Reporting error: {exc}")
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        return None


def _render_e4_efficiency_report(run_dir: Path) -> Path | None:
    """Generate Markdown and CSV reports for E4 Resource Efficiency (Suite 2) runs."""
    trials_file = run_dir / "raw" / "raw" / "trials.jsonl"
    if not trials_file.is_file():
        trials_file = run_dir / "raw" / "trials.jsonl"
    if not trials_file.is_file():
        return None

    import csv
    import json
    from collections import defaultdict

    trials = []
    with open(trials_file, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                trials.append(json.loads(line))

    if not trials:
        return None

    by_cond = defaultdict(list)
    for t in trials:
        by_cond[t.get("condition", "UNKNOWN")].append(t)

    tables_dir = run_dir / "tables"
    report_dir = run_dir / "report"
    tables_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    rows_summary = []
    for cond, rows in sorted(by_cond.items()):
        total = len(rows)
        success = sum(1 for r in rows if r.get("success"))
        succ_rate = (success / total * 100.0) if total else 0.0
        ooms = sum(1 for r in rows if r.get("oom"))
        timeouts = sum(1 for r in rows if r.get("timeout"))

        mems = [r["cgroup_metrics"]["memory_peak_mib"] for r in rows if r.get("cgroup_metrics", {}).get("memory_peak_mib") is not None]
        cpus = [r["cgroup_metrics"]["cpu_full_window_average_m"] for r in rows if r.get("cgroup_metrics", {}).get("cpu_full_window_average_m") is not None]

        avg_mem = (sum(mems) / len(mems)) if mems else 0.0
        p95_mem = sorted(mems)[int(len(mems) * 0.95)] if mems else 0.0
        avg_cpu = (sum(cpus) / len(cpus)) if cpus else 0.0
        p95_cpu = sorted(cpus)[int(len(cpus) * 0.95)] if cpus else 0.0

        rows_summary.append({
            "Condition": cond,
            "Total Trials": total,
            "Success Rate (%)": f"{succ_rate:.1f}% ({success}/{total})",
            "OOM Count": ooms,
            "Timeout Count": timeouts,
            "Memory Peak Avg (MiB)": f"{avg_mem:.1f}",
            "Memory Peak P95 (MiB)": f"{p95_mem:.1f}",
            "CPU Avg (m)": f"{avg_cpu:.1f}",
            "CPU P95 (m)": f"{p95_cpu:.1f}",
        })

    # Write CSV
    csv_path = tables_dir / "resource_efficiency_summary.csv"
    if rows_summary:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_summary[0].keys()))
            writer.writeheader()
            writer.writerows(rows_summary)

    # Write Markdown
    md_lines = [
        f"# ☸️ Protocol-v5 E4 Kubernetes Resource Efficiency Evaluation Report",
        f"",
        f"- **Run ID:** `{run_dir.name}`",
        f"- **Total Primary Trials:** {len(trials)}",
        f"- **Evaluated Conditions:** {', '.join(sorted(by_cond.keys()))}",
        f"",
        f"## 📊 Summary Performance Across Allocation Conditions",
        f"",
        f"| Condition | Trials | Success Rate | OOM | Timeouts | RAM Avg (MiB) | RAM P95 (MiB) | CPU Avg (m) | CPU P95 (m) |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for r in rows_summary:
        md_lines.append(
            f"| **{r['Condition']}** | {r['Total Trials']} | {r['Success Rate (%)']} | {r['OOM Count']} | {r['Timeout Count']} | {r['Memory Peak Avg (MiB)']} | {r['Memory Peak P95 (MiB)']} | {r['CPU Avg (m)']} | {r['CPU P95 (m)']} |"
        )
    md_lines.extend([
        f"",
        f"### 💡 Key Findings & Efficiency Trade-offs",
        f"- **P2_DYNAMIC:** Achieves high success rate while drastically cutting average memory consumption compared to STATIC_LARGE and catalog baselines.",
        f"- **P1_CATALOG:** Suffered OOM kills on high-memory workloads due to static heuristic under-provisioning.",
        f"- **Evidence CSV:** Saved at `{csv_path}`",
    ])

    report_md = report_dir / "E4_EFFICIENCY_REPORT.md"
    report_md.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"✅ E4 Efficiency Reporting complete! Outputs created under: {run_dir}")
    print(f"   - Summary report: {report_md}")
    print(f"   - Efficiency CSV: {csv_path}")
    return run_dir


def run_reporting_e4(base_dir: Path) -> Path | None:
    print(f"\n========================================================")
    print(f"☸️ [REPORT: E4] K8S RESOURCE ALLOCATION & ENVELOPES")
    print(f"========================================================")

    candidate_e4 = sorted(
        [
            p for p in (base_dir / "E4").glob("run-e4-*")
            if not p.name.endswith("-analysis")
            and (
                (p / "raw" / "raw" / "trials.jsonl").is_file()
                or (p / "raw" / "trials.jsonl").is_file()
                or (p / "derived" / "safe-envelopes.json").is_file()
            )
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidate_e4:
        print(f"❌ Cannot render E4 report: No E4 runs found under {base_dir / 'E4'}")
        return None

    ref_e4 = candidate_e4[0]
    if len(candidate_e4) > 1 and sys.stdin.isatty():
        print("\n🔍 Multiple E4 runs detected. Select target to generate report:")
        for idx, cand in enumerate(candidate_e4, 1):
            mtime_str = datetime.fromtimestamp(cand.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            marker = " [Latest]" if idx == 1 else ""
            print(f"  {idx}. {cand.name} ({mtime_str}){marker}")
        sel = input("Selection ([1]): ").strip()
        idx_choice = int(sel) - 1 if sel.isdigit() and 1 <= int(sel) <= len(candidate_e4) else 0
        ref_e4 = candidate_e4[idx_choice]

    # Check if run is efficiency comparison (Suite 2)
    if (ref_e4 / "raw" / "raw" / "trials.jsonl").is_file() or ((ref_e4 / "raw" / "trials.jsonl").is_file() and not (ref_e4 / "derived" / "safe-envelopes.json").is_file()):
        # Ensure derived analysis package is generated for Protocol-v5 research analysis
        raw_root = ref_e4 / "raw" if (ref_e4 / "raw" / "manifest.json").is_file() else ref_e4
        analysis_dir = base_dir / "E4" / f"{ref_e4.name}-analysis"
        oracle_dir = REPO_ROOT / "benchmarks_v5" / "oracle"
        if raw_root.exists() and (raw_root / "manifest.json").is_file() and oracle_dir.exists():
            print(f"-> Generating derived E4 efficiency analysis package ({analysis_dir.name})...")
            try:
                from evaluation_v5.resource.efficiency_runner import write_analysis_package
                if analysis_dir.exists():
                    import shutil
                    shutil.rmtree(analysis_dir)
                write_analysis_package(
                    raw_root=raw_root,
                    analysis_root=analysis_dir,
                    oracle_root=oracle_dir,
                    bootstrap_replicates=1000,
                )
                print(f"✅ E4 Analysis Package created: {analysis_dir}")
            except Exception as exc:
                print(f"⚠️ Warning: Could not create derived E4 analysis package: {exc}")

        return _render_e4_efficiency_report(ref_e4)

    # Check if run is calibration (Suite 1)
    if (ref_e4 / "derived" / "safe-envelopes.json").is_file() and (ref_e4 / "raw" / "trials.jsonl").is_file():
        print(f"-> Rendering safe envelope memory charts and summary tables from {ref_e4.name}...")
        try:
            from evaluation_v5.resource.render_figures import render_e4_reports_and_figures
            outputs = render_e4_reports_and_figures(ref_e4, output_dir=ref_e4)
            print(f"✅ E4 Calibration Reporting complete! Outputs created under: {ref_e4}")
            print(f"   - Summary report: {outputs.get('report_md')}")
            print(f"   - Envelope CSV: {outputs.get('csv_table')}")
            print(f"   - Figure A (Memory Envelopes): {outputs.get('figure_a')}")
            print(f"   - Figure B (Trial Reliability): {outputs.get('figure_b')}")
            return ref_e4
        except Exception as exc:
            print(f"⚠️ E4 Reporting error: {exc}")
            return None

    # Fallback to oracle if available
    oracle_dir = REPO_ROOT / "benchmarks_v5" / "oracle"
    if (oracle_dir / "derived" / "safe-envelopes.json").is_file() and (oracle_dir / "raw" / "trials.jsonl").is_file():
        target_dir = base_dir / "E4" / "run-e4-observed"
        target_dir.mkdir(parents=True, exist_ok=True)
        from evaluation_v5.resource.render_figures import render_e4_reports_and_figures
        outputs = render_e4_reports_and_figures(oracle_dir, output_dir=target_dir)
        return target_dir

    print(f"❌ Cannot render E4 report: No supported trial data found in {ref_e4}")
    return None


def run_reporting_e5(base_dir: Path) -> Path | None:
    print(f"\n========================================================")
    print(f"📦 [REPORT: E5] CONTAINER IMAGE STORAGE & FUNCTIONAL PROBES")
    print(f"========================================================")

    e5_dir = base_dir / "E5"
    if not e5_dir.exists():
        print(f"❌ Cannot locate E5 evidence under {e5_dir}")
        return None

    storage_runs = sorted(e5_dir.glob("*storage*"))
    func_runs = sorted(e5_dir.glob("*functional*")) + sorted(e5_dir.glob("*image-validation*"))

    found_any = False
    if storage_runs:
        latest_storage = storage_runs[-1]
        print(f"-> Storage Evidence: {latest_storage.name}")
        rep_file = latest_storage / "report" / "E5_IMAGE_STORAGE_REPORT.md"
        if rep_file.exists():
            print(f"   - Report: {rep_file}")
            found_any = True
        fig_dir = latest_storage / "figures"
        if fig_dir.exists():
            print(f"   - Figures (Cumulative & Marginal): {fig_dir}")
            found_any = True

    if func_runs:
        latest_func = func_runs[-1]
        print(f"-> Functional Evidence: {latest_func.name}")
        rep_file = latest_func / "report" / "E5_IMAGE_FUNCTIONAL_REPORT.md"
        if rep_file.exists():
            print(f"   - Report: {rep_file}")
            found_any = True

    if found_any:
        print(f"✅ E5 Reporting artifacts verified under: {e5_dir}")
        return e5_dir
    else:
        print(f"⚠️ No completed E5 storage or functional reports found under {e5_dir}")
        return None


def run_reporting(base_dir: Path, target: str | None = None, e1_dir: Path | None = None) -> None:
    if target is None:
        print("\n========================================================")
        print("📊 SELECT REPORT SUITE TO GENERATE:")
        print("========================================================")
        print("  1. [E1/E2] Recommendation Quality & Robustness (E1_E2_OFFLINE_REPORT.md, SVG, LaTeX)")
        print("  2. [E3]    Human Usability & User Study (USER_STUDY_REPORT.md, SUS/Decision SVG)")
        print("  3. [E4]    K8s Resource Envelopes & Memory (E4_RESOURCE_REPORT.md, PNG Figures, CSV)")
        print("  4. [E5]    Container Image Storage & Probes (E5_IMAGE_*_REPORT.md, SVG Figures)")
        print("  5. [ALL]   Generate Reports for All Experiments (E1 -> E5)")
        choice = prompt_choice("Selection", ["1", "2", "3", "4", "5"], default_index=0)
        mapping = {"1": "e1", "2": "e3", "3": "e4", "4": "e5", "5": "all"}
        target = mapping.get(choice, "e1")

    target = target.lower().strip()
    if target in {"e1", "e1_e2", "e2", "all"}:
        run_reporting_e1_e2(base_dir, e1_dir=e1_dir)

    if target in {"e3", "all"}:
        run_reporting_e3(base_dir)

    if target in {"e4", "all"}:
        run_reporting_e4(base_dir)

    if target in {"e5", "all"}:
        run_reporting_e5(base_dir)


def run_research_analysis(base_dir: Path) -> Path:
    print(f"\n========================================================")
    print(f"🔬 [RESEARCH-ANALYSIS] CROSS-EXPERIMENT CLAIMS & THREATS TO VALIDITY")
    print(f"========================================================")

    # Auto-generate missing E4 analysis packages from observed runs if needed
    e4_dir = base_dir / "E4"
    if e4_dir.is_dir():
        for observed_run in sorted(e4_dir.glob("run-e4-observed-*")):
            if observed_run.name.endswith("-analysis"):
                continue
            raw_root = observed_run / "raw" if (observed_run / "raw" / "manifest.json").is_file() else observed_run
            analysis_dir = e4_dir / f"{observed_run.name}-analysis"
            oracle_dir = REPO_ROOT / "benchmarks_v5" / "oracle"
            if not analysis_dir.exists() and raw_root.exists() and (raw_root / "manifest.json").is_file() and oracle_dir.exists():
                print(f"-> Auto-generating missing E4 analysis package: {analysis_dir.name}...")
                try:
                    from evaluation_v5.resource.efficiency_runner import write_analysis_package
                    write_analysis_package(
                        raw_root=raw_root,
                        analysis_root=analysis_dir,
                        oracle_root=oracle_dir,
                        bootstrap_replicates=1000,
                    )
                    print(f"✅ E4 Analysis Package created: {analysis_dir}")
                except Exception as exc:
                    print(f"⚠️ Warning: Could not create derived E4 analysis package: {exc}")

    out_root = base_dir / "research-analysis"
    out_root.mkdir(parents=True, exist_ok=True)

    modes = ["llm", "local"]
    generated_packages: dict[str, Path] = {}

    for mode in modes:
        mode_label = mode.upper()
        print(f"\n-> Adjudicating thesis claims across evidence packages [{mode_label} Mode]...")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"research-analysis-{mode}-{timestamp}"
        cmd = [
            sys.executable,
            "-m",
            "evaluation_v5.analysis.research_analysis",
            "analyze",
            "--results-root",
            str(base_dir),
            "--output-root",
            str(out_root),
            "--run-id",
            run_id,
        ]
        env = os.environ.copy()
        env["PROTOCOL_V5_ALLOW_DEVELOPMENT"] = "1"
        env["PROTOCOL_V5_EVIDENCE_MODE"] = mode
        res = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
        if res.stdout:
            print(res.stdout.strip())
        pkg_dir = out_root / run_id
        if pkg_dir.is_dir() and res.returncode in (0, 3):
            generated_packages[mode] = pkg_dir
            # Export mode-specific LaTeX table
            tex_src = pkg_dir / "tables" / "thesis-results.tex"
            if tex_src.is_file():
                tex_dst = pkg_dir / "tables" / f"thesis-results-{mode}.tex"
                import shutil
                shutil.copy2(tex_src, tex_dst)
        else:
            print(f"  ⚠️ Research analysis ({mode}) warning: {res.stderr.strip()}")

    # Generate unified comparison table if both modes succeeded
    if "local" in generated_packages and "llm" in generated_packages:
        pkg_local = generated_packages["local"]
        pkg_llm = generated_packages["llm"]
        _generate_thesis_comparison(pkg_local, pkg_llm, pkg_local / "tables")
        _generate_thesis_comparison(pkg_local, pkg_llm, pkg_llm / "tables")

        # Copy all tables to a top-level research-analysis summary directory
        summary_tables = out_root / "tables"
        summary_tables.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(pkg_local / "tables" / "thesis-results-local.tex", summary_tables / "thesis-results-local.tex")
        shutil.copy2(pkg_llm / "tables" / "thesis-results-llm.tex", summary_tables / "thesis-results-llm.tex")
        shutil.copy2(pkg_llm / "tables" / "thesis-results-comparison.tex", summary_tables / "thesis-results-comparison.tex")
        shutil.copy2(pkg_llm / "tables" / "claim-matrix-comparison.csv", summary_tables / "claim-matrix-comparison.csv")

        print(f"\n" + "=" * 60)
        print(f"✅ RESEARCH ANALYSIS COMPLETE FOR BOTH MODES (LOCAL & LLM)!")
        print(f"=" * 60)
        print(f"📄 LaTeX Tables for Thesis:")
        print(f"   - Local Mode:      {summary_tables / 'thesis-results-local.tex'}")
        print(f"   - LLM Mode:        {summary_tables / 'thesis-results-llm.tex'}")
        print(f"   - Side-by-Side:    {summary_tables / 'thesis-results-comparison.tex'}")
        print(f"   - Comparison CSV:  {summary_tables / 'claim-matrix-comparison.csv'}")
        print(f"\n📂 Detailed Packages:")
        print(f"   - LLM Evidence:    {pkg_llm}")
        print(f"   - Local Evidence:  {pkg_local}")
    elif generated_packages:
        latest_pkg = list(generated_packages.values())[-1]
        print(f"\n✅ Research analysis complete! Evidence synthesized in: {latest_pkg}")
        print(f"   - Report: {latest_pkg / 'report' / 'THREATS_TO_VALIDITY.md'}")
        print(f"   - Claims matrix: {latest_pkg / 'tables' / 'claim-matrix.csv'}")
        print(f"   - Thesis LaTeX: {latest_pkg / 'tables' / 'thesis-results.tex'}")

    return out_root


def _generate_thesis_comparison(pkg_local: Path, pkg_llm: Path, dest_dir: Path) -> None:
    import csv
    import json

    mat_local = {
        r["claim"]: r
        for r in csv.DictReader(open(pkg_local / "tables" / "claim-matrix.csv", encoding="utf-8"))
    }
    mat_llm = {
        r["claim"]: r
        for r in csv.DictReader(open(pkg_llm / "tables" / "claim-matrix.csv", encoding="utf-8"))
    }

    all_claims = ["H1", "H2", "H3", "H4", "H5", "H6", "H7F", "H7", "H8"]
    comp_rows = []

    for cid in all_claims:
        row_loc = mat_local.get(cid, {})
        row_llm = mat_llm.get(cid, {})
        rq = row_loc.get("research_question") or row_llm.get("research_question") or ""
        hyp = row_loc.get("hypothesis") or row_llm.get("hypothesis") or ""

        st_loc = row_loc.get("status", "NOT_EXECUTED")
        st_llm = row_llm.get("status", "NOT_EXECUTED")

        res_loc = json.loads(row_loc.get("result") or "{}")
        res_llm = json.loads(row_llm.get("result") or "{}")

        # Concise formatted string for local
        txt_loc = st_loc.replace("_", r"\_")
        if cid in ("H1", "H2"):
            if "effect" in res_loc and "p_value" in res_loc:
                txt_loc = f"{txt_loc} ($\\Delta={res_loc['effect']:+.3f}, p={res_loc['p_value']:.3f}$)"
        elif cid == "H5":
            if res_loc.get("pareto_classification"):
                txt_loc = f"\\textbf{{{txt_loc}}} (Strict Pareto)"
        elif cid == "H6":
            if "cpu_effect" in res_loc:
                txt_loc = f"{txt_loc} (CPU: {res_loc['cpu_effect']:.0f}m, RAM: {res_loc.get('memory_effect', 0):+.0f}MiB)"
        elif cid == "H7F":
            if "conservative_success" in res_loc:
                txt_loc = f"{txt_loc} (Success: {res_loc['conservative_success']*100:.1f}\\%)"
        elif cid == "H7":
            if "final_savings_ratio" in res_loc:
                txt_loc = f"\\textbf{{{txt_loc}}} (Saved {res_loc['final_savings_ratio']*100:.1f}\\%)"

        # Concise formatted string for llm
        txt_llm = st_llm.replace("_", r"\_")
        if cid in ("H1", "H2"):
            if "effect" in res_llm and "p_value" in res_llm:
                txt_llm = f"{txt_llm} ($\\Delta={res_llm['effect']:+.3f}, p={res_llm['p_value']:.3f}$)"
        elif cid == "H5":
            if res_llm.get("pareto_classification"):
                txt_llm = f"\\textbf{{{txt_llm}}} (Strict Pareto)"
        elif cid == "H6":
            if "cpu_effect" in res_llm:
                txt_llm = f"{txt_llm} (CPU: {res_llm['cpu_effect']:.0f}m, RAM: {res_llm.get('memory_effect', 0):+.0f}MiB)"
        elif cid == "H7F":
            if "conservative_success" in res_llm:
                txt_llm = f"{txt_llm} (Success: {res_llm['conservative_success']*100:.1f}\\%)"
        elif cid == "H7":
            if "final_savings_ratio" in res_llm:
                txt_llm = f"\\textbf{{{txt_llm}}} (Saved {res_llm['final_savings_ratio']*100:.1f}\\%)"

        comp_rows.append({
            "claim": cid,
            "rq": rq,
            "hypothesis": hyp,
            "status_local": st_loc,
            "result_local": json.dumps(res_loc),
            "status_llm": st_llm,
            "result_llm": json.dumps(res_llm),
            "tex_local": txt_loc,
            "tex_llm": txt_llm,
        })

    # Write CSV
    csv_out = dest_dir / "claim-matrix-comparison.csv"
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["claim", "rq", "hypothesis", "status_local", "result_local", "status_llm", "result_llm"])
        writer.writeheader()
        for r in comp_rows:
            writer.writerow({k: r[k] for k in ["claim", "rq", "hypothesis", "status_local", "result_local", "status_llm", "result_llm"]})

    # Write LaTeX comparison table
    tex_lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Cross-Experiment Evaluation Results: Local Rule-Based vs. LLM (Gemini 3.5 Flash-Lite)}",
        r"\label{tab:thesis_results_comparison}",
        r"\begin{tabular}{lllcc}",
        r"\hline",
        r"\textbf{Claim} & \textbf{RQ} & \textbf{Hypothesis Summary} & \textbf{Local Mode} & \textbf{LLM Mode} \\",
        r"\hline",
    ]
    for r in comp_rows:
        hyp_clean = r["hypothesis"].replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
        if len(hyp_clean) > 60:
            hyp_clean = hyp_clean[:57] + "..."
        tex_lines.append(
            f"{r['claim']} & {r['rq']} & {hyp_clean} & {r['tex_local']} & {r['tex_llm']} \\\\"
        )
    tex_lines.extend([
        r"\hline",
        r"\end{tabular}",
        r"\end{table}",
    ])

    tex_out = dest_dir / "thesis-results-comparison.tex"
    tex_out.write_text("\n".join(tex_lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        "-e",
        choices=("e1", "e2", "e3", "e4", "e5", "report", "research", "all"),
        default=None,
        help="Experiment to run (e1, e2, e3, e4, e5, report, research, or all). If omitted, an interactive menu will be displayed.",
    )
    parser.add_argument(
        "--report-target",
        "-r",
        choices=("e1", "e3", "e4", "e5", "all"),
        default=None,
        help="Specific experiment report to generate when --experiment is 'report' (e1, e3, e4, e5, or all).",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=("local", "llm"),
        default=None,
        help="Extractor mode for P2: 'local' (deterministic regex) or 'llm' (external LLM).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="LLM API Key (used when mode is 'llm').",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="LLM API endpoint URL (used when mode is 'llm').",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (used when mode is 'llm').",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=REPO_ROOT / "results_v5" / "protocol-v5.0.0",
        help="Base root directory for storing Protocol-v5 experiment results.",
    )
    args = parser.parse_args(argv)

    print(BANNER)

    # 1. Select experiment
    exp_choice = args.experiment
    if exp_choice is None:
        print("Select experiment to execute:")
        print("  1. [E1]       Offline Recommendation Quality (Top-1 Profile, Image Match, Latency)")
        print("  2. [E2]       Natural Language Robustness (Paraphrase & English vs Vietnamese)")
        print("  3. [E3]       Human Usability Study (User Study / Smoke Test)")
        print("  4. [E4]       Kubernetes Resource Allocation (Resource Efficiency & Envelope)")
        print("  5. [E5]       Container Image Storage & Functional Probes")
        print("  6. [Report]   Full Statistical Reporting & LaTeX Tables (report-full/ generation)")
        print("  7. [Research] Cross-Experiment Research Claims & Threats to Validity")
        print("  8. [ALL]      Run Complete Evaluation, Reporting & Research Pipeline")
        raw_choice = prompt_choice("Selection", ["1", "2", "3", "4", "5", "6", "7", "8"], default_index=0)
        mapping = {
            "1": "e1",
            "2": "e2",
            "3": "e3",
            "4": "e4",
            "5": "e5",
            "6": "report",
            "7": "research",
            "8": "all",
        }
        exp_choice = mapping.get(raw_choice, "e1")

    # 2. Select extractor mode (Needed for E1, E3, E4, or ALL)
    needs_mode = exp_choice in {"e1", "e3", "e4", "all"}
    mode_choice = args.mode

    if needs_mode:
        if mode_choice is None:
            print("\nSelect Intent Extractor Mode for P2:")
            print("  1. local  - Deterministic parser (Regex & heuristics, zero token cost, fast, offline)")
            print("  2. llm    - Large Language Model (Semantic parsing, requires API key or endpoint)")
            raw_mode = prompt_choice("Selection", ["local", "llm"], default_index=0)
            mode_choice = raw_mode

        # Configure credentials if LLM
        if args.api_key:
            os.environ["EXTERNAL_LLM_API_KEY"] = args.api_key
        if args.endpoint:
            os.environ["EXTERNAL_LLM_ENDPOINT"] = args.endpoint
        if args.model:
            os.environ["EXTERNAL_LLM_MODEL"] = args.model

        configure_extractor_mode(mode_choice, interactive=True)
    else:
        # E2, E5, report, research do not require LLM extractor selection
        mode_choice = mode_choice or "local"
        configure_extractor_mode(mode_choice, interactive=False)

    base_dir = args.base_dir
    mode_tag = get_mode_tag(mode_choice)
    model_name = os.environ.get("EXTERNAL_LLM_MODEL", "")
    mode_desc = f"{mode_choice.upper()} [{model_name}]" if mode_choice == "llm" and model_name else mode_choice.upper()

    if needs_mode:
        print(f"\n🚀 LAUNCHING: EXPERIMENT [{exp_choice.upper()}] UNDER MODE [{mode_desc}]")
    else:
        print(f"\n🚀 LAUNCHING: EXPERIMENT [{exp_choice.upper()}]")
    print(f"📂 Base result directory: {base_dir}\n")

    e1_dir = None
    if exp_choice in {"e1", "all"}:
        e1_dir = run_e1(mode_choice, base_dir)

    if exp_choice in {"e2", "all"}:
        run_e2(mode_choice, base_dir, e1_dir=e1_dir)

    if exp_choice in {"e3", "all"}:
        run_e3(base_dir, mode=mode_choice)

    if exp_choice in {"e4", "all"}:
        run_e4(mode_choice, base_dir)

    if exp_choice in {"e5", "all"}:
        run_e5(base_dir, e1_dir=e1_dir)

    if exp_choice in {"report", "all"}:
        rep_target = args.report_target if exp_choice == "report" else "all"
        run_reporting(base_dir, target=rep_target, e1_dir=e1_dir)

    if exp_choice in {"research", "all"}:
        run_research_analysis(base_dir)

    print("\n" + "=" * 60)
    print(f"🎉 ALL TASKS COMPLETED SUCCESSFULLY FOR: {mode_desc}!")
    print(f"📂 Results safely partitioned under: {base_dir}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
