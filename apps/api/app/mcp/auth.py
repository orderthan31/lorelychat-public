"""Manual bearer-token authentication, without OAuth discovery or an issuer."""


def build_auth_app(app, service_factory):
    # All SDK imports stay behind the optional gateway factory.
    from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
    from mcp.server.auth.provider import AccessToken
    from starlette.middleware.authentication import AuthenticationMiddleware

    class StoredTokenVerifier:
        async def verify_token(self, token):
            try:
                principal = service_factory().principal_for_token(token)
                if principal is None or principal.is_owner:
                    return None
                return AccessToken(token=token, client_id=principal.id, scopes=sorted(principal.scopes))
            except Exception:
                # Neither token representations nor storage errors are diagnostics.
                return None

    return AuthenticationMiddleware(
        RequireAuthMiddleware(AuthContextMiddleware(app), required_scopes=["lore:read"]),
        backend=BearerAuthBackend(StoredTokenVerifier()),
    )


def current_principal(service):
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.mcpserver.exceptions import ToolError

    token = get_access_token()
    if token is None:
        raise ToolError("authentication_required")
    principal = service.principal_for_token(token.token)
    if principal is None or principal.is_owner or principal.id != token.client_id:
        raise ToolError("authentication_required")
    if "lore:read" not in principal.scopes:
        raise ToolError("scope_denied")
    return principal
