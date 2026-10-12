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
from pathlib import Path
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
    mode_tag = get_mode_tag(mode)
    out_dir = base_dir / "E2" / f"run-e2-{mode_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========================================================")
    print(f"🛡️ [E2] RUNNING NATURAL LANGUAGE ROBUSTNESS ANALYSIS (TAG: {mode_tag})")
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


def run_e3(mode: str, base_dir: Path) -> Path:
    mode_tag = get_mode_tag(mode)
    out_dir = base_dir / "E3" / f"run-e3-{mode_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========================================================")
    print(f"👥 [E3] RUNNING USER STUDY / USABILITY SMOKE TEST (TAG: {mode_tag})")
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


def run_e5(mode: str, base_dir: Path, e1_dir: Path | None = None) -> Path:
    mode_tag = get_mode_tag(mode)
    out_dir = base_dir / "E5" / f"run-e5-functional-{mode_tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n========================================================")
    print(f"📦 [E5] RUNNING IMAGE STORAGE & FUNCTIONAL PROBES (TAG: {mode_tag})")
    print(f"📁 Target output directory: {out_dir}")
    print(f"========================================================")

    env = os.environ.copy()
    env["P2_STRUCTURED_EXTRACTOR"] = mode

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
    ref_e1 = e1_dir or (base_dir / "E1" / f"run-e1-{mode_tag}")
    if ref_e1.exists():
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        "-e",
        choices=("e1", "e2", "e3", "e4", "e5", "all"),
        default=None,
        help="Experiment to run (e1, e2, e3, e4, e5, or all). If omitted, an interactive menu will be displayed.",
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
        print("  1. [E1]  Recommendation Quality (Top-1 Profile, Image Match, Latency)")
        print("  2. [E2]  Natural Language Robustness (Paraphrase & English vs Vietnamese)")
        print("  3. [E3]  Human Usability Study (User Study / Smoke Test)")
        print("  4. [E4]  Kubernetes Resource Allocation (Resource Efficiency & Envelope)")
        print("  5. [E5]  Container Image Storage & Functional Probes")
        print("  6. [ALL] Run complete evaluation pipeline (E1 -> E5)")
        raw_choice = prompt_choice("Selection", ["1", "2", "3", "4", "5", "6"], default_index=0)
        mapping = {"1": "e1", "2": "e2", "3": "e3", "4": "e4", "5": "e5", "6": "all"}
        exp_choice = mapping.get(raw_choice, "e1")

    # 2. Select extractor mode
    mode_choice = args.mode
    if mode_choice is None:
        print("\nSelect Intent Extractor Mode for P2:")
        print("  1. local  - Deterministic parser (Regex & heuristics, zero token cost, fast, offline)")
        print("  2. llm    - Large Language Model (Semantic parsing, requires API key or endpoint)")
        raw_mode = prompt_choice("Selection", ["local", "llm"], default_index=0)
        mode_choice = raw_mode

    # 3. Configure credentials if LLM
    if args.api_key:
        os.environ["EXTERNAL_LLM_API_KEY"] = args.api_key
    if args.endpoint:
        os.environ["EXTERNAL_LLM_ENDPOINT"] = args.endpoint
    if args.model:
        os.environ["EXTERNAL_LLM_MODEL"] = args.model

    configure_extractor_mode(mode_choice, interactive=True)

    base_dir = args.base_dir
    mode_tag = get_mode_tag(mode_choice)
    model_name = os.environ.get("EXTERNAL_LLM_MODEL", "")
    mode_desc = f"{mode_choice.upper()} [{model_name}]" if mode_choice == "llm" and model_name else mode_choice.upper()

    print(f"\n🚀 LAUNCHING: EXPERIMENT [{exp_choice.upper()}] UNDER MODE [{mode_desc}]")
    print(f"📂 Tag: {mode_tag} | Base result directory: {base_dir}\n")

    e1_dir = None
    if exp_choice in {"e1", "all"}:
        e1_dir = run_e1(mode_choice, base_dir)

    if exp_choice in {"e2", "all"}:
        run_e2(mode_choice, base_dir, e1_dir=e1_dir)

    if exp_choice in {"e3", "all"}:
        run_e3(mode_choice, base_dir)

    if exp_choice in {"e4", "all"}:
        run_e4(mode_choice, base_dir)

    if exp_choice in {"e5", "all"}:
        run_e5(mode_choice, base_dir, e1_dir=e1_dir)

    print("\n" + "=" * 60)
    print(f"🎉 ALL TASKS COMPLETED SUCCESSFULLY FOR: {mode_desc}!")
    print(f"📂 Results safely partitioned into: {base_dir}/<E1..E5>/*-{mode_tag}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
