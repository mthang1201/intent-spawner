"""Registry and configuration-based factory for recommendation backends."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from typing import Any

from .base import Recommender
from .external_llm import ExternalLLMRecommender
from .rule_based import RuleBasedRecommender
from .self_hosted_llm import SelfHostedLLMRecommender


DEFAULT_BACKEND = "rule_based"
BACKEND_ENV_VAR = "RECOMMENDER_BACKEND"
RecommenderFactory = Callable[..., Recommender]


class RecommenderRegistry:
    """Explicit mapping from stable backend names to constructors."""

    def __init__(self) -> None:
        self._factories: dict[str, RecommenderFactory] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def register(self, name: str, factory: RecommenderFactory) -> None:
        normalized = name.strip() if isinstance(name, str) else ""
        if not normalized:
            raise ValueError("recommender backend name must not be blank")
        if normalized in self._factories:
            raise ValueError(f"recommender backend {normalized!r} is already registered")
        if not callable(factory):
            raise TypeError("recommender backend factory must be callable")
        self._factories[normalized] = factory

    def create(self, name: str, **configuration: Any) -> Recommender:
        normalized = name.strip() if isinstance(name, str) else ""
        if normalized not in self._factories:
            available = ", ".join(self.names) or "none"
            raise ValueError(
                f"unknown recommender backend {name!r}; registered backends: {available}"
            )
        backend = self._factories[normalized](**configuration)
        if not isinstance(backend, Recommender):
            raise TypeError(
                f"recommender backend factory {normalized!r} did not return a Recommender"
            )
        return backend


DEFAULT_REGISTRY = RecommenderRegistry()
DEFAULT_REGISTRY.register(DEFAULT_BACKEND, RuleBasedRecommender)
DEFAULT_REGISTRY.register("rule_based_mapping", RuleBasedRecommender)
DEFAULT_REGISTRY.register("rule_based_context", RuleBasedRecommender)
DEFAULT_REGISTRY.register("external_llm", ExternalLLMRecommender)
DEFAULT_REGISTRY.register("self_hosted_llm", SelfHostedLLMRecommender)
DEFAULT_REGISTRY.register("self_hosted_local_ollama_llm", SelfHostedLLMRecommender)



def configured_backend_name(
    backend_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve an explicit or environment-selected backend name."""

    if backend_name is not None:
        return backend_name
    selected_environ = os.environ if environ is None else environ
    return selected_environ.get(BACKEND_ENV_VAR, DEFAULT_BACKEND)


def create_recommender(
    backend_name: str | None = None,
    *,
    registry: RecommenderRegistry = DEFAULT_REGISTRY,
    environ: Mapping[str, str] | None = None,
    **configuration: Any,
) -> Recommender:
    """Create the explicitly configured backend, failing closed on errors."""

    return registry.create(
        configured_backend_name(backend_name, environ=environ),
        **configuration,
    )
