"""Render compact, dependency-free SVG figures from protocol-v4 CSV outputs."""

from __future__ import annotations

import argparse
import csv
from html import escape
from pathlib import Path
from typing import Iterable


COLORS = ("#2563eb", "#16a34a", "#dc2626", "#7c3aed")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _label(value: str) -> str:
    return {
        "rule_based_context": "Context rule",
        "rule_based_intent_only": "Intent-only rule",
        "static_large": "Static large",
        "static_small": "Static small",
    }.get(value, value)


def _svg_start(title: str, subtitle: str, *, width: int = 980, height: int = 560) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Inter,Arial,sans-serif;fill:#172033}.title{font-size:24px;font-weight:700}.sub{font-size:13px;fill:#526078}.axis{stroke:#8290a7;stroke-width:1}.grid{stroke:#dce2ea;stroke-width:1}.tick{font-size:12px}.label{font-size:13px;font-weight:600}.value{font-size:11px}</style>',
        f'<text x="70" y="40" class="title">{escape(title)}</text>',
        f'<text x="70" y="64" class="sub">{escape(subtitle)}</text>',
    ]


def _axes(svg: list[str], *, y_max: float, y_label: str) -> None:
    left, top, bottom, right = 90, 95, 475, 940
    for index in range(6):
        value = y_max * index / 5
        y = bottom - (bottom - top) * index / 5
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" class="grid"/>')
        svg.append(f'<text x="78" y="{y + 4:.1f}" text-anchor="end" class="tick">{value:.1f}</text>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis"/>')
    svg.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="axis"/>')
    svg.append(f'<text x="22" y="290" transform="rotate(-90 22 290)" text-anchor="middle" class="label">{escape(y_label)}</text>')


def _grouped_bars(
    rows: Iterable[dict[str, str]],
    series: tuple[tuple[str, str], ...],
    *,
    title: str,
    subtitle: str,
    y_max: float,
    y_label: str,
) -> str:
    data = list(rows)
    svg = _svg_start(title, subtitle)
    _axes(svg, y_max=y_max, y_label=y_label)
    left, top, bottom, right = 90, 95, 475, 940
    group_width = (right - left) / len(data)
    bar_width = min(42, group_width / (len(series) + 1))
    for row_index, row in enumerate(data):
        center = left + group_width * (row_index + 0.5)
        for series_index, (field, legend) in enumerate(series):
            value = float(row[field])
            height = min(value / y_max, 1.0) * (bottom - top)
            x = center + (series_index - (len(series) - 1) / 2) * (bar_width + 5) - bar_width / 2
            y = bottom - height
            color = COLORS[series_index]
            svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{height:.1f}" fill="{color}" rx="2"/>')
            svg.append(f'<text x="{x + bar_width / 2:.1f}" y="{max(y - 6, top + 10):.1f}" text-anchor="middle" class="value">{value:.3f}</text>')
        svg.append(f'<text x="{center:.1f}" y="500" text-anchor="middle" class="label">{escape(_label(row["recommender"]))}</text>')
    legend_x = 120
    for index, (_, legend) in enumerate(series):
        x = legend_x + index * 230
        svg.append(f'<rect x="{x}" y="530" width="14" height="14" fill="{COLORS[index]}"/>')
        svg.append(f'<text x="{x + 21}" y="542" class="tick">{escape(legend)}</text>')
    svg.append('</svg>')
    return "\n".join(svg) + "\n"


def render(analysis_dir: Path) -> list[Path]:
    out = analysis_dir / "figures"
    out.mkdir(exist_ok=False)
    system = _rows(analysis_dir / "system-effectiveness.csv")
    recommendation = _rows(analysis_dir / "recommendation-summary.csv")
    figures = {
        "system-outcomes.svg": _grouped_bars(
            system,
            (("workload_success_rate", "Workload success"), ("oom_killed_rate", "OOMKilled"), ("pending_failure_rate", "Pending / unschedulable")),
            title="Observed Kubernetes outcomes",
            subtitle="80 paired trials per strategy; intervals remain in the accompanying CSV tables.",
            y_max=1.0,
            y_label="Rate",
        ),
        "resource-efficiency.svg": _grouped_bars(
            system,
            (("cpu_request_utilization_mean", "CPU / request"), ("memory_request_utilization_mean", "Memory / request"), ("peak_memory_to_request_mean", "Peak memory / request")),
            title="Resource-request efficiency on successful measured trials",
            subtitle="Ratios above 1 indicate observed usage exceeding the requested resource; availability differs after OOM.",
            y_max=2.5,
            y_label="Mean utilization ratio",
        ),
        "recommendation-quality.svg": _grouped_bars(
            recommendation,
            (("joint_acceptable_rate", "Joint acceptable"), ("underprovisioned_rate", "Under-provisioned"), ("overprovisioned_rate", "Over-provisioned")),
            title="Offline recommendation quality",
            subtitle="48 held-out samples across 20 workload families; family-bootstrap uncertainty is tabulated separately.",
            y_max=1.0,
            y_label="Rate",
        ),
    }
    paths = []
    for name, content in figures.items():
        path = out / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis_dir", type=Path)
    args = parser.parse_args()
    for path in render(args.analysis_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
