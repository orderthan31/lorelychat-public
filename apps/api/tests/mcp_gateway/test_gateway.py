import asyncio
import builtins
import json
from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from app.agent.contracts import AgentError, Principal
from app.agent.operations import TOOL_SPECS
from app.mcp import GatewayOptions, build_mcp_gateway

TOKEN = "synthetic-test-token-not-a-real-key"
SENTINEL = "PRIVATE_SENTINEL_MUST_NOT_LEAK"
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


class Service:
    def __init__(self):
        self.enabled = True
        self.mcp_enabled = True
        self.tokens = {TOKEN: Principal("test-client", frozenset({"lore:read", "lore:draft"}))}
        self.calls = []
        self.failure = None
        self.block = None

    def principal_for_token(self, token):
        return self.tokens.get(token)


class Operations:
    def __init__(self, service, principal):
        self.service, self.principal = service, principal

    async def execute(self, name, arguments):
        self.service.calls.append((self.principal.id, name, arguments))
        if self.service.failure:
            raise self.service.failure
        if self.service.block:
            await self.service.block.wait()
        return {"name": name, "arguments": arguments, "principal": self.principal.id}


def setup(options=None):
    service = Service()
    gateway = build_mcp_gateway(lambda: service, options, operations_factory=Operations,
                                enabled_check=lambda s: s.enabled and s.mcp_enabled)
    return service, gateway


def client(gateway):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=gateway.asgi_app),
                             base_url="http://localhost", headers=HEADERS)


async def rpc(http, method, params=None, token=TOKEN, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method,
                                         "params": params or {}}, headers=headers, **kwargs)


async def call(http, name="lore_search", arguments=None, **kwargs):
    return await rpc(http, "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)


def test_real_http_initialize_list_call_and_flat_schema():
    async def run():
        service, gateway = setup()
        async with gateway.lifespan(), client(gateway) as http:
            response = await rpc(http, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                       "clientInfo": {"name": "test", "version": "1"}})
            assert response.status_code == 200, response.text
            assert response.json()["result"]["serverInfo"]["name"] == "Lorechat"
            listed = await rpc(http, "tools/list")
            tools = listed.json()["result"]["tools"]
            assert {tool["name"] for tool in tools} == set(TOOL_SPECS)
            assert len(tools) == 17
            assert 'lore_image_context' in {tool['name'] for tool in tools}
            for tool in tools:
                assert tool["inputSchema"] == TOOL_SPECS[tool["name"]].args_schema.model_json_schema()
            search = next(tool for tool in tools if tool["name"] == "lore_search")
            assert set(search["inputSchema"]["properties"]) == {"query", "kind", "limit"}
            assert search["inputSchema"]["properties"]["limit"]["maximum"] == 20
            response = await call(http, arguments={"query": "hello", "limit": 2})
            assert response.status_code == 200, response.text
            assert response.json()["result"]["structuredContent"]["arguments"]["query"] == "hello"
            assert service.calls[-1][0] == "test-client"
            assert gateway.active_calls == 0
    asyncio.run(run())


def test_sdk_in_memory_client_roundtrip_and_no_anonymous_tools():
    async def run():
        service, gateway = setup()
        async with gateway.lifespan():
            async with Client(gateway.server) as sdk:
                listed = await sdk.list_tools()
                assert {tool.name for tool in listed.tools} == set(TOOL_SPECS)
                denied = await sdk.call_tool("lore_capabilities", {})
                assert denied.is_error
            context = auth_context_var.set(AuthenticatedUser(AccessToken(
                token=TOKEN, client_id="test-client", scopes=["lore:read"])))
            try:
                async with Client(gateway.server) as sdk:
                    result = await sdk.call_tool("lore_search", {"query": "memory"})
                    assert not result.is_error
                    assert result.structured_content["arguments"]["query"] == "memory"
            finally:
                auth_context_var.reset(context)
    asyncio.run(run())


def test_http_missing_bad_revoked_scope_and_connection_identity():
    async def run():
        service, gateway = setup()
        async with gateway.lifespan(), client(gateway) as http:
            for token in (None, "bad-token"):
                response = await call(http, token=token)
                assert response.status_code == 401
                assert "resource_metadata" not in response.headers.get("www-authenticate", "")
            service.tokens["draft-only"] = Principal("draft", frozenset({"lore:draft"}))
            assert (await call(http, token="draft-only")).status_code == 403
            assert not service.calls
            assert (await call(http)).json()["result"]["isError"] is False
            service.tokens["other"] = Principal("second", frozenset({"lore:read"}))
            assert (await call(http, token="other")).json()["result"]["structuredContent"]["principal"] == "second"
            denied = await call(http, "lore_commit_draft", {"draft_id": "d", "expected_version": 1,
                                "preview_hash": "a" * 64, "request_id": "r"})
            assert denied.json()["result"]["isError"]
            del service.tokens[TOKEN]
            assert (await call(http)).status_code == 401
    asyncio.run(run())


def test_raw_unknown_type_and_depth_arguments_reject_before_sdk(caplog):
    async def run():
        service, gateway = setup()
        async with gateway.lifespan(), client(gateway) as http:
            for args in ({"client_id": SENTINEL}, {"limit": "2"}, {"limit": 21}, {"query": SENTINEL * 50}):
                response = await call(http, arguments=args)
                assert response.json()["result"]["isError"]
                assert SENTINEL not in response.text
            deep = {}
            for _ in range(20):
                deep = {"nested": deep}
            response = await call(http, "lore_stage_character", {"fields": deep})
            assert response.json()["result"]["isError"]
            assert service.calls == []
        assert SENTINEL not in caplog.text
    asyncio.run(run())


def test_expected_and_unexpected_errors_are_sanitized(caplog):
    from mcp.server.mcpserver.exceptions import ToolError
    async def run():
        service, gateway = setup()
        async with gateway.lifespan(), client(gateway) as http:
            rejected = await call(http, token=SENTINEL)
            assert rejected.status_code == 401
            assert SENTINEL not in rejected.text
            for error in (AgentError(SENTINEL, 409), RuntimeError(SENTINEL), ToolError(SENTINEL)):
                service.failure = error
                response = await call(http)
                assert response.json()["result"]["isError"]
                assert SENTINEL not in response.text
        assert SENTINEL not in caplog.text
        assert TOKEN not in caplog.text
    asyncio.run(run())


def test_host_origin_declared_and_chunked_body_limits():
    async def run():
        _, gateway = setup(GatewayOptions(max_request_body_size=512))
        async with gateway.lifespan(), client(gateway) as http:
            assert (await call(http, headers={"Host": "attacker.invalid"})).status_code == 421
            assert (await call(http, headers={"Origin": "https://attacker.invalid"})).status_code == 403
            assert (await call(http, headers={"Origin": "http://localhost"})).status_code == 200
            assert (await call(http, headers={"Origin": "http://localhost:5174"})).status_code == 403
            headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
            assert (await http.post("/mcp", content=b"x" * 513, headers=headers)).status_code == 413
            async def chunks():
                yield b"x" * 300
                yield b"x" * 300
            assert (await http.post("/mcp", content=chunks(), headers=headers)).status_code == 413
    asyncio.run(run())


def test_feature_gate_lifespan_stop_and_existing_routes():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount

    async def run():
        service, gateway = setup()
        async def old_api(request):
            return JSONResponse({"existing": True})
        parent = Starlette(routes=[Route("/health", old_api), Route("/uploads/example", old_api),
                                   Mount("/", gateway.asgi_app)])
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=parent),
                                      base_url="http://localhost", headers=HEADERS) as http:
            assert (await call(http)).status_code == 503
            async with gateway.lifespan():
                for path in ("/health", "/uploads/example"):
                    assert (await http.get(path)).json() == {"existing": True}
                for path in ("/", "/mcp/mcp", "/.well-known/oauth-protected-resource", "/mcp/"):
                    assert (await http.get(path)).status_code == 404
                for flag in ("enabled", "mcp_enabled"):
                    setattr(service, flag, False)
                    assert (await call(http)).status_code == 503
                    setattr(service, flag, True)
                    assert (await call(http)).status_code == 200
                gateway.stop_admission()
                assert (await call(http)).status_code == 503
            assert not gateway._started
            with pytest.raises(RuntimeError, match="only start once"):
                async with gateway.lifespan():
                    pass
    asyncio.run(run())


def test_concurrency_and_drain_admission():
    async def run():
        service, gateway = setup(GatewayOptions(max_active_calls=1))
        service.block = asyncio.Event()
        async with gateway.lifespan(), client(gateway) as http:
            pending = asyncio.create_task(call(http))
            for _ in range(100):
                if gateway.active_calls:
                    break
                await asyncio.sleep(0.001)
            assert gateway.active_calls == 1
            denied = await call(http)
            assert denied.json()["result"]["content"][0]["text"] == "busy"
            gateway.stop_admission()
            assert (await call(http)).status_code == 503
            service.block.set()
            assert (await pending).json()["result"]["isError"] is False
            assert gateway.active_calls == 0
    asyncio.run(run())


def test_optional_sdk_missing_is_closed_and_lazy(monkeypatch):
    real_import = builtins.__import__
    touched = []
    def no_sdk(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            touched.append(name)
            raise ModuleNotFoundError("optional SDK absent")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_sdk)
    gateway = build_mcp_gateway(lambda: pytest.fail("unavailable gateway must not read services"))
    assert touched
    assert gateway.server is None
    async def run():
        async with gateway.lifespan(), client(gateway) as http:
            assert (await call(http)).status_code == 503
    asyncio.run(run())


def test_real_core_registry_and_database_auth(tmp_path):
    from sqlmodel import SQLModel, create_engine
    from app.agent.service import AgentService
    from app.agent.schema import initialize_agent_schema
    engine = create_engine("sqlite:///" + str(tmp_path / "agent.db"))
    SQLModel.metadata.create_all(engine)
    initialize_agent_schema(engine)
    service = AgentService(engine)
    service.update_settings({"enabled": True, "mcp_enabled": True})
    registration = service.create_client("synthetic-client", ["lore:read", "lore:draft", "lore:commit"])
    gateway = build_mcp_gateway(lambda: AgentService(engine))
    async def run():
        async with gateway.lifespan(), client(gateway) as http:
            result = await call(http, "lore_capabilities", token=registration["token"])
            assert result.status_code == 200, result.text
            assert not result.json()["result"]["isError"]
            assert set(result.json()["result"]["structuredContent"]["tools"]) == set(TOOL_SPECS)
            staged = await call(http, "lore_stage_character", {"fields": {"name": "Synthetic", "persona": "Fixture"}},
                                token=registration["token"])
            assert not staged.json()["result"]["isError"]
            draft = staged.json()["result"]["structuredContent"]
            commit_args = {"draft_id": draft["id"], "expected_version": draft["version"],
                           "preview_hash": draft["preview_hash"], "request_id": "mcp-fixture-commit"}
            denied = await call(http, "lore_commit_draft", commit_args, token=registration["token"])
            assert denied.json()["result"]["isError"]
            requested = await call(http, "lore_request_approval", {"draft_id": draft["id"]}, token=registration["token"])
            assert requested.json()["result"]["structuredContent"]["approval_required"]
            # Owner decision is deliberately outside MCP, never a model-facing tool.
            service.approve_draft(Principal("owner", frozenset(), True), draft["id"], draft["version"], draft["preview_hash"])
            committed = await call(http, "lore_commit_draft", commit_args, token=registration["token"])
            assert not committed.json()["result"]["isError"]
            receipt = committed.json()["result"]["structuredContent"]
            reread = await call(http, "lore_get_operation", {"request_id": "mcp-fixture-commit"}, token=registration["token"])
            assert reread.json()["result"]["structuredContent"] == receipt
            assert receipt["status"] == "completed"
            service.revoke_client(registration["client"]["id"])
            assert (await call(http, token=registration["token"])).status_code == 401
            service.update_settings({"enabled": False, "mcp_enabled": True})
            assert (await call(http, token=registration["token"])).status_code == 503
    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_real_sdk_http_client_over_asgi():
    from mcp.client.streamable_http import streamable_http_client

    async def run():
        service, gateway = setup()
        async with gateway.lifespan(), client(gateway) as http:
            http.headers["Authorization"] = "Bearer " + TOKEN
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with Client(transport) as sdk:
                listed = await sdk.list_tools()
                assert {tool.name for tool in listed.tools} == set(TOOL_SPECS)
                result = await sdk.call_tool("lore_search", {"query": "sdk-http"})
                assert not result.is_error
                assert result.structured_content["arguments"]["query"] == "sdk-http"
                rejected = await sdk.call_tool("lore_search", {"client_id": SENTINEL})
                assert rejected.is_error
                assert SENTINEL not in rejected.model_dump_json()
    asyncio.run(run())


def test_relookup_revocation_and_feature_change_before_tool_dispatch():
    async def run():
        for change in ("revoke", "disable"):
            service, gateway = setup()
            original = service.principal_for_token
            lookups = 0
            def lookup(token):
                nonlocal lookups
                lookups += 1
                principal = original(token)
                if lookups == 1:
                    if change == "revoke":
                        service.tokens.clear()
                    else:
                        service.mcp_enabled = False
                return principal
            service.principal_for_token = lookup
            async with gateway.lifespan(), client(gateway) as http:
                response = await call(http)
                assert response.json()["result"]["isError"]
                assert service.calls == []
    asyncio.run(run())


def test_allowlist_options_reject_wildcards(monkeypatch):
    monkeypatch.setenv("LORECHAT_MCP_ALLOWED_HOSTS", "example.test:443")
    monkeypatch.setenv("LORECHAT_MCP_ALLOWED_ORIGINS", "https://example.test")
    assert GatewayOptions().allowed_hosts == ("example.test:443",)
    with pytest.raises(ValueError):
        GatewayOptions(allowed_origins=("*",))
