"""
Generic API-key connector — for providers keyed by a simple API key/token
rather than OAuth (Moz, Keywords Everywhere). Keys are stored only in the
visitor's own session (server memory tied to their session cookie),
never on disk, never shared between visitors.

Each provider can optionally implement `fetch_keyword_data()` for a real
live call. If it doesn't, the key is still stored and marked "connected" —
useful as a starting point, but the panel will say no live call is wired
up yet rather than pretending to show real data.
"""
import requests


def keywordseverywhere_fetch(api_key: str, keyword: str) -> dict:
    """Real, working call to the Keywords Everywhere API."""
    resp = requests.post(
        "https://api.keywordseverywhere.com/v1/get_keyword_data",
        headers={"Authorization": f"Bearer {api_key}"},
        data={"country": "us", "currency": "USD", "dataSource": "gkp", "kw[]": keyword},
        timeout=15,
    )
    if resp.status_code == 401:
        raise RuntimeError("Keywords Everywhere rejected this API key (401 Unauthorized).")
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("data", [])
    if not rows:
        return {"keyword": keyword, "found": False, "credits_used": data.get("credits_used"), "credits_remaining": data.get("credits_remaining")}
    row = rows[0]
    return {
        "keyword": row.get("keyword", keyword),
        "found": True,
        "volume": row.get("vol"),
        "cpc": (row.get("cpc") or {}).get("value"),
        "competition": row.get("competition"),
        "trend": row.get("trend"),
        "credits_used": data.get("credits_used"),
        "credits_remaining": data.get("credits_remaining"),
    }


def moz_fetch(api_key: dict, keyword: str) -> dict:
    """Moz's API requires per-tier request signing/scoping that varies by
    subscription. The key is safely stored (see api key routes in app.py),
    but the live call isn't wired up yet — raise a clear, honest message
    instead of returning fake data."""
    raise RuntimeError(
        "Moz's key is connected, but the live API call isn't implemented yet "
        "(Moz's request format depends on your specific API tier). "
        "See README for where to add it."
    )


FETCHERS = {
    "keywordseverywhere": keywordseverywhere_fetch,
    "moz": moz_fetch,
}


def fetch_keyword_data(provider_id: str, credentials: dict, keyword: str) -> dict:
    fn = FETCHERS.get(provider_id)
    if not fn:
        raise RuntimeError(f"No keyword lookup implemented for '{provider_id}' yet.")
    if provider_id == "moz":
        return fn(credentials, keyword)
    # single-field providers (api_key) — pass the first credential value
    key = next(iter(credentials.values()), None)
    return fn(key, keyword)
