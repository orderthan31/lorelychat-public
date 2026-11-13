"""Cached, offline SDK capability resolution; no model-family policy tables.

Effort is an application intent, not proof of provider-side reasoning. Only the
transport's captured wire controls describe serialization. The SDK alone maps
that intent into native provider controls.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

from app.engine.llm_error_types import LLMCapabilityError

RESERVES = {'low': 4096, 'medium': 8192, 'high': 16384, 'xhigh': 32768, 'max': 65536}
CHAT_PURPOSES = frozenset({'chat_generation', 'chat_generation_fallback', 'chat_command_postprocess'})
_PROVIDER_ALIASES = {'google': 'gemini', 'openai_compatible': 'openai'}


def positive_int(value):
    return value if type(value) is int and value > 0 else None


@dataclass(frozen=True)
class ReasoningResolution:
    requested: str | None
    effective: str | None
    reason: str
    supported: tuple[str, ...] | None
    model: str
    provider: str
    model_info: dict
    supported_params: tuple[str, ...]

    def diagnostics(self):
        return {'requested_effort': self.requested, 'effective_effort': self.effective,
                'resolution_reason': self.reason, 'supported_efforts': self.supported,
                'capability_model': self.model, 'capability_source': 'installed_sdk'}


def resolve(client, model=None):
    from app.engine.litellm_transport import load_sdk
    sdk = load_sdk()
    from litellm.router_utils.reasoning_effort_capability import (
        declared_reasoning_efforts, resolve_supported_reasoning_efforts,
        nearest_declared_reasoning_effort,
    )
    model = model or client.model()
    provider = _PROVIDER_ALIASES.get(client.provider(), client.provider())
    requested = client.overrides.get('reasoning_effort')
    # Local bundled metadata only: capability discovery must never fetch models,
    # credentials, remote pricing or perform a trial inference.
    key = f'{provider}/{model}'
    entry = sdk.model_cost.get(key)
    if entry is None:
        key, entry = model, sdk.model_cost.get(model)
    info = deepcopy(entry or {})
    info.update(key=key, litellm_provider=provider)
    params = tuple(sdk.get_supported_openai_params(model=model, custom_llm_provider=provider) or ())
    declared = declared_reasoning_efforts(info)
    supported = resolve_supported_reasoning_efforts(info, deployment_is_mapped=entry is not None)
    # Explicit levels are authoritative even where a catalog omitted the generic
    # supports_reasoning flag. Explicit False remains the escape hatch.
    if declared is not None and info.get('supports_reasoning') is not False:
        supported = declared
    effective = None
    if requested is None:
        reason = 'not_selected'
    elif not isinstance(requested, str) or requested not in RESERVES:
        raise LLMCapabilityError('Unknown reasoning effort; expected low, medium, high, xhigh or max')
    elif supported == () or info.get('supports_reasoning') is False:
        reason = 'nonreasoning_model'
    elif 'reasoning_effort' not in params:
        reason = 'sdk_reasoning_control_unsupported'
    elif supported is None:
        # Unknown levels are not an OFF signal. Do not invent upper-tier support;
        # preserve conventional effort and conservatively cap upper requests.
        effective = 'high' if requested in {'xhigh', 'max'} else requested
        reason = 'incomplete_sdk_levels_conservative_fallback'
    else:
        effective = nearest_declared_reasoning_effort(requested, supported)
        if effective not in supported:
            effective = None
            reason = 'no_supported_reasoning_strength'
        else:
            reason = 'sdk_declared' if effective == requested else 'sdk_level_clamped'
    if effective is not None:
        from litellm.types.utils import LlmProviders
        from litellm.router_utils.reasoning_effort_capability import REASONING_EFFORT_STRENGTH_ORDER
        config = sdk.ProviderConfigManager.get_provider_chat_config(model=model, provider=LlmProviders(provider))
        if config is not None:
            # Catalog levels can include tiers available only on another API
            # (e.g. Responses). Query this route's maintained mapper locally;
            # these are capability checks, never replacement model requests.
            ladder = supported if supported is not None else ('low', 'medium', 'high')
            lower = [level for level in reversed(REASONING_EFFORT_STRENGTH_ORDER)
                     if level in ladder and level in RESERVES
                     and REASONING_EFFORT_STRENGTH_ORDER.index(level)
                     < REASONING_EFFORT_STRENGTH_ORDER.index(effective)]
            for candidate in [effective, *lower]:
                try:
                    config.map_openai_params({'reasoning_effort': candidate}, {}, model, drop_params=False)
                except sdk.UnsupportedParamsError:
                    continue
                except Exception:
                    raise LLMCapabilityError('SDK reasoning capability resolution failed') from None
                if candidate != effective:
                    reason = 'sdk_transport_level_clamped'
                effective = candidate
                break
            else:
                effective, reason = None, 'sdk_transport_effort_unsupported'
    return ReasoningResolution(requested, effective, reason, supported, model, provider, info, params)


def output_budget(client, resolution):
    value = client.overrides.get('min_output_tokens')
    base = max(512, min(8192, int(value) * 2)) if value is not None else None
    reserve = (RESERVES.get(resolution.effective, 0)
               if client.profile == 'chat' and client.purpose in CHAT_PURPOSES else 0)
    # Do not introduce a ceiling for legacy callers that never selected one.
    proposed = base + reserve if base is not None else None
    limits = {}
    if reserve:
        selected = resolution.model == client.model()
        for label, override, sdk_keys in (
            ('output', 'model_max_output_tokens', ('max_output_tokens',)),
            ('context', 'model_context_window_tokens', ('max_input_tokens', 'max_tokens')),
        ):
            limit = positive_int(client.overrides.get(override)) if selected else None
            source = 'selected_model' if limit else 'installed_sdk'
            if limit is None:
                limit = next((positive_int(resolution.model_info.get(k)) for k in sdk_keys
                              if positive_int(resolution.model_info.get(k))), None)
            if limit is not None:
                limits[label] = {'tokens': limit, 'source': source}
    final = min([proposed, *(v['tokens'] for v in limits.values())]) if proposed is not None else None
    return {**resolution.diagnostics(), 'visible_base_tokens': base, 'reasoning_reserve_tokens': reserve,
            'proposed_max_output_tokens': proposed, 'final_max_output_tokens': final,
            'clamped': final != proposed, 'limits': limits}


def sampling_parameters(resolution, candidates):
    """Validate sampling with the installed SDK, never model-name conditionals.

    For SDK-native thinking blocks leave implicit sampling provider-owned.
    The caller rejects explicit conflicts instead of silently losing user
    controls. Other parameters are checked with the SDK mapping function
    used at dispatch, with drop_params strictly disabled.
    """
    from app.engine.litellm_transport import load_sdk
    sdk = load_sdk()
    from litellm.types.utils import LlmProviders
    config = sdk.ProviderConfigManager.get_provider_chat_config(
        model=resolution.model, provider=LlmProviders(resolution.provider))
    effort = {'reasoning_effort': resolution.effective}
    native = config.map_openai_params(deepcopy(effort), {}, resolution.model, drop_params=False) if config else {}
    native_thinking = isinstance(native.get('thinking'), dict) and native['thinking'].get('type') in {'enabled', 'adaptive'}
    result, omitted = {}, []
    for name, value in candidates.items():
        if value is None:
            continue
        if (native_thinking or resolution.model_info.get('supports_sampling_params') is False
                or name not in resolution.supported_params):
            omitted.append(name)
            continue
        try:
            if config:
                config.map_openai_params({**effort, name: value}, {}, resolution.model, drop_params=False)
        except sdk.UnsupportedParamsError:
            omitted.append(name)
        else:
            result[name] = value
    return result, omitted
