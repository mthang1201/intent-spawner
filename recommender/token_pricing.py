"""Versioned token pricing provenance and estimation for LLM evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PricingProvenance:
    """Versioned provenance for LLM token pricing estimates."""

    pricing_id: str
    snapshot_date: str
    provider: str
    applicable_model: str
    prompt_price_per_m: float
    completion_price_per_m: float
    source_provenance: str

    def __post_init__(self) -> None:
        if not isinstance(self.pricing_id, str) or not self.pricing_id.strip():
            raise ValueError("pricing_id must be a non-empty string")
        if not isinstance(self.snapshot_date, str) or not self.snapshot_date.strip():
            raise ValueError("snapshot_date must be a non-empty string")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string")
        if not isinstance(self.applicable_model, str) or not self.applicable_model.strip():
            raise ValueError("applicable_model must be a non-empty string")
        if (
            not isinstance(self.prompt_price_per_m, (int, float))
            or isinstance(self.prompt_price_per_m, bool)
            or not math.isfinite(float(self.prompt_price_per_m))
            or float(self.prompt_price_per_m) < 0
        ):
            raise ValueError("prompt_price_per_m must be a non-negative finite number")
        if (
            not isinstance(self.completion_price_per_m, (int, float))
            or isinstance(self.completion_price_per_m, bool)
            or not math.isfinite(float(self.completion_price_per_m))
            or float(self.completion_price_per_m) < 0
        ):
            raise ValueError("completion_price_per_m must be a non-negative finite number")
        if not isinstance(self.source_provenance, str) or not self.source_provenance.strip():
            raise ValueError("source_provenance must be a non-empty string")

    def calculate_cost_usd(
        self, prompt_tokens: int | None, completion_tokens: int | None
    ) -> float | None:
        if prompt_tokens is None or completion_tokens is None:
            return None
        if not isinstance(prompt_tokens, int) or prompt_tokens < 0:
            return None
        if not isinstance(completion_tokens, int) or completion_tokens < 0:
            return None
        return (
            prompt_tokens * (float(self.prompt_price_per_m) / 1_000_000.0)
            + completion_tokens * (float(self.completion_price_per_m) / 1_000_000.0)
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PricingProvenance":
        return cls(
            pricing_id=str(data["pricing_id"]),
            snapshot_date=str(data["snapshot_date"]),
            provider=str(data["provider"]),
            applicable_model=str(data["applicable_model"]),
            prompt_price_per_m=float(data["prompt_price_per_m"]),
            completion_price_per_m=float(data["completion_price_per_m"]),
            source_provenance=str(data["source_provenance"]),
        )

    @classmethod
    def from_file(cls, path: Path | str) -> "PricingProvenance":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("pricing file must contain a JSON object")
        return cls.from_dict(raw)


__all__ = ["PricingProvenance"]
