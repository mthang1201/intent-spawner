"""Reproducible chart rendering for Protocol-v5 E5 image storage and catalog scalability."""

from __future__ import annotations

from collections.abc import Sequence
import logging
from pathlib import Path
from typing import Any

from .storage_contracts import (
    MarginalStorageRecord,
    PairwiseReuseAnalysis,
    PrefixStorageMeasurement,
    ScaleLevelEvaluationRecord,
)

logger = logging.getLogger(__name__)


def generate_all_figures(
    *,
    prefixes: Sequence[PrefixStorageMeasurement],
    marginal_records: Sequence[MarginalStorageRecord],
    pairwise_analysis: PairwiseReuseAnalysis,
    scale_records: Sequence[ScaleLevelEvaluationRecord],
    output_dir: Path,
) -> dict[str, str]:
    """Generate reproducible Figures A through E from persisted evidence data.

    Returns a mapping of figure keys to relative file paths.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}

    # 1. Figure A: Cumulative Storage (Logical vs Unique Layer Bytes)
    fig_a_path = output_dir / "figure_a_cumulative_storage.png"
    fig_a_svg = output_dir / "figure_a_cumulative_storage.svg"

    x_vals = [p.prefix_size for p in prefixes]
    logical_gib = [p.naive_logical_bytes / (1024**3) for p in prefixes]
    unique_gib = [p.unique_layer_bytes / (1024**3) for p in prefixes]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(x_vals, logical_gib, marker="o", color="#d9534f", label="Logical Image Bytes (Naive Sum)", linewidth=2)
    ax.plot(x_vals, unique_gib, marker="s", color="#2e6da4", label="Unique Layer Bytes (Deduplicated)", linewidth=2)
    ax.fill_between(x_vals, unique_gib, logical_gib, color="#2e6da4", alpha=0.15, label="Layer Deduplication Savings")
    ax.set_title("Figure A: Cumulative Storage vs Available Catalog Images", fontsize=12, fontweight="bold")
    ax.set_xlabel("Number of Available Images (Catalog Prefix Size)", fontsize=10)
    ax.set_ylabel("Storage (GiB - Compressed OCI Layer Blobs)", fontsize=10)
    ax.set_xticks(x_vals)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper left")
    # layout handled by bbox_inches
    fig.savefig(fig_a_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_a_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_a"] = fig_a_path.name
    generated["figure_a_svg"] = fig_a_svg.name

    # 2. Figure B: Marginal Unique Bytes per Image (U_n - U_(n-1))
    fig_b_path = output_dir / "figure_b_marginal_storage.png"
    fig_b_svg = output_dir / "figure_b_marginal_storage.svg"

    m_indices = [m.introduction_index for m in marginal_records]
    m_labels = [f"{m.introduction_index}: {m.image_id}" for m in marginal_records]
    marginal_gib = [m.marginal_unique_bytes / (1024**3) for m in marginal_records]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(m_indices, marginal_gib, color="#5cb85c", width=0.5, edgecolor="#4cae4c")
    ax.set_title("Figure B: Marginal Unique Storage per Introduced Image (U_n - U_{n-1})", fontsize=12, fontweight="bold")
    ax.set_xlabel("Introduced Image", fontsize=10)
    ax.set_ylabel("Marginal Unique Bytes (GiB)", fontsize=10)
    ax.set_xticks(m_indices)
    ax.set_xticklabels(m_labels, rotation=15, ha="right", fontsize=9)
    for bar, val in zip(bars, marginal_gib):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05, f"{val:.2f} GiB", ha="center", va="bottom", fontsize=8)
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    # layout handled by bbox_inches
    fig.savefig(fig_b_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_b_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_b"] = fig_b_path.name
    generated["figure_b_svg"] = fig_b_svg.name

    # 3. Figure C: Pairwise Layer-Reuse Heatmaps (Bytes and Count)
    fig_c_bytes_path = output_dir / "figure_c_pairwise_reuse_bytes.png"
    fig_c_bytes_svg = output_dir / "figure_c_pairwise_reuse_bytes.svg"

    n_images = len(pairwise_analysis.image_ids)
    short_names = [img.replace("-deep-learning", "").replace("-data-science", "") for img in pairwise_analysis.image_ids]
    byte_matrix_gib = [
        [b / (1024**3) for b in row]
        for row in pairwise_analysis.shared_layer_byte_matrix
    ]

    fig, ax = plt.subplots(figsize=(7, 6))
    cax = ax.matshow(byte_matrix_gib, cmap="Blues")
    fig.colorbar(cax, label="Shared Layer Storage (GiB)")
    ax.set_title("Figure C: Pairwise Shared Layer Storage (GiB)", fontsize=12, fontweight="bold", pad=20)
    ax.set_xticks(range(n_images))
    ax.set_yticks(range(n_images))
    ax.set_xticklabels(short_names, rotation=25, ha="left", fontsize=9)
    ax.set_yticklabels(short_names, fontsize=9)
    for i in range(n_images):
        for j in range(n_images):
            val = byte_matrix_gib[i][j]
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="white" if val > 2.5 else "black", fontsize=9)
    # layout handled by bbox_inches
    fig.savefig(fig_c_bytes_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_c_bytes_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_c_bytes"] = fig_c_bytes_path.name
    generated["figure_c_bytes_svg"] = fig_c_bytes_svg.name

    fig_c_count_path = output_dir / "figure_c_pairwise_reuse_count.png"
    fig_c_count_svg = output_dir / "figure_c_pairwise_reuse_count.svg"

    count_matrix = pairwise_analysis.shared_layer_count_matrix
    fig, ax = plt.subplots(figsize=(7, 6))
    cax2 = ax.matshow(count_matrix, cmap="Purples")
    fig.colorbar(cax2, label="Shared Layer Count")
    ax.set_title("Figure C: Pairwise Shared Layer Count", fontsize=12, fontweight="bold", pad=20)
    ax.set_xticks(range(n_images))
    ax.set_yticks(range(n_images))
    ax.set_xticklabels(short_names, rotation=25, ha="left", fontsize=9)
    ax.set_yticklabels(short_names, fontsize=9)
    for i in range(n_images):
        for j in range(n_images):
            c_val = count_matrix[i][j]
            ax.text(j, i, str(c_val), ha="center", va="center", color="white" if c_val > 15 else "black", fontsize=9)
    # layout handled by bbox_inches
    fig.savefig(fig_c_count_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_c_count_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_c_count"] = fig_c_count_path.name
    generated["figure_c_count_svg"] = fig_c_count_svg.name

    # 4. Figure D: Recommendation Quality vs Catalog Size
    fig_d_path = output_dir / "figure_d_recommendation_quality.png"
    fig_d_svg = output_dir / "figure_d_recommendation_quality.svg"

    obs_scales = [s for s in scale_records if s.p2_evaluation_status == "OBSERVED"]
    all_scale_sizes = [s.catalog_size for s in scale_records]

    fig, ax = plt.subplots(figsize=(8, 5))
    if obs_scales:
        s_x = [s.catalog_size for s in obs_scales]
        acc_vals = [s.p2_image_acceptable_accuracy for s in obs_scales]
        pref_vals = [s.p2_image_preferred_accuracy for s in obs_scales]
        rec_vals = [s.p2_retrieval_recall_at_k for s in obs_scales]

        ax.scatter(s_x, acc_vals, color="#337ab7", s=80, zorder=5, label="P2 Image Acceptable Accuracy")
        ax.scatter(s_x, pref_vals, color="#5bc0de", s=80, marker="^", zorder=5, label="P2 Image Preferred Accuracy")
        ax.scatter(s_x, rec_vals, color="#f0ad4e", s=80, marker="D", zorder=5, label=f"P2 Retrieval Recall@{obs_scales[0].recall_k}")

        if len(s_x) > 1:
            ax.plot(s_x, acc_vals, color="#337ab7", linestyle="-")
            ax.plot(s_x, pref_vals, color="#5bc0de", linestyle="--")
            ax.plot(s_x, rec_vals, color="#f0ad4e", linestyle=":")
        else:
            # Single observed point: draw horizontal dashed guide
            ax.axhline(acc_vals[0], color="#337ab7", linestyle=":", alpha=0.4)
            ax.axhline(rec_vals[0], color="#f0ad4e", linestyle=":", alpha=0.4)

    for s in scale_records:
        if s.p2_evaluation_status != "OBSERVED":
            ax.axvline(s.catalog_size, color="gray", linestyle="--", alpha=0.5)
            ax.text(s.catalog_size, 0.45, f"Scale {s.catalog_size}\n(NOT_EXECUTED)", rotation=90, va="center", ha="right", color="gray", fontsize=8)

    ax.set_title("Figure D: P2 Recommendation Accuracy & Retrieval Recall vs Catalog Size", fontsize=12, fontweight="bold")
    ax.set_xlabel("Catalog Size (Approved Images)", fontsize=10)
    ax.set_ylabel("Metric Score [0.0 - 1.0]", fontsize=10)
    ax.set_ylim(0.0, 1.05)
    ax.set_xticks(all_scale_sizes)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="lower left")
    # layout handled by bbox_inches
    fig.savefig(fig_d_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_d_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_d"] = fig_d_path.name
    generated["figure_d_svg"] = fig_d_svg.name

    # 5. Figure E: Recommendation Latency vs Catalog Size
    fig_e_path = output_dir / "figure_e_recommendation_latency.png"
    fig_e_svg = output_dir / "figure_e_recommendation_latency.svg"

    fig, ax = plt.subplots(figsize=(8, 5))
    if obs_scales:
        s_x = [s.catalog_size for s in obs_scales]
        lat_ms = [((s.p2_latency_mean_seconds or 0.0) * 1000.0) for s in obs_scales]
        lat_p95 = [((s.p2_latency_p95_seconds or 0.0) * 1000.0) for s in obs_scales]
        lat_min = [((s.p2_latency_min_seconds or 0.0) * 1000.0) for s in obs_scales]
        lat_max = [((s.p2_latency_max_seconds or 0.0) * 1000.0) for s in obs_scales]

        ax.errorbar(s_x, lat_ms, yerr=[[m - mn for m, mn in zip(lat_ms, lat_min)], [mx - m for m, mx in zip(lat_ms, lat_max)]], fmt="o", color="#d9534f", ecolor="#d9534f", elinewidth=2, capsize=5, label="Mean Latency (Min-Max Range)", zorder=5)
        ax.scatter(s_x, lat_p95, color="#f0ad4e", marker="s", s=60, zorder=6, label="95th Percentile Latency")

    for s in scale_records:
        if s.p2_evaluation_status != "OBSERVED":
            ax.axvline(s.catalog_size, color="gray", linestyle="--", alpha=0.5)
            ax.text(s.catalog_size, 5.0, f"Scale {s.catalog_size}\n(NOT_EXECUTED)", rotation=90, va="center", ha="right", color="gray", fontsize=8)

    ax.set_title("Figure E: P2 Recommendation Latency vs Catalog Size", fontsize=12, fontweight="bold")
    ax.set_xlabel("Catalog Size (Approved Images)", fontsize=10)
    ax.set_ylabel("Recommendation Latency (ms)", fontsize=10)
    ax.set_xticks(all_scale_sizes)
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper left")
    # layout handled by bbox_inches
    fig.savefig(fig_e_path, dpi=200, bbox_inches="tight")
    fig.savefig(fig_e_svg, bbox_inches="tight")
    plt.close(fig)
    generated["figure_e"] = fig_e_path.name
    generated["figure_e_svg"] = fig_e_svg.name

    return generated
