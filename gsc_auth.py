"""
Google Search Console OAuth — each visitor connects THEIR OWN GSC account.

Requires the site owner to set up a Google Cloud OAuth Client (one-time):
  1. https://console.cloud.google.com/ -> create/select a project
  2. Enable the "Google Search Console API"
  3. OAuth consent screen -> configure (External is fine for testing)
  4. Credentials -> Create OAuth Client ID -> Web application
  5. Add an Authorized redirect URI:  <YOUR_BASE_URL>/auth/google/callback
  6. Set env vars: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, BASE_URL

Without those env vars set, the Google connect button will return a clear
"not configured" error instead of connecting.
"""
import os
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Flows in flight, keyed by the OAuth `state` param, so the callback can
# reconstruct the exact flow that started it.
_PENDING_FLOWS: dict[str, Flow] = {}


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _redirect_uri() -> str:
    return f"{BASE_URL.rstrip('/')}/auth/google/callback"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def start_auth() -> str:
    """Returns the URL to redirect the visitor's browser to."""
    if not is_configured():
        raise RuntimeError(
            "Google Search Console isn't configured on this server yet. "
            "The site owner needs to set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET (see gsc_auth.py docstring)."
        )
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    _PENDING_FLOWS[state] = flow
    return auth_url


def finish_auth(full_callback_url: str, state: str) -> dict:
    """Exchanges the callback for credentials. Returns a JSON-safe dict to store in the session."""
    flow = _PENDING_FLOWS.pop(state, None)
    if flow is None:
        flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())
    flow.fetch_token(authorization_response=full_callback_url)
    creds = flow.credentials
    return json_from_credentials(creds)


def json_from_credentials(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def credentials_from_json(data: dict) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )


def list_verified_sites(creds_json: dict) -> list[str]:
    creds = credentials_from_json(creds_json)
    service = build("searchconsole", "v1", credentials=creds)
    resp = service.sites().list().execute()
    return [s["siteUrl"] for s in resp.get("siteEntry", [])]


def _best_matching_site(page_url: str, sites: list[str]) -> str | None:
    from urllib.parse import urlparse
    host = urlparse(page_url).netloc
    candidates = [s for s in sites if host in s]
    if candidates:
        # Prefer an exact URL-prefix property (https://domain.com/) over sc-domain: style
        candidates.sort(key=lambda s: (not s.startswith("http"), len(s)))
        return candidates[0]
    return None


def page_performance(creds_json: dict, page_url: str, days: int = 28) -> dict:
    """Real GSC performance for one exact page URL, last `days` days ending 3 days ago."""
    import datetime
    creds = credentials_from_json(creds_json)
    service = build("searchconsole", "v1", credentials=creds)
    sites = list_verified_sites(creds_json)
    site = _best_matching_site(page_url, sites)
    if not site:
        return {"error": "No verified Search Console property matches this URL's domain.", "sites": sites}

    end = datetime.date.today() - datetime.timedelta(days=3)
    start = end - datetime.timedelta(days=days - 1)

    body_totals = {
        "startDate": str(start), "endDate": str(end),
        "dimensions": [],
        "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}],
        "rowLimit": 1,
    }
    totals_resp = service.searchanalytics().query(siteUrl=site, body=body_totals).execute()
    totals_rows = totals_resp.get("rows", [])
    totals = totals_rows[0] if totals_rows else {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

    body_queries = dict(body_totals)
    body_queries["dimensions"] = ["query"]
    body_queries["rowLimit"] = 15
    queries_resp = service.searchanalytics().query(siteUrl=site, body=body_queries).execute()
    queries = [
        {"query": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
         "ctr": round(r["ctr"] * 100, 2), "position": round(r["position"], 1)}
        for r in queries_resp.get("rows", [])
    ]

    return {
        "site": site,
        "date_range": f"{start} to {end}",
        "clicks": totals.get("clicks", 0),
        "impressions": totals.get("impressions", 0),
        "ctr": round(totals.get("ctr", 0) * 100, 2),
        "position": round(totals.get("position", 0), 1),
        "queries": queries,
    }
