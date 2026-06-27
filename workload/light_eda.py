"""Small/light workload used to show defensive over-requesting waste."""

from __future__ import annotations

import os
import random
import statistics


def main() -> None:
    values = [random.random() for _ in range(10_000)]
    print("Light EDA workload complete.")
    print(f"rows={len(values)} mean={statistics.mean(values):.4f} stdev={statistics.pstdev(values):.4f}")
    print("Observed recommendation/profile env:")
    for key in ("RECOMMENDED_PROFILE", "RECOMMENDATION_REASONS", "CPU_GUARANTEE", "MEM_GUARANTEE", "MEM_LIMIT"):
        print(f"{key}={os.environ.get(key, '<unset>')}")


if __name__ == "__main__":
    main()

