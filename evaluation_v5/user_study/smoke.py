"""Deterministic synthetic smoke path through the real study Hub adapter.

This is a researcher/CI integration check, not a participant study.  It uses
one generated pseudonym, deterministic clocks, synthetic interactions, the
real P2 preview runtime, the real study form parser/pre-spawn logic, and the
immutable finalizer inside a temporary directory.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import uuid

from recommender.deployment import DeploymentMetadata
from recommender.jupyterhub_integration import RecommendationPreviewRuntime
from recommender.p2_backend import P2Recommender

from .assignment import generate_assignment_manifest
from .hub import (
    StudyGateStore,
    StudySessionRuntime,
    _study_options_from_form,
    apply_b0_selection,
    options_form,
)
from .instrumentation import AppendOnlyEventStore
from .runner import main as runner_main
from .schemas import Condition, EventType, browser_safe_task_set, load_task_set


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0
        self.base = datetime(2026, 8, 26, tzinfo=timezone.utc)
        self.uuid_index = 0

    def tick(self, seconds: float = 1.0) -> None:
        self.value += seconds

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return self.base + timedelta(seconds=self.value - 1000.0)

    def uuid(self) -> uuid.UUID:
        self.uuid_index += 1
        return uuid.uuid5(uuid.NAMESPACE_URL, f"smoke-event-{self.uuid_index}")


def _spawner(participant_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(name=participant_id),
        user_options={},
        extra_annotations={},
        extra_resource_guarantees={},
        extra_resource_limits={},
        log=SimpleNamespace(info=lambda *args, **kwargs: None),
    )


async def _exercise(tmp: Path, task_set_path: Path) -> dict[str, object]:
    tasks = load_task_set(task_set_path)
    assignment = generate_assignment_manifest(
        tasks,
        study_id="e3-synthetic-hub-smoke",
        participant_count=1,
        seed=20260827,
        consent_version="consent-synthetic-smoke-v1",
        git_revision="unknown",
        generated_at_utc="2026-08-26T00:00:00Z",
    )
    participant = assignment.assignments[0]
    clock = _Clock()
    backend = P2Recommender()
    preview_runtime = RecommendationPreviewRuntime(
        monotonic=clock.monotonic,
        deployment=DeploymentMetadata(
            backend="p2",
            backend_version=backend.backend_version,
            package_version="intent-spawner-synthetic-smoke",
            package_checksum="a" * 64,
        ),
        catalog=backend.catalog,
        backend=backend,
    )
    store = AppendOnlyEventStore(
        tmp / "events.jsonl", tmp / "completion-markers"
    )
    gates = StudyGateStore(tmp)
    study = StudySessionRuntime(
        assignment_manifest=assignment,
        browser_task_set=browser_safe_task_set(tasks),
        recommendation_runtime=preview_runtime,
        event_store=store,
        gate_store=gates,
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
        uuid_factory=clock.uuid,
        mark_restart_incomplete=False,
    )
    gates.acknowledge_consent(
        assignment,
        participant,
        acknowledged_at_utc="2026-08-26T00:00:00Z",
    )
    spawner = _spawner(participant.participant_id)
    condition_counts = {Condition.B0.value: 0, Condition.P2.value: 0}

    for assigned in participant.task_sequence:
        active = study.current_task(participant.participant_id)
        if active is None or active.assigned.trial_id != assigned.trial_id:
            raise RuntimeError("synthetic smoke task progression drifted")
        if assigned.sequence_index == 4:
            gates.acknowledge_transition(
                assignment,
                participant,
                acknowledged_at_utc=clock.utc_now().isoformat().replace("+00:00", "Z"),
            )
        study.ensure_task_shown(active)
        rendered = options_form(
            preview_runtime,
            active,
            preview_endpoint="/hub/study/preview",
            event_endpoint="/hub/study/event",
            advance_endpoint="/hub/study/advance",
            consent_version=assignment.consent_version,
        )
        condition_counts[assigned.condition.value] += 1
        clock.tick()
        if assigned.condition is Condition.B0:
            if 'id="b0-controls"' not in rendered or 'id="p2-controls"' in rendered:
                raise RuntimeError("B0 smoke form leaked P2 controls")
            study.record(
                participant.participant_id,
                assigned.trial_id,
                EventType.PROFILE_CHANGED,
                old_profile_id=None,
                new_profile_id="small",
            )
            clock.tick()
            study.record(
                participant.participant_id,
                assigned.trial_id,
                EventType.IMAGE_CHANGED,
                old_image_id=None,
                new_image_id="minimal-python",
            )
            clock.tick()
            options = _study_options_from_form(
                study,
                spawner,
                {
                    "study_trial_id": [assigned.trial_id],
                    "study_consent_version": [assignment.consent_version],
                    "study_profile": ["small"],
                    "study_image_id": ["minimal-python"],
                },
            )
            spawner.user_options = options
            apply_b0_selection(spawner, preview_runtime, options)
        else:
            if 'id="p2-controls"' not in rendered or 'id="b0-controls"' in rendered:
                raise RuntimeError("P2 smoke form did not isolate recommendation controls")
            study.record(
                participant.participant_id,
                assigned.trial_id,
                EventType.INTENT_FOCUS,
            )
            clock.tick()
            study.record(
                participant.participant_id,
                assigned.trial_id,
                EventType.INTENT_EDIT,
            )
            clock.tick()
            preview = await study.issue_preview(
                participant.participant_id,
                assigned.trial_id,
                active.scenario,
            )
            clock.tick()
            action = "accept"
            override_profile = None
            override_image = None
            if preview.get("requires_manual_override"):
                action = "override"
                recommended = (
                    str(preview["applied_profile"]),
                    str(preview["recommendation"]["image_id"]),
                )
                override_profile, override_image = (
                    ("small", "minimal-python")
                    if recommended != ("small", "minimal-python")
                    else ("medium", "scipy-data-science")
                )
                if override_profile != recommended[0]:
                    study.record(
                        participant.participant_id,
                        assigned.trial_id,
                        EventType.PROFILE_CHANGED,
                        old_profile_id=recommended[0],
                        new_profile_id=override_profile,
                    )
                    clock.tick()
                if override_image != recommended[1]:
                    study.record(
                        participant.participant_id,
                        assigned.trial_id,
                        EventType.IMAGE_CHANGED,
                        old_image_id=recommended[1],
                        new_image_id=override_image,
                    )
                    clock.tick()
                study.record(
                    participant.participant_id,
                    assigned.trial_id,
                    EventType.OVERRIDE,
                    profile_id=override_profile,
                    image_id=override_image,
                )
                clock.tick()
            formdata = {
                "study_trial_id": [assigned.trial_id],
                "study_consent_version": [assignment.consent_version],
                "preview_version": [preview["preview_version"]],
                "decision_action": [action],
                "recommendation_preview_id": [
                    preview["recommendation_preview_id"]
                ],
            }
            if action == "override":
                formdata["override_profile"] = [override_profile]
                formdata["override_image_id"] = [override_image]
            options = _study_options_from_form(
                study,
                spawner,
                formdata,
            )
            spawner.user_options = options
            await preview_runtime.pre_spawn(spawner)
        clock.tick()
        study.record_ready(participant.participant_id, assigned.trial_id)
        clock.tick()

    store.complete_session(
        participant.session_id,
        expected_trial_ids={task.trial_id for task in participant.task_sequence},
    )
    completion = store.read_completion_marker(participant.session_id)
    if completion is None:
        raise RuntimeError("synthetic smoke completion marker is missing")
    gates.record_completed_session(
        assignment,
        participant,
        completed_at_utc=str(completion["completed_at_utc"]),
    )
    assignment_path = tmp / "assignment-manifest.json"
    browser_path = tmp / "browser-task-set.json"
    assignment_path.write_text(
        json.dumps(assignment.to_dict(), sort_keys=True) + "\n", encoding="utf-8"
    )
    browser_path.write_text(
        json.dumps(browser_safe_task_set(tasks), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (tmp / "exclusions.jsonl").touch()
    result_dir = tmp / "finalized-dry-run"
    exit_code = runner_main(
        [
            "finalize",
            "--run-id",
            "synthetic-hub-smoke",
            "--task-set",
            str(task_set_path),
            "--assignments",
            str(assignment_path),
            "--events",
            str(tmp / "events.jsonl"),
            "--sessions",
            str(tmp / "sessions.jsonl"),
            "--exclusions",
            str(tmp / "exclusions.jsonl"),
            "--execution-status",
            "DRY_RUN",
            "--output-dir",
            str(result_dir),
            "--created-at-utc",
            "2026-08-26T01:00:00Z",
        ]
    )
    if exit_code != 0:
        raise RuntimeError("synthetic smoke finalization failed")
    events = store.read_events()
    if any(event["participant_id"] != participant.participant_id for event in events):
        raise RuntimeError("synthetic smoke participant identity drifted")
    assigned_by_trial = {task.trial_id: task for task in participant.task_sequence}
    p2_only = {
        EventType.INTENT_FOCUS.value,
        EventType.INTENT_EDIT.value,
        EventType.PREVIEW_REQUESTED.value,
        EventType.PREVIEW_RECEIVED.value,
        EventType.OVERRIDE.value,
    }
    for trial_id, assigned in assigned_by_trial.items():
        trial = [event for event in events if event["trial_id"] == trial_id]
        if [event["event_index"] for event in trial] != list(range(len(trial))):
            raise RuntimeError("synthetic smoke event ordering drifted")
        if not trial or trial[0]["event_type"] != EventType.TASK_SHOWN.value:
            raise RuntimeError("synthetic smoke task_shown is missing")
        if any(
            event["task_id"] != assigned.task_id
            or event["pair_id"] != assigned.pair_id
            or event["condition"] != assigned.condition.value
            for event in trial
        ):
            raise RuntimeError("synthetic smoke task identity propagation drifted")
        confirms = [
            event for event in trial if event["event_type"] == EventType.CONFIRM.value
        ]
        ready = [
            event
            for event in trial
            if event["event_type"] == EventType.NOTEBOOK_READY.value
        ]
        if len(confirms) != 1 or not confirms[0]["profile_id"] or not confirms[0]["image_id"]:
            raise RuntimeError("synthetic smoke final selection was not captured")
        if len(ready) != 1 or (
            ready[0]["profile_id"], ready[0]["image_id"]
        ) != (confirms[0]["profile_id"], confirms[0]["image_id"]):
            raise RuntimeError("synthetic smoke notebook readiness is stale or misbound")
        if assigned.condition is Condition.B0 and any(
            event["event_type"] in p2_only for event in trial
        ):
            raise RuntimeError("synthetic smoke leaked P2 events into B0")
    if not all((result_dir / name).is_file() for name in ("manifest.json", "derived/summary.json", "report/status.json")):
        raise RuntimeError("synthetic smoke finalization artifacts are incomplete")
    return {
        "schema_version": "protocol-v5-user-study-hub-smoke-v1.0.0",
        "status": "PASS",
        "evidence_class": "SYNTHETIC_DRY_RUN",
        "participant_id": participant.participant_id,
        "task_count": len(participant.task_sequence),
        "condition_task_counts": condition_counts,
        "event_count": len(events),
        "notebook_ready_count": sum(
            event["event_type"] == EventType.NOTEBOOK_READY.value for event in events
        ),
        "finalized_measured_task_count": sum(
            1 for _ in (result_dir / "derived/task-outcomes.jsonl").read_text().splitlines()
        ),
        "temporary_output_removed": True,
    }


def run_synthetic_hub_smoke(task_set_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="intent-spawner-user-study-smoke-") as raw:
        return asyncio.run(_exercise(Path(raw), task_set_path))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task-set",
        type=Path,
        default=Path("benchmarks_v5/user-study-draft-v1.yaml"),
    )
    args = parser.parse_args()
    result = run_synthetic_hub_smoke(args.task_set.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_synthetic_hub_smoke"]
