"""
Generic MCP OAuth — works for ANY provider in providers.py with kind
"mcp_oauth" (Ubersuggest, Ahrefs, Semrush, or anything added later).

Each visitor connects THEIR OWN account for THEIR chosen provider(s).
This is the same visitor-facing pattern regardless of which tool: the
provider's `endpoint` is the only thing that changes.

Adapts the desktop-style MCP OAuth flow (OAuthClientProvider expects to
open a local browser and catch the redirect on a localhost server) into
a normal multi-user web flow:
  - redirect_handler: stashes the authorize URL so /auth/mcp/<id>/start
    can 302 the visitor's own browser there.
  - callback_handler: waits on an asyncio.Future that
    /auth/mcp/<id>/callback resolves when the visitor's browser returns.

Tokens live in memory only, per (browser session, provider) pair.
"""
import asyncio
import os

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider, AuthorizationCodeResult
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


def format_exception(exc) -> str:
    """Readable nested ExceptionGroup details instead of a useless str(exc)."""
    lines = []

    def walk(e, level=0):
        prefix = "  " * level
        lines.append(f"{prefix}{type(e).__name__}: {e}")
        children = getattr(e, "exceptions", None)
        if children:
            for child in children:
                walk(child, level + 1)
        cause = getattr(e, "__cause__", None)
        if cause and not children:
            lines.append(f"{prefix}Caused by:")
            walk(cause, level + 1)

    walk(exc)
    return "\n".join(lines)


class InMemoryTokenStorage:
    """Per (session, provider) token storage — never written to disk."""
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


class McpProviderSession:
    """One visitor's connection state for one MCP provider (e.g. this
    browser session's Ahrefs connection, separate from its Semrush one)."""
    def __init__(self, provider_id: str, endpoint: str):
        self.provider_id = provider_id
        self.endpoint = endpoint
        self.storage = InMemoryTokenStorage()
        self.pending_redirect_url: str | None = None
        self.pending_future: asyncio.Future | None = None
        self.connected = False
        self.tools: list[dict] = []
        self.error: str | None = None
        self.busy = False


def _redirect_uri(provider_id: str) -> str:
    return f"{BASE_URL.rstrip('/')}/auth/mcp/{provider_id}/callback"


async def _with_client(psess: McpProviderSession, work):
    async def redirect_handler(url: str):
        psess.pending_redirect_url = url

    async def callback_handler():
        loop = asyncio.get_running_loop()
        psess.pending_future = loop.create_future()
        return await asyncio.wait_for(psess.pending_future, timeout=300)

    oauth = OAuthClientProvider(
        server_url=psess.endpoint,
        client_metadata=OAuthClientMetadata(
            client_name="SEO Audit Checker",
            redirect_uris=[AnyUrl(_redirect_uri(psess.provider_id))],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=psess.storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    async with httpx2.AsyncClient(
        auth=oauth, follow_redirects=True, timeout=httpx2.Timeout(30.0, read=300.0)
    ) as http_client:
        transport = streamable_http_client(psess.endpoint, http_client=http_client)
        async with Client(transport) as client:
            return await work(client)


def _tool_dict(t) -> dict:
    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
    return {"name": t.name, "description": t.description or "", "inputSchema": schema}


async def connect(psess: McpProviderSession):
    """Background task: populates pending_redirect_url quickly (for /start
    to redirect to), blocks until /callback resolves it, then lists tools."""
    psess.busy = True
    psess.error = None
    try:
        async def work(client):
            result = await client.list_tools()
            psess.tools = [_tool_dict(t) for t in result.tools]
            psess.connected = True
        await _with_client(psess, work)
    except Exception as exc:
        psess.error = format_exception(exc)
    finally:
        psess.busy = False


async def resolve_callback(psess: McpProviderSession, code: str, state: str | None, iss: str | None):
    if psess.pending_future and not psess.pending_future.done():
        psess.pending_future.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))


async def call_tool(psess: McpProviderSession, name: str, arguments: dict) -> dict:
    async def work(client):
        result = await client.call_tool(name, arguments or {})
        out = []
        for item in result.content:
            if hasattr(item, "text"):
                out.append(item.text)
            else:
                try:
                    out.append(item.model_dump(mode="json"))
                except Exception:
                    out.append(str(item))
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)
        return {"isError": bool(is_error), "content": out}

    try:
        return await _with_client(psess, work)
    except Exception as exc:
        raise RuntimeError(format_exception(exc)) from exc
