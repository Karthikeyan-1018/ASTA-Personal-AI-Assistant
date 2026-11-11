"""Provider routing policy for Phase 9.

The router keeps its historical, provider-agnostic behaviour as the default.
This module only decides *which* provider should be attempted first and which
provider may act as a fallback. It never sends a private request to a cloud
provider: private routing is always resolved to local Ollama, regardless of the
configured policy.

Model names are not duplicated here. They come from provider configuration,
with an optional ``complexity:provider:model`` override map for operators who
want per-complexity models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import settings


logger = logging.getLogger(__name__)


GROQ = "groq"
OLLAMA = "ollama"
SUPPORTED_PROVIDERS = (GROQ, OLLAMA)

GROQ_FIRST = "groq_first"
LOCAL_FIRST = "local_first"
COST_AWARE = "cost_aware"
SUPPORTED_POLICIES = (GROQ_FIRST, LOCAL_FIRST, COST_AWARE)


@dataclass(frozen=True)
class ProviderProfile:
    """Static, configuration-derived facts about one provider."""

    name: str
    default_model: str
    context_window_tokens: int
    input_cost_per_million: float
    output_cost_per_million: float
    is_local: bool


@dataclass(frozen=True)
class RoutingDecision:
    """The ordered providers a request is allowed to use."""

    primary: str
    fallback: str | None
    provider_order: tuple[str, ...]
    policy: str
    complexity: str
    primary_model: str
    fallback_model: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def private(self) -> bool:
        return self.policy == "private"

    def model_for(self, provider: str) -> str | None:
        if provider == self.primary:
            return self.primary_model
        if provider == self.fallback:
            return self.fallback_model
        return None


def _config_float(value) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def provider_profile(provider: str) -> ProviderProfile:
    """Return the profile for a provider name.

    Raises ``ValueError`` for unknown providers so a configuration typo is
    never silently routed to an unintended destination.
    """

    from app.ai.tokenization import resolve_context_window

    name = (provider or "").strip().lower()

    if name == GROQ:
        model = settings.GROQ_MODEL

        return ProviderProfile(
            name=GROQ,
            default_model=model,
            context_window_tokens=resolve_context_window(
                model=model,
                provider=GROQ,
            ),
            input_cost_per_million=_config_float(
                settings.AI_GROQ_INPUT_COST_PER_MILLION
            ),
            output_cost_per_million=_config_float(
                settings.AI_GROQ_OUTPUT_COST_PER_MILLION
            ),
            is_local=False,
        )

    if name == OLLAMA:
        model = settings.OLLAMA_MODEL

        return ProviderProfile(
            name=OLLAMA,
            default_model=model,
            context_window_tokens=resolve_context_window(
                model=model,
                provider=OLLAMA,
            ),
            input_cost_per_million=_config_float(
                settings.AI_OLLAMA_INPUT_COST_PER_MILLION
            ),
            output_cost_per_million=_config_float(
                settings.AI_OLLAMA_OUTPUT_COST_PER_MILLION
            ),
            is_local=True,
        )

    raise ValueError(f"Unsupported LLM provider: {provider!r}")


def provider_profiles() -> dict[str, ProviderProfile]:
    """Return profiles for every supported provider."""

    return {
        provider: provider_profile(provider)
        for provider in SUPPORTED_PROVIDERS
    }


def _complexity_value(complexity) -> str:
    if complexity is None:
        return "moderate"

    value = getattr(complexity, "value", complexity)

    return str(value).strip().lower() or "moderate"


def resolve_model(provider: str, complexity=None) -> str:
    """Resolve the model for a provider, honouring operator overrides."""

    name = (provider or "").strip().lower()
    profile = provider_profile(name)
    label = _complexity_value(complexity)

    overrides = settings.ai_routing_model_overrides

    for key in ((label, name), ("default", name), ("*", name)):
        override = overrides.get(key)

        if override:
            return override

    return profile.default_model


def _other_provider(provider: str) -> str:
    return OLLAMA if provider == GROQ else GROQ


def _cost_aware_provider(complexity: str) -> str:
    mapping = {
        "simple": settings.AI_ROUTING_SIMPLE_PROVIDER,
        "moderate": settings.AI_ROUTING_MODERATE_PROVIDER,
        "complex": settings.AI_ROUTING_COMPLEX_PROVIDER,
        "dynamic_information": settings.AI_ROUTING_DYNAMIC_PROVIDER,
        "dynamic": settings.AI_ROUTING_DYNAMIC_PROVIDER,
        "task_action": settings.AI_ROUTING_COMPLEX_PROVIDER,
        "private": OLLAMA,
    }

    provider = (mapping.get(complexity) or GROQ).strip().lower()

    if provider not in SUPPORTED_PROVIDERS:
        logger.warning(
            "Unknown cost-aware provider %r; falling back to Groq.",
            provider,
        )
        return GROQ

    return provider


def select_route(
    private: bool = False,
    complexity=None,
    policy: str | None = None,
) -> RoutingDecision:
    """Return the provider order for one request.

    Private requests are always Ollama-only. This is enforced before the
    configured policy is even read, so no policy can route private data to a
    cloud provider.
    """

    label = _complexity_value(complexity)

    if private:
        model = resolve_model(OLLAMA, label)

        return RoutingDecision(
            primary=OLLAMA,
            fallback=None,
            provider_order=(OLLAMA,),
            policy="private",
            complexity=label,
            primary_model=model,
            fallback_model=None,
            reasons=("private requests are local-only",),
        )

    configured = (
        settings.AI_ROUTING_POLICY if policy is None else policy
    )
    configured = (configured or "").strip().lower()

    if configured not in SUPPORTED_POLICIES:
        logger.warning(
            "Unknown AI_ROUTING_POLICY %r; using %s.",
            configured,
            GROQ_FIRST,
        )
        configured = GROQ_FIRST

    if configured == LOCAL_FIRST:
        primary = OLLAMA
        fallback = GROQ
        reasons = ("local_first policy",)
    elif configured == COST_AWARE:
        primary = _cost_aware_provider(label)
        fallback = _other_provider(primary)
        reasons = (f"cost_aware policy for {label} request",)
    else:
        primary = GROQ
        fallback = OLLAMA
        reasons = ("groq_first policy",)

    primary_model = resolve_model(primary, label)
    fallback_model = (
        resolve_model(fallback, label) if fallback else None
    )

    return RoutingDecision(
        primary=primary,
        fallback=fallback,
        provider_order=(
            (primary, fallback) if fallback else (primary,)
        ),
        policy=configured,
        complexity=label,
        primary_model=primary_model,
        fallback_model=fallback_model,
        reasons=reasons,
    )


__all__ = [
    "COST_AWARE",
    "GROQ",
    "GROQ_FIRST",
    "LOCAL_FIRST",
    "OLLAMA",
    "ProviderProfile",
    "RoutingDecision",
    "SUPPORTED_POLICIES",
    "SUPPORTED_PROVIDERS",
    "provider_profile",
    "provider_profiles",
    "resolve_model",
    "select_route",
]
