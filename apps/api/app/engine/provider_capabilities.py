"""Endpoint + model-family wire policy, not a provider-name quality heuristic.

Reviewed against official Gemini 3.6/3.8 migration, xAI text best practices
and caching docs, and OpenAI reasoning/latest-model docs (2026-10-27).
This is a conservative Chat Completions / generateContent adapter, not a
Responses migration. Unknown compatible endpoints retain the legacy contract.
"""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


def verified_endpoint(url: str, host: str, paths: tuple[str, ...]) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == host
                and parsed.port in (None, 443) and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path.rstrip("/") in paths)
    except ValueError:
        return False


def safe_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def safe_model_version(value: object) -> str | None:
    # Identifiers only: never persist arbitrary response text as diagnostics.
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value) else None


@dataclass(frozen=True)
class Capabilities:
    policy: str = "generic_compatible"
    output_cap_parameter: str = "max_tokens"
    temperature: float | None = .7
    top_p: float | None = None
    cache_affinity: bool = False

    def diagnostics(self) -> dict:
        omitted = ["temperature"] if self.temperature is None else []
        return {
            "policy": self.policy,
            "output_cap_parameter": self.output_cap_parameter,
            "omitted_parameters": omitted,
            "warning_codes": ["sampling_omitted"] if omitted else [],
            "provider_warning_count": 0,
        }


def capabilities(*, model: str, endpoint: str, transport: str,
                 reasoning_effort: str | None = None) -> Capabilities:
    if transport == "generate_content" and verified_endpoint(
        endpoint, "generativelanguage.googleapis.com", ("/v1beta", "/v1")
    ):
        match = re.match(r"^gemini-(\d+)(?:\.(\d+))?(?:-|$)", model)
        major, minor = (int(match[1]), int(match[2] or 0)) if match else (0, 0)
        # 3.5 Flash is a documented auto-routed 3.6 alias. The 3.6 guide
        # explicitly includes 3.5 Flash-Lite in sampling deprecation too.
        ignored = (major >= 4 or (major == 3 and minor >= 6)
                   or model in {"gemini-3.5-flash", "gemini-3.5-flash-lite"})
        return Capabilities(
            policy="gemini_sampling_ignored" if ignored else "gemini_sampling",
            output_cap_parameter="maxOutputTokens",
            temperature=None if ignored else .7,
        )
    if transport != "chat_completions":
        return Capabilities()
    if verified_endpoint(endpoint, "api.x.ai", ("/v1",)):
        if re.match(r"^grok-\d+(?:[.-]|$)", model):
            # Accepted range is 0..2; sampler cap of 1 is NOT evidence of 400.
            # Legacy 1.1 was sampled as 1: preserve that effective sampling,
            # rather than silently switch RP variance to recommended .7.
            return Capabilities(policy="xai_grok", temperature=1.0, top_p=.95, cache_affinity=True)
        return Capabilities(cache_affinity=True)
    if verified_endpoint(endpoint, "api.openai.com", ("/v1",)):
        reasoning = bool(re.match(r"^(?:o[134](?:-|$)|gpt-(?:5|6)(?:[.-]|$))", model))
        # Chat-only aliases are not reasoning models. Never rewrite their slug.
        reasoning = reasoning and "-chat" not in model
        if reasoning:
            # New GPT families allow sampling with explicitly selected `none`;
            # older o-series/GPT-5 do not. Unknown/default effort stays omitted.
            none_sampling = bool(re.match(r"^gpt-(?:5\.[1-9]\d*(?:-|$)|6(?:\.1)?-(?:sol|terra|luna)(?:-|$))", model))
            none_sampling = none_sampling and not model.startswith("gpt-6.1-sol")
            sampling = reasoning_effort == "none" and none_sampling
            return Capabilities(policy="openai_reasoning", output_cap_parameter="max_completion_tokens",
                                temperature=.7 if sampling else None)
    return Capabilities()
