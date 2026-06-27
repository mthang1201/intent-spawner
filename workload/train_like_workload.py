"""Bounded training-like workload for the proposed recommendation demo."""

from __future__ import annotations

import os
import time


TARGET_MIB = int(os.environ.get("TRAIN_LIKE_TARGET_MIB", "512"))
BLOCK_MIB = int(os.environ.get("TRAIN_LIKE_BLOCK_MIB", "64"))
SLEEP_SECONDS = float(os.environ.get("TRAIN_LIKE_SLEEP_SECONDS", "0.3"))


def main() -> None:
    print("Proposed-method training-like workload")
    print(f"RECOMMENDED_PROFILE={os.environ.get('RECOMMENDED_PROFILE', '<unset>')}")
    print(f"RECOMMENDATION_REASONS={os.environ.get('RECOMMENDATION_REASONS', '<unset>')}")

    blocks: list[bytearray] = []
    allocated = 0
    while allocated < TARGET_MIB:
        blocks.append(bytearray(BLOCK_MIB * 1024 * 1024))
        allocated += BLOCK_MIB
        print(f"allocated_mib={allocated}", flush=True)
        time.sleep(SLEEP_SECONDS)

    print("Training-like workload finished without OOM.")


if __name__ == "__main__":
    main()

