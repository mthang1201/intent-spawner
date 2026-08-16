from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from recommender.jupyterhub_integration import PREVIEW_MAX_ENTRIES, PREVIEW_VERSION
from test_config_validation import confirm, issue, preview_runtime, spawner


def test_cross_user_token_is_rejected():
    runtime = preview_runtime()
    preview = issue(runtime, intent="demo", dataset_size_gb=0, code_context="")
    with pytest.raises(ValueError, match="not bound"):
        runtime.options_from_form(
            spawner("mallory"),
            {
                "preview_version": [PREVIEW_VERSION],
                "decision_action": ["accept"],
                "recommendation_preview_id": [preview["recommendation_preview_id"]],
            },
        )
    runtime.executor.shutdown()


def test_one_time_token_replay_is_rejected():
    runtime = preview_runtime()
    preview = issue(runtime, intent="demo", dataset_size_gb=0, code_context="")
    options = confirm(runtime, preview)
    asyncio.run(runtime.pre_spawn(spawner(options=options)))
    with pytest.raises(ValueError, match="already used"):
        asyncio.run(runtime.pre_spawn(spawner(options=options)))
    runtime.executor.shutdown()


@pytest.mark.parametrize(
    "patch",
    [
        {"decision_action": "override", "override_profile": "large", "override_image_id": "scipy-data-science"},
        {"applied_profile": "large"},
        {"applied_image_id": "scipy-data-science"},
    ],
)
def test_forged_user_options_cannot_change_confirmed_decision(patch):
    runtime = preview_runtime()
    preview = issue(runtime, intent="basic", dataset_size_gb=0, code_context="")
    options = confirm(runtime, preview)
    options.update(patch)
    with pytest.raises(ValueError, match="not confirmed|changed after"):
        asyncio.run(runtime.pre_spawn(spawner(options=options)))
    assert preview["recommendation_preview_id"] in runtime.previews
    runtime.executor.shutdown()


def test_generation_change_invalidates_token():
    runtime = preview_runtime()
    preview = issue(runtime, intent="demo", dataset_size_gb=0, code_context="")
    runtime.extra_generation["resource_policy_hash"] = "b" * 64
    with pytest.raises(ValueError, match="generation is stale"):
        confirm(runtime, preview)
    runtime.executor.shutdown()


def test_expired_token_is_pruned():
    clock = [10.0]
    runtime = preview_runtime(monotonic=lambda: clock[0])
    preview = issue(runtime, intent="demo", dataset_size_gb=0, code_context="")
    clock[0] += 1801
    with pytest.raises(ValueError, match="unknown, expired"):
        confirm(runtime, preview)
    runtime.executor.shutdown()


def test_multi_tab_tokens_are_isolated():
    runtime = preview_runtime()
    first = issue(runtime, intent="basic", dataset_size_gb=0, code_context="")
    second = issue(runtime, intent="train model", dataset_size_gb=2, code_context="")
    first_options = confirm(runtime, first)
    second_options = confirm(runtime, second)
    asyncio.run(runtime.pre_spawn(spawner(options=first_options)))
    assert second["recommendation_preview_id"] in runtime.previews
    asyncio.run(runtime.pre_spawn(spawner(options=second_options)))
    runtime.executor.shutdown()


def test_preview_store_is_bounded_and_evicts_oldest():
    clock = [1.0]
    runtime = preview_runtime(monotonic=lambda: clock[0])
    first = issue(runtime, intent="first", dataset_size_gb=0, code_context="")
    for index in range(PREVIEW_MAX_ENTRIES):
        clock[0] += 0.001
        issue(runtime, intent=f"item-{index}", dataset_size_gb=0, code_context="")
    assert len(runtime.previews) == PREVIEW_MAX_ENTRIES
    assert first["recommendation_preview_id"] not in runtime.previews
    runtime.executor.shutdown()


def test_preview_record_does_not_trust_mutated_form_context():
    runtime = preview_runtime()
    preview = issue(runtime, intent="basic", dataset_size_gb=0, code_context="")
    options = confirm(runtime, preview)
    forged = deepcopy(options)
    forged["intent"] = "gpu training"
    forged["code_context"] = "import torch"
    target = spawner(options=forged)
    asyncio.run(runtime.pre_spawn(target))
    assert target.cpu_limit == 0.5
    runtime.executor.shutdown()
