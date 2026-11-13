"""Request-owned LiteLLM Vertex Gemini driver (pinned SDK interface).

The maintained SDK builds the URL, headers and native request. Only credential
acquisition and the application's explicit-region policy are overridden. This
avoids LiteLLM's process-global Vertex credential cache and ambient key fallback.
"""
from copy import deepcopy
from types import SimpleNamespace

from app.engine import vertex_auth


async def sdk_completion(route, *, messages, parameters, client, timeout):
    from app.engine.litellm_transport import load_sdk, TransportConfigurationError
    sdk = load_sdk()
    from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexLLM, VertexGeminiConfig
    from litellm.llms.vertex_ai.gemini.transformation import get_supports_response_schema

    if any(not isinstance(message, dict) or not message.keys() <= {'role', 'content'}
           or message.get('role') not in {'system', 'user', 'assistant'}
           or not isinstance(message.get('content'), str) for message in messages):
        raise TransportConfigurationError('Vertex currently supports text messages only')
    config = VertexGeminiConfig()
    allowed = {'temperature', 'top_p', 'top_k', 'max_tokens', 'response_format', 'thinkingConfig', 'safety_settings'}
    if not parameters.keys() <= allowed:
        raise TransportConfigurationError('Unsupported Vertex request control')
    params = deepcopy(parameters)
    native = {key: params.pop(key) for key in ('thinkingConfig', 'safety_settings') if key in params}
    optional = config.map_openai_params(params, {}, route.model, drop_params=False)
    optional.update(native)
    # Do not let an SDK capability fallback append schema prose to user input.
    if 'response_schema' in optional and get_supports_response_schema(model=route.model, custom_llm_provider='vertex_ai') is False:
        raise TransportConfigurationError('Native Vertex response schema is unsupported for this model')
    token = await vertex_auth.acquire_token(route, timeout)

    class OwnedVertex(VertexLLM):
        async def _ensure_access_token_async(self, **kwargs):
            return token, route.vertex_project

        @staticmethod
        def get_vertex_region(vertex_region, model):
            # Account location is authoritative; never silently change residency.
            return vertex_region

    # The lower maintained SDK driver needs logging callbacks; the app captures
    # bounded native evidence instead. No raw prompts/credentials reach callbacks.
    log = SimpleNamespace(pre_call=lambda **kw: None, post_call=lambda **kw: None, optional_params={})
    return await OwnedVertex().completion(
        model=route.model, messages=deepcopy(messages), model_response=sdk.ModelResponse(),
        print_verbose=lambda *a, **kw: None, custom_llm_provider='vertex_ai',
        encoding=sdk.encoding, logging_obj=log, optional_params=optional,
        acompletion=True, timeout=timeout, vertex_project=route.vertex_project,
        vertex_location=route.vertex_location, vertex_credentials=None, gemini_api_key=None,
        litellm_params={}, client=client, api_base=None,
    )
