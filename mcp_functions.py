"""
Maps abstract SEO functions (backlinks, domain overview, rank tracking,
competitor gap, keyword difficulty, SERP data) onto whatever real MCP
tools a connected provider actually exposes — discovered live via
list_tools(), never hardcoded to one provider's exact tool names.

This is what lets "Ahrefs" and "Semrush" and "Ubersuggest" all plug into
the same audit report without provider-specific code: each one just
needs its real tool names/descriptions to contain the right words.
"""
import re
from urllib.parse import urlparse

import mcp_oauth

# category -> substrings that would appear in a matching tool's name/description
CATEGORY_HINTS = {
    "keyword_data": ["keyword_overview", "keyword overview", "keyword_research", "keyword research", "match_keywords"],
    "keyword_difficulty": ["difficulty", " kd", "kd_", "seo_difficulty"],
    "backlinks": ["backlink", "referring domain", "link_intersect", "linking"],
    "domain_overview": ["domain_overview", "domain overview", "site_overview", "traffic_overview"],
    "rank_tracking": ["rank", "position", "project_position", "serp_analysis", "rankings"],
    "competitor_gap": ["competitor", "gap", "content_gap", "keyword_gap"],
    "serp": ["serp", "search results", "google_search", "ranked_keywords"],
}


def find_tool(tools: list[dict], category: str) -> dict | None:
    hints = CATEGORY_HINTS.get(category, [])
    for t in tools:
        haystack = f"{t['name']} {t.get('description', '')}".lower()
        if any(h in haystack for h in hints):
            return t
    return None


def _guess_arguments(tool: dict, page_url: str, keyword: str | None) -> dict:
    """Best-effort argument builder from the tool's real input schema.
    Different providers name their parameters differently (keyword vs kw,
    domain vs site, url vs page) — we match by substring in the property
    name rather than assuming one exact name."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    domain = urlparse(page_url).netloc

    args = {}
    for prop_name in (required or list(props.keys())):
        lname = prop_name.lower()
        if "keyword" in lname or lname in ("kw", "query", "term"):
            if keyword:
                args[prop_name] = keyword
        elif "domain" in lname or lname in ("site", "target"):
            args[prop_name] = domain
        elif "url" in lname or "page" in lname:
            args[prop_name] = page_url
        elif "location" in lname or "country" in lname or "loc_id" in lname:
            continue  # let the tool use its own default rather than guessing wrong
        elif "limit" in lnamme or "max" in lname:
            args[prop_name] = 10
    return args


async def run_category(psess: "mcp_oauth.McpProviderSession", category: str, page_url: str, keyword: str | None) -> dict:
    tool = find_tool(psess.tools, category)
    if not tool:
        return {"available": False}
    args = _guess_arguments(tool, page_url, keyword)
    try:
        result = await mcp_oauth.call_tool(psess, tool["name"], args)
        return {"available": True, "tool": tool["name"], "arguments": args, "result": result}
    except Exception as e:
        return {"available": True, "tool": tool["name"], "arguments": args, "error": str(e)}


def extract_difficulty_hint(result: dict) -> float | None:
    """Best-effort: pull a difficulty/competition-shaped number out of a
    tool's raw content, since exact field names vary by provider."""
    text = str(result.get("content", ""))
    for key in ("difficulty", "kd", "seo_difficulty", "competition"):
        m = re.search(rf'"{key}"\s*:\s*([0-9.]+)', text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None
