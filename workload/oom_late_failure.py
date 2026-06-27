"""Gradually allocate memory until a low-memory container is OOMKilled.

The defaults are tuned for the demo Small profile, whose memory limit is 384Mi.
The script allocates in small chunks with sleeps so the failure is observable
instead of instant. It is intentionally capped to avoid dangerous host pressure.
"""

from __future__ import annotations

import os
import time


BLOCK_MIB = int(os.environ.get("OOM_BLOCK_MIB", "32"))
TARGET_MIB = int(os.environ.get("OOM_TARGET_MIB", "640"))
SLEEP_SECONDS = float(os.environ.get("OOM_SLEEP_SECONDS", "1.0"))
MAX_SAFE_MIB = int(os.environ.get("OOM_MAX_SAFE_MIB", "1024"))


def main() -> None:
    target_mib = min(TARGET_MIB, MAX_SAFE_MIB)
    blocks: list[bytearray] = []

    print(
        f"Starting gradual allocation: block={BLOCK_MIB}MiB, "
        f"target={target_mib}MiB, sleep={SLEEP_SECONDS}s",
        flush=True,
    )

    allocated = 0
    while allocated < target_mib:
        blocks.append(bytearray(BLOCK_MIB * 1024 * 1024))
        allocated += BLOCK_MIB
        print(f"allocated_mib={allocated}", flush=True)
        time.sleep(SLEEP_SECONDS)

    print("Completed allocation without OOM. This usually means the profile limit is high enough.")
    time.sleep(30)


if __name__ == "__main__":
    main()

