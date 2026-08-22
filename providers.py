"""
Registry of connectable SEO data providers.

To add a new tool: add one entry here. Two supported kinds:

  "mcp_oauth" — the provider exposes an MCP server with OAuth Dynamic Client
                Registration (like Ubersuggest, Ahrefs, Semrush). No API key
                needed from anyone; each visitor authorizes their own account
                in their browser. Just needs an `endpoint` URL.

  "api_key"   — the provider is a plain REST API keyed by an API key/token
                (like Moz, Keywords Everywhere). Each visitor pastes their
                own key, which we store only in their session (never on disk,
                never sent anywhere except that provider's API).
"""

PROVIDERS = {
    "ubersuggest": {
        "label": "Ubersuggest",
        "kind": "mcp_oauth",
        "endpoint": "https://ubersuggest-mcp.neilpatelapi.com/mcp",
        "description": "Keyword volume, CPC, difficulty, and domain overview.",
    },
    "ahrefs": {
        "label": "Ahrefs",
        "kind": "mcp_oauth",
        "endpoint": "https://api.ahrefs.com/mcp/mcp",
        "description": "Backlinks, referring domains, and keyword rankings.",
    },
    "semrush": {
        "label": "Semrush",
        "kind": "mcp_oauth",
        "endpoint": "https://mcp.semrush.com/claude/v1/mcp",
        "description": "Keyword research, competitor analysis, and traffic data.",
    },
    "moz": {
        "label": "Moz",
        "kind": "api_key",
        "fields": [{"name": "access_id", "label": "Access ID"}, {"name": "secret_key", "label": "Secret Key", "secret": True}],
        "description": "Domain Authority, spam score, and link metrics.",
    },
    "keywordseverywhere": {
        "label": "Keywords Everywhere",
        "kind": "api_key",
        "fields": [{"name": "api_key", "label": "API Key", "secret": True}],
        "description": "Search volume, CPC, and competition for any keyword.",
    },
    "serper": {
        "label": "Serper (SERP API)",
        "kind": "api_key",
        "role": "serp",
        "fields": [{"name": "api_key", "label": "API Key", "secret": True}],
        "description": "Reliable Google SERP results — no scraping, an actual search API.",
    },
}


def public_provider_list():
    """What the frontend needs to render connector cards — never includes secrets."""
    return [
        {"id": pid, "label": p["label"], "kind": p["kind"], "description": p.get("description", ""),
         "fields": p.get("fields", []), "role": p.get("role", "keywords")}
        for pid, p in PROVIDERS.items()
    ]
