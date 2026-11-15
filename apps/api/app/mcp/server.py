"""Optional stateless MCP gateway over the shared Agent operation registry."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import inspect
import os
from typing import Any, Callable


def _environment_list(name, default):
    value = os.environ.get(name)
    return tuple(item.strip() for item in value.split(",") if item.strip()) if value is not None else default


@dataclass(frozen=True)
class GatewayOptions:
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: _environment_list(
        "LORECHAT_MCP_ALLOWED_HOSTS", ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*")))
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: _environment_list(
        "LORECHAT_MCP_ALLOWED_ORIGINS", ("http://localhost", "http://127.0.0.1", "http://[::1]")))
    max_request_body_size: int = 1048576
    max_active_calls: int = 8
    max_argument_depth: int = 16
    max_argument_nodes: int = 10000

    def __post_init__(self):
        if not self.allowed_hosts or not self.allowed_origins:
            raise ValueError("MCP allowlists must not be empty")
        # Host port patterns are SDK-supported; origins must always be exact.
        for item in self.allowed_hosts:
            if "*" in item.removesuffix(":*"):
                raise ValueError("MCP wildcard hosts are not allowed")
        if any("*" in item for item in self.allowed_origins):
            raise ValueError("MCP wildcard origins are not allowed")
        if min(self.max_request_body_size, self.max_active_calls,
               self.max_argument_depth, self.max_argument_nodes) < 1:
            raise ValueError("MCP limits must be positive")


def _feature_enabled(service):
    # Read only the control row, never model accounts or secret configuration.
    from sqlalchemy import select
    from app.agent.models import settings
    with service.engine.connect() as connection:
        value = connection.execute(select(settings.c.data).where(settings.c.id == "settings")).scalar_one_or_none()
    return bool(value and value.get("enabled") is True and value.get("mcp_enabled") is True)


class MCPGateway:
    def __init__(self, service_factory, options, enabled_check):
        self.service_factory = service_factory
        self.options = options
        self.enabled_check = enabled_check
        self.server: Any = None
        self._child: Any = None
        self._started = False
        self._admitting = False
        self._lifespan_entered = False
        self.active_calls = 0
        self.asgi_app = self._asgi

    def enabled(self):
        try:
            return bool(self.enabled_check(self.service_factory()))
        except Exception:
            return False

    def stop_admission(self):
        self._admitting = False

    @asynccontextmanager
    async def lifespan(self):
        if self._lifespan_entered:
            raise RuntimeError("MCP gateway lifespan may only start once")
        self._lifespan_entered = True
        if self.server is None:
            yield
            return
        async with self.server.session_manager.run():
            self._started = self._admitting = True
            try:
                yield
            finally:
                self.stop_admission()
                self._started = False

    async def _asgi(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        # No OAuth metadata, redirects, duplicated submounts or private routes.
        if scope.get("path") != "/mcp":
            await JSONResponse({"error": "not_found"}, status_code=404)(scope, receive, send)
            return
        if not self._started or not self._admitting or not self.enabled():
            await JSONResponse({"error": "mcp_unavailable"}, status_code=503)(scope, receive, send)
            return
        await self._child(scope, receive, send)


def _bounded_arguments(value, options):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > options.max_argument_depth or nodes > options.max_argument_nodes:
            raise ValueError("argument_budget")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def build_mcp_gateway(service_factory: Callable, options: GatewayOptions | None = None, *,
                      specs=None, operations_factory=None, enabled_check=None):
    """Build once; host must enter lifespan before serving and stop admission on drain.

    Optional injections are for isolated tests. Production uses the canonical
    ToolSpec/Pydantic registry and checks the injected service's own engine.
    """
    gateway = MCPGateway(service_factory, options or GatewayOptions(), enabled_check or _feature_enabled)
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.server.mcpserver.tools import Tool
        from mcp.server.transport_security import TransportSecuritySettings
        from mcp.types import CallToolResult, TextContent, ToolAnnotations
    except ImportError:
        return gateway

    from pydantic import ValidationError
    from app.mcp.auth import build_auth_app, current_principal
    if specs is None or operations_factory is None:
        from app.agent.operations import AgentOperations, TOOL_SPECS
        specs = TOOL_SPECS if specs is None else specs
        operations_factory = AgentOperations if operations_factory is None else operations_factory
    specs = dict(specs)

    def failure(code):
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=code)])

    async def validate_call(ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        if not gateway._admitting or not gateway.enabled():
            return failure("mcp_unavailable")
        if gateway.active_calls >= gateway.options.max_active_calls:
            return failure("busy")
        params = ctx.params or {}
        name = params.get("name") if isinstance(params, dict) else None
        spec = specs.get(name) if isinstance(name, str) else None
        if spec is None:
            return failure("unknown_operation")
        try:
            arguments = params.get("arguments", {})
            _bounded_arguments(arguments, gateway.options)
            spec.args_schema.model_validate(arguments, strict=True)
        except (ValidationError, ValueError, TypeError, RecursionError):
            return failure("invalid_arguments")
        gateway.active_calls += 1
        try:
            return await call_next(ctx)
        finally:
            gateway.active_calls -= 1

    def make_invoker(spec):
        async def invoke(**arguments):
            try:
                if not gateway._admitting or not gateway.enabled():
                    raise ToolError("mcp_unavailable")
                service = service_factory()
                principal = current_principal(service)
                if spec.required_scope not in principal.scopes:
                    raise ToolError("scope_denied")
                # Validation here also covers direct invocation outside middleware.
                validated = spec.args_schema.model_validate(arguments, strict=True)
                return await operations_factory(service, principal).execute(
                    spec.name, validated.model_dump(mode="python"))
            except ToolError as exc:
                code = exc.args[0] if exc.args else None
                safe = ("mcp_unavailable", "authentication_required", "scope_denied")
                raise ToolError(code if isinstance(code, str) and code in safe else "operation_failed") from None
            except ValidationError:
                raise ToolError("invalid_arguments") from None
            except Exception as exc:
                # Only fixed codes cross the SDK boundary, even for unexpected errors.
                code = {401: "authentication_required", 403: "scope_denied", 404: "not_found",
                        409: "operation_conflict", 422: "invalid_arguments", 503: "operation_unavailable"}.get(
                            getattr(exc, "status_code", None), "operation_failed")
                raise ToolError(code) from None

        parameters = []
        annotations = {"return": dict[str, Any]}
        for name, model_field in spec.args_schema.model_fields.items():
            annotation = model_field.rebuild_annotation()
            annotations[name] = annotation
            default = inspect.Parameter.empty if model_field.is_required() else model_field.get_default(call_default_factory=True)
            parameters.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY,
                                                annotation=annotation, default=default))
        invoke.__name__ = spec.name
        invoke.__annotations__ = annotations
        invoke.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, Any])
        return invoke

    tools = []
    for spec in specs.values():
        tool = Tool.from_function(
            make_invoker(spec), name=spec.name, description=spec.description,
            annotations=ToolAnnotations(read_only_hint=spec.read_only,
                                        destructive_hint=not spec.read_only,
                                        open_world_hint=spec.external_effect),
        )
        # Public SDK Tool descriptor preserves the canonical schema verbatim.
        tool.parameters = spec.args_schema.model_json_schema()
        tools.append(tool)
    server = MCPServer("Lorechat", version="1", tools=tools, middleware=[validate_call])
    gateway.server = server
    child = server.streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True,
        max_request_body_size=gateway.options.max_request_body_size,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(gateway.options.allowed_hosts),
            allowed_origins=list(gateway.options.allowed_origins)),
    )
    gateway._child = build_auth_app(child, service_factory)
    return gateway
