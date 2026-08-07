#!/usr/bin/env python3
"""Preview catalog/dynamic resource selection without creating a pod."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommender.dynamic_resources import (  # noqa: E402
    DEFAULT_RESOURCE_POLICY_PATH,
    ResourceSelector,
    load_resource_policy,
)
from recommender.recommender import recommend_profile  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview policy-bounded Catalog or Dynamic resource selection."
    )
    parser.add_argument("--intent", default="")
    parser.add_argument("--dataset-gb", type=float, default=0.0)
    parser.add_argument("--code-context", default="")
    parser.add_argument("--mode", choices=("catalog", "dynamic"), default=None)
    parser.add_argument("--policy", type=Path, default=DEFAULT_RESOURCE_POLICY_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    recommendation = recommend_profile(
        args.intent,
        args.dataset_gb,
        args.code_context,
    )
    selector = ResourceSelector(load_resource_policy(args.policy), mode=args.mode)
    decision = selector.select(
        recommended_profile=recommendation.profile,
        score=recommendation.score,
        dataset_size_gb=args.dataset_gb,
    )
    print(
        json.dumps(
            {
                "recommendation": recommendation.to_dict(),
                "resource_decision": decision.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
