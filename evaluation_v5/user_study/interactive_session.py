"""Interactive Human Trial Session Runner for Protocol-v5 E3.

Allows real participants to execute the within-subject crossover evaluation
tasks (B0 manual baseline vs P2 intent-aware recommender), recording valid,
immutable event streams, decision timings, and questionnaire responses (SEQ, SUS).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid

from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import RecommendationPreviewRuntime
from recommender.p2_backend import P2Config, P2Recommender

from .assignment import AssignmentManifest, load_assignment_manifest
from .hub import (
    DecisionDeadlineExpired,
    StudyGateStore,
    StudySessionRuntime,
    options_form,
)
from .instrumentation import AppendOnlyEventStore
from .questionnaires import (
    CUSTOM_ITEMS,
    CUSTOM_ITEM_IDS,
    FINAL_PREFERENCE_ID,
    FINAL_PREFERENCES,
    SEQ_ITEM_ID,
    SUS_ITEMS,
    SUS_ITEM_IDS,
    QuestionnaireType,
)
from .schemas import (
    CancelReason,
    Condition,
    EventType,
    browser_safe_task_set,
    load_task_set,
)


PROFILE_OPTIONS = [
    ("small", "1 CPU, 2 GB RAM, 0 GPU (Lightweight compute)"),
    ("medium", "2 CPU, 8 GB RAM, 0 GPU (Data science & tabular analytics)"),
    ("large", "4 CPU, 16 GB RAM, 0 GPU (Memory/CPU heavy ML training)"),
]

IMAGE_OPTIONS = [
    ("minimal-python", "Python 3.10 standard library & JupyterLab"),
    ("scipy-data-science", "Pandas, NumPy, SciPy, Matplotlib"),
    ("pytorch-deep-learning", "PyTorch 2.x, Torchvision, CUDA/CPU"),
    ("tensorflow-deep-learning", "TensorFlow 2.x, Keras, TensorBoard"),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _prompt_int(prompt: str, min_val: int, max_val: int, default: int) -> int:
    while True:
        try:
            raw = input(f"{prompt} [{default}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
        if not raw:
            return default
        if raw.isdigit():
            val = int(raw)
            if min_val <= val <= max_val:
                return val
        print(f"Invalid input. Please enter an integer between {min_val} and {max_val}.")


def _prompt_choice(prompt: str, choices: list[str], default_index: int = 0) -> str:
    default_val = choices[default_index]
    options_str = " / ".join(f"[{c}]" if c == default_val else c for c in choices)
    while True:
        try:
            raw = input(f"{prompt} ({options_str}): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)
        if not raw:
            return default_val
        for c in choices:
            if raw.lower() == c.lower():
                return c
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print(f"Invalid option. Please choose from: {choices}")


def _reset_participant_staging(staging_dir: Path, participant: Any) -> None:
    events_file = staging_dir / "events.jsonl"
    if events_file.exists():
        lines = [
            line for line in events_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("participant_id") != participant.participant_id
        ]
        events_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    questionnaires_file = staging_dir / "questionnaires.jsonl"
    if questionnaires_file.exists():
        lines = [
            line for line in questionnaires_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("participant_id") != participant.participant_id
        ]
        questionnaires_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    sessions_file = staging_dir / "sessions.jsonl"
    if sessions_file.exists():
        lines = [
            line for line in sessions_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("participant_id") != participant.participant_id
        ]
        sessions_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    for marker_dir in ("consent-acks", "completion-markers", "transition-markers", "incomplete-markers"):
        marker_file = staging_dir / marker_dir / f"{participant.session_id}.json"
        if marker_file.exists():
            marker_file.unlink()


def _clean_incomplete_trials(events_path: Path, participant: Any) -> None:
    if not events_path.exists():
        return
    lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    terminal_trial_ids = {
        e["trial_id"]
        for e in events
        if e.get("participant_id") == participant.participant_id
        and e.get("event_type") in {"confirm", "cancel"}
    }
    kept = [
        line
        for line, e in zip(lines, events)
        if e.get("participant_id") != participant.participant_id
        or e.get("trial_id") in terminal_trial_ids
    ]
    if len(kept) != len(lines):
        events_path.write_text(
            "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8"
        )
        print(
            f"🔄 Cleaned up {len(lines) - len(kept)} interrupted event(s) from previous incomplete task. Resuming cleanly!"
        )


async def run_interactive_trial(
    *,
    task_set_path: Path,
    assignment_path: Path,
    staging_dir: Path,
    extractor_mode: str = "local",
    participant_id: str | None = None,
) -> dict[str, Any]:
    """Execute an interactive human trial session."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    events_path = staging_dir / "events.jsonl"
    completion_dir = staging_dir / "completion-markers"
    completion_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "exclusions.jsonl").touch(exist_ok=True)

    tasks = load_task_set(task_set_path)
    assignment = load_assignment_manifest(assignment_path)

    # Check already completed sessions
    completed_session_ids = set()
    sessions_file = staging_dir / "sessions.jsonl"
    if sessions_file.exists():
        for line in sessions_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    payload = json.loads(line)
                    if payload.get("session_status") == "complete":
                        completed_session_ids.add(payload.get("session_id"))
                except Exception:
                    pass

    # Select participant
    if participant_id is None:
        print("\n========================================================")
        print("👥 SELECT STUDY PARTICIPANT")
        print("========================================================")
        available = []
        for idx, p in enumerate(assignment.assignments, 1):
            is_done = p.session_id in completed_session_ids
            marker = " [COMPLETED]" if is_done else ""
            cond_order = " -> ".join(p.condition_order)
            print(f"  {idx:2d}. {p.participant_id} (Order: {cond_order}){marker}")
            if not is_done:
                available.append((idx, p))

        if not available:
            print("\n⚠️ All participants in this manifest are already marked completed!")
            rec = input("Select participant number to run anyway or press Enter to cancel: ").strip()
            if not rec or not rec.isdigit():
                return {"status": "CANCELLED", "message": "No participant chosen."}
            target_idx = int(rec) - 1
            participant = assignment.assignments[target_idx]
        else:
            default_choice = str(available[0][0])
            choice = input(f"\nSelect participant number ([{default_choice}]): ").strip() or default_choice
            target_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(assignment.assignments) else available[0][0] - 1
            participant = assignment.assignments[target_idx]
    else:
        participant = assignment.participant(participant_id)

    print(f"\n🎯 Selected Participant: {participant.participant_id}")
    print(f"   Session ID:           {participant.session_id}")
    print(f"   Condition Order:      {' -> '.join(participant.condition_order)}")
    print(f"   Counterbalance Cell:  {participant.counterbalance_cell}")

    # Check if participant already has partial records in staging
    has_partial_events = False
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    if json.loads(line).get("participant_id") == participant.participant_id:
                        has_partial_events = True
                        break
                except Exception:
                    pass

    if has_partial_events:
        print(f"\n⚠️ Participant {participant.participant_id} already has recorded events in staging.")
        act = _prompt_choice("Session action", ["Continue", "Reset"], default_index=0)
        if act.lower() == "reset":
            _reset_participant_staging(staging_dir, participant)
            print(f"🧹 Staging data for {participant.participant_id} has been reset. Starting clean from Task 1!\n")
        else:
            _clean_incomplete_trials(events_path, participant)

    # Initialize Runtime Components
    store = AppendOnlyEventStore(events_path, completion_dir)
    gates = StudyGateStore(staging_dir)

    backend = P2Recommender(config=P2Config(extractor_mode=extractor_mode))
    preview_runtime = RecommendationPreviewRuntime(
        monotonic=time.monotonic,
        deployment=DeploymentMetadata(
            backend="p2",
            backend_version=backend.backend_version,
            package_version="intent-spawner-interactive-v1",
            package_checksum="a" * 64,
        ),
        catalog=backend.catalog,
        backend=backend,
    )

    study = StudySessionRuntime(
        assignment_manifest=assignment,
        browser_task_set=browser_safe_task_set(tasks),
        recommendation_runtime=preview_runtime,
        event_store=store,
        gate_store=gates,
        monotonic=time.monotonic,
        utc_now=lambda: datetime.now(timezone.utc),
        uuid_factory=uuid.uuid4,
        mark_restart_incomplete=False,
    )

    # Consent Check
    if not study.has_consent(participant.participant_id):
        print("\n========================================================")
        print("📋 INFORMED CONSENT ACKNOWLEDGEMENT")
        print("========================================================")
        print(f"Study ID: {assignment.study_id} | Consent Version: {assignment.consent_version}")
        print("You are participating in an interactive research evaluation comparing two")
        print("JupyterHub profile selection mechanisms: B0 (Manual) and P2 (Intent-Spawner).")
        print("All logs are pseudonymous and content-free; no personal data is collected.")
        consent_ans = _prompt_choice("Do you consent to begin this trial?", ["y", "n"], default_index=0)
        if consent_ans.lower() != "y":
            print("Consent not given. Aborting trial session.")
            return {"status": "CONSENT_REFUSED"}
        study.acknowledge_consent(participant.participant_id)

    total_tasks = len(participant.task_sequence)
    for step_num, assigned in enumerate(participant.task_sequence, 1):
        active = study.current_task(participant.participant_id)
        if active is None or active.assigned.trial_id != assigned.trial_id:
            # Task may already have progressed
            continue

        # Standardized Transition Check (between Task 4 and 5)
        if study.transition_required(active):
            print("\n" + "=" * 65)
            print("☕ STANDARDIZED TRANSITION / BREAK")
            print("=" * 65)
            print("You have successfully completed Period 1!")
            print(f"Next, you will transition to Period 2 under Condition: {participant.condition_order[1]}")
            print("Take a short breath, then press Enter to continue...")
            input("[Press Enter to proceed]")
            study.acknowledge_transition(participant.participant_id)

        # Ensure Task Shown
        study.ensure_task_shown(active)
        start_time = time.monotonic()

        cond_name = "B0: MANUAL SELECTION" if assigned.condition is Condition.B0 else "P2: INTENT-SPAWNER (RECOMMENDER)"
        phase_name = "WARM-UP (UNSCORED)" if assigned.phase.value == "warm_up" else "MEASURED TASK"

        print("\n" + "=" * 65)
        print(f"📋 TASK [{step_num}/{total_tasks}] — {phase_name}")
        print(f"⚙️ Condition: {cond_name}")
        print("=" * 65)
        print("\n📝 Scenario & Workload Requirement:")
        print(f"   \"{active.scenario.strip()}\"\n")

        confirmed_profile = None
        confirmed_image = None
        is_cancelled = False

        if assigned.condition is Condition.B0:
            print("--- ⚙️ Manual Environment Selection (B0) ---")
            print("Available Hardware Profiles:")
            for p_idx, (p_id, p_desc) in enumerate(PROFILE_OPTIONS, 1):
                print(f"  {p_idx}. {p_id:8s} -> {p_desc}")
            sel_p = _prompt_int("Select Profile", 1, len(PROFILE_OPTIONS), default=1)
            selected_profile = PROFILE_OPTIONS[sel_p - 1][0]

            try:
                study.record(
                    participant.participant_id,
                    assigned.trial_id,
                    EventType.PROFILE_CHANGED,
                    old_profile_id=None,
                    new_profile_id=selected_profile,
                )
            except DecisionDeadlineExpired:
                print("\n⏰ The 10-minute decision limit expired for this task.")
                print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                is_cancelled = True

            if not is_cancelled:
                print("\nAvailable Container Images:")
                for img_idx, (img_id, img_desc) in enumerate(IMAGE_OPTIONS, 1):
                    print(f"  {img_idx}. {img_id:24s} -> {img_desc}")
                sel_img = _prompt_int("Select Image", 1, len(IMAGE_OPTIONS), default=1)
                selected_image = IMAGE_OPTIONS[sel_img - 1][0]

                try:
                    study.record(
                        participant.participant_id,
                        assigned.trial_id,
                        EventType.IMAGE_CHANGED,
                        old_image_id=None,
                        new_image_id=selected_image,
                    )
                except DecisionDeadlineExpired:
                    print("\n⏰ The 10-minute decision limit expired for this task.")
                    print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                    is_cancelled = True

            if not is_cancelled:
                print(f"\nReady to launch: Profile=[{selected_profile}], Image=[{selected_image}]")
                action = _prompt_choice("Action", ["Confirm", "Cancel"], default_index=0)
                if action.lower() == "confirm":
                    try:
                        study.record(
                            participant.participant_id,
                            assigned.trial_id,
                            EventType.CONFIRM,
                            profile_id=selected_profile,
                            image_id=selected_image,
                        )
                        confirmed_profile = selected_profile
                        confirmed_image = selected_image
                    except DecisionDeadlineExpired:
                        print("\n⏰ The 10-minute decision limit expired for this task.")
                        print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                        is_cancelled = True
                else:
                    try:
                        study.record(
                            participant.participant_id,
                            assigned.trial_id,
                            EventType.CANCEL,
                            cancel_reason=CancelReason.USER_CHOICE,
                        )
                    except DecisionDeadlineExpired:
                        pass
                    is_cancelled = True

        else:
            # P2: Recommender mode
            print("--- 💡 Intent-Aware Recommendation (P2) ---")
            override_profile = None
            override_image = None

            while True:
                try:
                    study.record(
                        participant.participant_id,
                        assigned.trial_id,
                        EventType.INTENT_FOCUS,
                    )
                    print("Describe what you plan to do in your notebook in natural language:")
                    prompt_input = input("Intent text (press Enter to use scenario): ").strip()
                    intent_text = prompt_input or active.scenario

                    study.record(
                        participant.participant_id,
                        assigned.trial_id,
                        EventType.INTENT_EDIT,
                    )

                    print("\n🔍 Generating recommendation from intent...")
                    preview = await study.issue_preview(
                        participant.participant_id,
                        assigned.trial_id,
                        intent_text,
                    )
                except DecisionDeadlineExpired:
                    print("\n⏰ The 10-minute decision limit expired for this task.")
                    print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                    is_cancelled = True
                    break

                rec_profile = str(preview.get("applied_profile") or "small")
                rec_image = str(preview.get("recommendation", {}).get("image_id") or "minimal-python")
                reason = preview.get("explanation") or preview.get("recommendation", {}).get("reason") or "Optimal match"

                print(f"\n✨ System Recommendation:")
                print(f"   • Profile: {rec_profile.upper()}")
                print(f"   • Image:   {rec_image}")
                print(f"   • Reason:  {reason}")

                print("\nChoose next action:")
                print("  1. Accept recommendation and Launch [Default]")
                print("  2. Override manually (Choose different profile / image)")
                print("  3. Re-edit intent prompt")
                print("  4. Cancel task")

                choice = _prompt_choice("Action", ["1", "2", "3", "4"], default_index=0)
                if choice == "1":
                    try:
                        study.record(
                            participant.participant_id,
                            assigned.trial_id,
                            EventType.CONFIRM,
                            profile_id=rec_profile,
                            image_id=rec_image,
                        )
                        confirmed_profile = rec_profile
                        confirmed_image = rec_image
                    except DecisionDeadlineExpired:
                        print("\n⏰ The 10-minute decision limit expired for this task.")
                        print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                        is_cancelled = True
                    break
                elif choice == "2":
                    print("\nSelect Override Profile:")
                    for p_idx, (p_id, p_desc) in enumerate(PROFILE_OPTIONS, 1):
                        print(f"  {p_idx}. {p_id:8s} -> {p_desc}")
                    sel_p = _prompt_int("Select Profile", 1, len(PROFILE_OPTIONS), default=1)
                    override_profile = PROFILE_OPTIONS[sel_p - 1][0]

                    print("\nSelect Override Image:")
                    for img_idx, (img_id, img_desc) in enumerate(IMAGE_OPTIONS, 1):
                        print(f"  {img_idx}. {img_id:24s} -> {img_desc}")
                    sel_img = _prompt_int("Select Image", 1, len(IMAGE_OPTIONS), default=1)
                    override_image = IMAGE_OPTIONS[sel_img - 1][0]

                    try:
                        is_diff = (override_profile != rec_profile) or (override_image != rec_image)

                        if override_profile != rec_profile:
                            study.record(
                                participant.participant_id,
                                assigned.trial_id,
                                EventType.PROFILE_CHANGED,
                                old_profile_id=rec_profile,
                                new_profile_id=override_profile,
                            )
                        if override_image != rec_image:
                            study.record(
                                participant.participant_id,
                                assigned.trial_id,
                                EventType.IMAGE_CHANGED,
                                old_image_id=rec_image,
                                new_image_id=override_image,
                            )
                        if is_diff:
                            study.record(
                                participant.participant_id,
                                assigned.trial_id,
                                EventType.OVERRIDE,
                                profile_id=override_profile,
                                image_id=override_image,
                            )
                        study.record(
                            participant.participant_id,
                            assigned.trial_id,
                            EventType.CONFIRM,
                            profile_id=override_profile,
                            image_id=override_image,
                        )
                        confirmed_profile = override_profile
                        confirmed_image = override_image
                    except DecisionDeadlineExpired:
                        print("\n⏰ The 10-minute decision limit expired for this task.")
                        print("   Task automatically marked as TIMEOUT / CANCELLED per study protocol.")
                        is_cancelled = True
                    break
                elif choice == "3":
                    continue
                else:
                    try:
                        study.record(
                            participant.participant_id,
                            assigned.trial_id,
                            EventType.CANCEL,
                            cancel_reason=CancelReason.USER_CHOICE,
                        )
                    except DecisionDeadlineExpired:
                        pass
                    is_cancelled = True
                    break

        elapsed = time.monotonic() - start_time
        if confirmed_profile and confirmed_image and not is_cancelled:
            try:
                study.record_ready(participant.participant_id, assigned.trial_id)
            except Exception:
                pass
            print(f"✅ Pod Launched: {confirmed_profile} + {confirmed_image} (Decision time: {elapsed:.1f}s)")
        else:
            print(f"⚠️ Task Ended (Cancelled or Timed Out). Elapsed: {elapsed:.1f}s")

        # Handle Any Pending Questionnaires
        while True:
            pending = study.pending_questionnaire(participant.participant_id)
            if pending is None:
                break
            kind = QuestionnaireType(pending["questionnaire_type"])

            if kind is QuestionnaireType.SEQ_TASK:
                print("\n--- 📊 Task Experience Rating (Single Ease Question - SEQ) ---")
                print("Overall, how easy or difficult was it to select the environment for this task?")
                print("  [1] Very Difficult  ...  [4] Neutral  ...  [7] Very Easy")
                seq_val = _prompt_int("Your Rating (1-7)", 1, 7, default=5)
                study.record_questionnaire(
                    participant.participant_id,
                    str(pending["questionnaire_id"]),
                    str(uuid.uuid4()),
                    {SEQ_ITEM_ID: seq_val},
                )

            elif kind is QuestionnaireType.POST_CONDITION:
                cond_label = pending.get("condition") or "Current Condition"
                print("\n" + "=" * 65)
                print(f"📋 POST-CONDITION SYSTEM USABILITY SCALE (SUS) — Condition: {cond_label}")
                print("=" * 65)
                print("Please rate each statement from 1 (Strongly Disagree) to 5 (Strongly Agree):\n")

                sus_responses: dict[str, int] = {}
                for idx, (item_id, item_text) in enumerate(zip(SUS_ITEM_IDS, SUS_ITEMS), 1):
                    print(f"  {idx:2d}. {item_text}")
                    ans = _prompt_int("     Rating (1-5)", 1, 5, default=4 if idx % 2 == 1 else 2)
                    sus_responses[item_id] = ans

                print("\nAdditional Experience Questions (1 = Strongly Disagree to 7 = Strongly Agree):")
                for c_id in CUSTOM_ITEM_IDS:
                    c_text = CUSTOM_ITEMS[c_id]
                    print(f"  • {c_text}")
                    ans = _prompt_int("    Rating (1-7)", 1, 7, default=5)
                    sus_responses[c_id] = ans

                study.record_questionnaire(
                    participant.participant_id,
                    str(pending["questionnaire_id"]),
                    str(uuid.uuid4()),
                    sus_responses,
                )

            elif kind is QuestionnaireType.FINAL_PREFERENCE:
                print("\n" + "=" * 65)
                print("🏆 FINAL SYSTEM PREFERENCE")
                print("=" * 65)
                print("Having experienced both selection methods, which system did you prefer overall?")
                print("  1. B0 - Manual Selection (Default JupyterHub dropdowns)")
                print("  2. P2 - Intent-Aware Recommendation (Natural language intent)")
                print("  3. No Preference")
                pref_sel = _prompt_choice("Preference", ["1", "2", "3"], default_index=1)
                mapping = {"1": "B0", "2": "P2", "3": "NO_PREFERENCE"}
                final_pref = mapping.get(pref_sel, "P2")
                study.record_questionnaire(
                    participant.participant_id,
                    str(pending["questionnaire_id"]),
                    str(uuid.uuid4()),
                    {FINAL_PREFERENCE_ID: final_pref},
                )

    # Complete session
    store.complete_session(
        participant.session_id,
        expected_trial_ids={task.trial_id for task in participant.task_sequence},
    )
    completion = store.read_completion_marker(participant.session_id)
    if completion is not None:
        gates.record_completed_session(
            assignment,
            participant,
            completed_at_utc=str(completion["completed_at_utc"]),
        )

    print("\n" + "=" * 65)
    print(f"🎉 TRIAL SESSION COMPLETED FOR PARTICIPANT: {participant.participant_id}")
    print(f"📁 Evidence safely appended to staging directory: {staging_dir}")
    print("=" * 65 + "\n")
    return {
        "status": "COMPLETED",
        "participant_id": participant.participant_id,
        "session_id": participant.session_id,
        "staging_dir": str(staging_dir),
    }
