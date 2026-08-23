"""
SEO Audit Checker — minimal, real, deployable audit tool.
Run: pip install -r requirements.txt && uvicorn app:app --reload
Then open http://127.0.0.1:8000
"""
import re
import uuid
import asyncio
import collections
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import gsc_auth
import mcp_oauth
import mcp_functions
import serp_scraper
import api_key_auth
import providers as providers_module

app = FastAPI(title="SEO Audit Checker")
app.add_middleware(SessionMiddleware, secret_key=uuid.uuid4().hex, same_site="lax")

# Per-session state that can't live in a signed cookie (OAuth tokens, API
# keys, live connections). Keyed by a random id we put in the session cookie.
# mcp_sessions:    sid -> {provider_id -> McpProviderSession}
# apikey_sessions: sid -> {provider_id -> {field_name: value}}
_MCP_SESSIONS: dict[str, dict[str, mcp_oauth.McpProviderSession]] = {}
_APIKEY_SESSIONS: dict[str, dict[str, dict]] = {}


def get_sid(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        request.session["sid"] = sid
    return sid


def get_mcp_session(request: Request, provider_id: str) -> mcp_oauth.McpProviderSession:
    sid = get_sid(request)
    bucket = _MCP_SESSIONS.setdefault(sid, {})
    if provider_id not in bucket:
        provider = providers_module.PROVIDERS[provider_id]
        bucket[provider_id] = mcp_oauth.McpProviderSession(provider_id, provider["endpoint"])
    return bucket[provider_id]

STOPWORDS = set("""
a an the and or but if of at by for with about against between into through
during before after above below to from up down in out on off over under
again further then once is are was were be been being have has had do does
did doing this that these those it its as not no you your we our they their
""".split())


def fetch(url: str) -> tuple[str, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOAuditChecker/1.0)"}
    r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
    r.raise_for_status()
    return r.text, {"status_code": r.status_code, "final_url": r.url}


def word_freq(text: str) -> collections.Counter:
    words = re.findall(r"[a-zA-Z']{3,}", text.lower())
    return collections.Counter(w for w in words if w not in STOPWORDS)


def analyze(url: str) -> dict:
    html, meta = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(meta["final_url"])
    domain = parsed.netloc

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag.get("content", "").strip() if desc_tag else ""

    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    canonical = canonical_tag.get("href", "").strip() if canonical_tag else ""

    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    has_viewport = bool(viewport_tag)

    robots_tag = soup.find("meta", attrs={"name": "robots"})
    robots_content = robots_tag.get("content", "").lower() if robots_tag else ""
    is_noindex = "noindex" in robots_content

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body_text = soup.get_text(separator=" ", strip=True)
    word_count = len(body_text.split())

    images = soup.find_all("img")
    missing_alt = sum(1 for img in images if not img.get("alt", "").strip())

    internal, external = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        full = urljoin(meta["final_url"], href)
        host = urlparse(full).netloc
        (internal if host == domain else external).append(full)

    has_schema = bool(soup.find("script", attrs={"type": "application/ld+json"}))
    keywords = word_freq(body_text).most_common(15)

    # ---- Checklist: each item is (label, category, passed, detail, weight) ----
    checks = []

    def add(label, category, passed, detail, weight=1, warn=False):
        checks.append({
            "label": label, "category": category,
            "status": "warn" if warn else ("pass" if passed else "fail"),
            "detail": detail, "weight": weight,
        })

    add("Title tag present", "Meta & Titles", bool(title), f'"{title}"' if title else "No <title> tag found", 2)
    add("Title length (30–60 chars)", "Meta & Titles",
        30 <= len(title) <= 60, f"{len(title)} characters", 1,
        warn=bool(title) and not (30 <= len(title) <= 60))
    add("Meta description present", "Meta & Titles", bool(description),
        f'"{description[:100]}{"…" if len(description) > 100 else ""}"' if description else "No meta description found", 2)
    add("Meta description length (70–160 chars)", "Meta & Titles",
        70 <= len(description) <= 160, f"{len(description)} characters", 1,
        warn=bool(description) and not (70 <= len(description) <= 160))
    add("Canonical tag present", "Meta & Titles", bool(canonical),
        canonical if canonical else "No canonical tag found", 1)
    add("Not blocked by noindex", "Meta & Titles", not is_noindex,
        "Page is marked noindex" if is_noindex else "Indexable", 3)
    add("Mobile viewport tag present", "Meta & Titles", has_viewport,
        "Missing <meta name=viewport>" if not has_viewport else "Present", 1)

    add("Exactly one H1", "Structure", len(h1s) == 1,
        f"{len(h1s)} H1 tag(s) found" + (f': "{h1s[0]}"' if len(h1s) == 1 else ""), 2,
        warn=len(h1s) > 1)
    add("Has H2 subheadings", "Structure", len(h2s) >= 2, f"{len(h2s)} H2 tag(s) found", 1)
    add("Structured data (JSON-LD) present", "Structure", has_schema,
        "No JSON-LD schema found" if not has_schema else "Found", 1)

    add("Sufficient content length (300+ words)", "Content", word_count >= 300,
        f"{word_count} words", 2, warn=150 <= word_count < 300)
    add("Images have ALT text", "Images & Links",
        missing_alt == 0, f"{missing_alt} of {len(images)} images missing ALT text" if images else "No images found", 1,
        warn=0 < missing_alt <= max(1, len(images) // 4))
    add("Has internal links", "Images & Links", len(internal) >= 3, f"{len(internal)} internal links found", 1)
    add("Has external links", "Images & Links", len(external) >= 1, f"{len(external)} external links found", 1)

    max_score = sum(c["weight"] for c in checks)
    earned = sum(c["weight"] for c in checks if c["status"] == "pass") + \
        sum(c["weight"] * 0.5 for c in checks if c["status"] == "warn")
    score_pct = round((earned / max_score) * 100) if max_score else 0

    if score_pct >= 90:
        grade = "A+"
    elif score_pct >= 80:
        grade = "A"
    elif score_pct >= 70:
        grade = "B"
    elif score_pct >= 60:
        grade = "C"
    elif score_pct >= 45:
        grade = "D"
    else:
        grade = "F"

    return {
        "url": meta["final_url"],
        "status_code": meta["status_code"],
        "grade": grade,
        "score": score_pct,
        "title": title,
        "description": description,
        "word_count": word_count,
        "internal_links": len(internal),
        "external_links": len(external),
        "images": len(images),
        "missing_alt": missing_alt,
        "checks": checks,
        "keywords": [{"word": w, "count": c} for w, c in keywords],
    }


@app.get("/api/audit")
def audit(url: str = Query(..., description="Page URL to audit")):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        result = analyze(url)
        return JSONResponse(result)
    except requests.exceptions.RequestException as e:
        return JSONResponse({"error": f"Could not fetch that URL: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Audit failed: {e}"}, status_code=500)


# ---------------------------------------------------------------------------
# Google Search Console — per-visitor connect
# ---------------------------------------------------------------------------

@app.get("/api/google/status")
def google_status(request: Request):
    creds = request.session.get("google_creds")
    return {"configured": gsc_auth.is_configured(), "connected": bool(creds)}


@app.get("/auth/google/start")
def google_start():
    try:
        url = gsc_auth.start_auth()
        return RedirectResponse(url)
    except RuntimeError as e:
        return RedirectResponse(f"/?google_error={str(e)}")


@app.get("/auth/google/callback", name="google_callback")
def google_callback(request: Request):
    try:
        creds_json = gsc_auth.finish_auth(str(request.url), request.query_params.get("state"))
        request.session["google_creds"] = creds_json
    except Exception as e:
        return RedirectResponse(f"/?google_error={str(e)}")
    return RedirectResponse("/?google_connected=1")


@app.post("/auth/google/disconnect")
def google_disconnect(request: Request):
    request.session.pop("google_creds", None)
    return {"ok": True}


@app.post("/admin/google-config")
async def admin_google_config(request: Request):
    """Site owner pastes their Google OAuth Client ID/Secret here instead of
    editing host environment variables. Applies immediately, no redeploy."""
    body = await request.json()
    client_id = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        return JSONResponse({"error": "Both Client ID and Client Secret are required."}, status_code=400)
    gsc_auth.set_runtime_credentials(client_id, client_secret)
    return {"ok": True}


@app.get("/api/gsc/performance")
def gsc_performance(request: Request, url: str = Query(...)):
    creds = request.session.get("google_creds")
    if not creds:
        return JSONResponse({"error": "Google Search Console isn't connected."}, status_code=401)
    try:
        return JSONResponse(gsc_auth.page_performance(creds, url))
    except Exception as e:
        return JSONResponse({"error": f"GSC lookup failed: {e}"}, status_code=500)


# ---------------------------------------------------------------------------
# Connector registry — the frontend renders one card per entry here
# ---------------------------------------------------------------------------

@app.get("/api/providers")
def api_providers():
    return providers_module.public_provider_list()


# ---------------------------------------------------------------------------
# MCP OAuth providers (Ubersuggest, Ahrefs, Semrush, or any future one) —
# same generic routes handle all of them via the {provider_id} path param.
# ---------------------------------------------------------------------------

@app.get("/api/mcp/{provider_id}/status")
def mcp_status(provider_id: str, request: Request):
    if provider_id not in providers_module.PROVIDERS:
        return JSONResponse({"error": "Unknown provider"}, status_code=404)
    sid = get_sid(request)
    psess = _MCP_SESSIONS.get(sid, {}).get(provider_id)
    if not psess:
        return {"connected": False, "busy": False, "error": None, "tools": []}
    return {"connected": psess.connected, "busy": psess.busy, "error": psess.error, "tools": psess.tools}


@app.get("/auth/mcp/{provider_id}/start")
async def mcp_start(provider_id: str, request: Request):
    if provider_id not in providers_module.PROVIDERS:
        return RedirectResponse("/?connector_error=Unknown+provider")
    psess = get_mcp_session(request, provider_id)
    psess.pending_redirect_url = None
    psess.error = None
    psess.connected = False
    asyncio.create_task(mcp_oauth.connect(psess))
    for _ in range(100):  # wait up to ~10s for the authorize URL to be produced
        if psess.pending_redirect_url or psess.error:
            break
        await asyncio.sleep(0.1)
    if psess.error:
        return RedirectResponse(f"/?connector_error={psess.error[:300]}")
    if not psess.pending_redirect_url:
        return RedirectResponse("/?connector_error=Timed+out+starting+the+connection.")
    return RedirectResponse(psess.pending_redirect_url)


@app.get("/auth/mcp/{provider_id}/callback")
async def mcp_callback(provider_id: str, request: Request):
    psess = get_mcp_session(request, provider_id)
    qp = request.query_params
    if "error" in qp:
        psess.error = qp.get("error_description", qp.get("error"))
        return RedirectResponse(f"/?connector_error={psess.error}")
    await mcp_oauth.resolve_callback(psess, qp.get("code"), qp.get("state"), qp.get("iss"))
    for _ in range(100):  # wait for the background connect() task to finish listing tools
        if psess.connected or psess.error or not psess.busy:
            break
        await asyncio.sleep(0.1)
    label = providers_module.PROVIDERS[provider_id]["label"]
    return RedirectResponse(f"/?connector_connected={label}")


@app.post("/auth/mcp/{provider_id}/disconnect")
def mcp_disconnect(provider_id: str, request: Request):
    sid = get_sid(request)
    _MCP_SESSIONS.get(sid, {}).pop(provider_id, None)
    return {"ok": True}


@app.get("/api/mcp/{provider_id}/keywords")
async def mcp_keywords(provider_id: str, request: Request, keyword: str = Query(...)):
    sid = get_sid(request)
    psess = _MCP_SESSIONS.get(sid, {}).get(provider_id)
    if not psess or not psess.connected:
        return JSONResponse({"error": f"{provider_id} isn't connected."}, status_code=401)
    tool = next((t for t in psess.tools if "keyword" in t["name"].lower()), None)
    if not tool:
        return JSONResponse({
            "error": "No keyword-research tool found on this account.",
            "available_tools": [t["name"] for t in psess.tools],
        }, status_code=404)
    try:
        result = await mcp_oauth.call_tool(psess, tool["name"], {"keyword": keyword})
        return JSONResponse({"tool": tool["name"], "result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/mcp/{provider_id}/run_tool")
async def mcp_run_tool(provider_id: str, request: Request):
    body = await request.json()
    sid = get_sid(request)
    psess = _MCP_SESSIONS.get(sid, {}).get(provider_id)
    if not psess or not psess.connected:
        return JSONResponse({"error": f"{provider_id} isn't connected."}, status_code=401)
    try:
        result = await mcp_oauth.call_tool(psess, body.get("name"), body.get("arguments") or {})
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# API-key providers (Moz, Keywords Everywhere, or any future one) —
# key is stored only in this visitor's session, never on disk.
# ---------------------------------------------------------------------------

@app.get("/api/apikey/{provider_id}/status")
def apikey_status(provider_id: str, request: Request):
    sid = get_sid(request)
    creds = _APIKEY_SESSIONS.get(sid, {}).get(provider_id)
    return {"connected": bool(creds)}


@app.post("/auth/apikey/{provider_id}/connect")
async def apikey_connect(provider_id: str, request: Request):
    if provider_id not in providers_module.PROVIDERS:
        return JSONResponse({"error": "Unknown provider"}, status_code=404)
    body = await request.json()
    sid = get_sid(request)
    _APIKEY_SESSIONS.setdefault(sid, {})[provider_id] = body
    return {"ok": True}


@app.post("/auth/apikey/{provider_id}/disconnect")
def apikey_disconnect(provider_id: str, request: Request):
    sid = get_sid(request)
    _APIKEY_SESSIONS.get(sid, {}).pop(provider_id, None)
    return {"ok": True}


@app.get("/api/apikey/{provider_id}/keywords")
def apikey_keywords(provider_id: str, request: Request, keyword: str = Query(...)):
    sid = get_sid(request)
    creds = _APIKEY_SESSIONS.get(sid, {}).get(provider_id)
    if not creds:
        return JSONResponse({"error": f"{provider_id} isn't connected."}, status_code=401)
    try:
        result = api_key_auth.fetch_keyword_data(provider_id, creds, keyword)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/mcp/{provider_id}/functions")
async def mcp_functions_route(provider_id: str, request: Request, url: str = Query(...), keyword: str = Query(...)):
    sid = get_sid(request)
    psess = _MCP_SESSIONS.get(sid, {}).get(provider_id)
    if not psess or not psess.connected:
        return JSONResponse({"error": f"{provider_id} isn't connected."}, status_code=401)
    categories = ["keyword_data", "keyword_difficulty", "backlinks", "domain_overview",
                  "rank_tracking", "competitor_gap"]
    out = {}
    for cat in categories:
        out[cat] = await mcp_functions.run_category(psess, cat, url, keyword)
    return JSONResponse(out)


@app.get("/api/serp")
async def serp_route(request: Request, keyword: str = Query(...), url: str = Query(...)):
    sid = get_sid(request)
    provider_sessions = _MCP_SESSIONS.get(sid, {})

    serp_data = None
    serp_source = None
    scraped_fallback = False

    # 1. A dedicated SERP API (Serper), if connected — most reliable, purpose-built for this
    serper_creds = _APIKEY_SESSIONS.get(sid, {}).get("serper")
    if serper_creds and serper_creds.get("api_key"):
        try:
            results = serp_scraper.serper_fetch_serp(serper_creds["api_key"], keyword)
            serp_data = {"results": results}
            serp_source = "Serper (SERP API)"
        except Exception as e:
            serp_data = None  # fall through to the next source rather than failing outright

    # 2. A connected MCP provider's own SERP-shaped tool (Ahrefs/Semrush/etc.)
    if serp_data is None:
        for provider_id, psess in provider_sessions.items():
            if not psess.connected:
                continue
            outcome = await serp_scraper.serp_via_provider(psess, keyword, url)
            if outcome:
                serp_data = outcome
                serp_source = providers_module.PROVIDERS[provider_id]["label"]
                break

    # 3. Last resort: scrape Google directly (fragile — see README)
    if serp_data is None:
        try:
            results = serp_scraper.scrape_google_serp(keyword)
            serp_data = {"results": results}
            serp_source = "Direct scrape (best-effort)"
            scraped_fallback = True
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)

    difficulty = None
    difficulty_source = None
    for provider_id, psess in provider_sessions.items():
        if not psess.connected:
            continue
        val = await serp_scraper.keyword_difficulty_via_provider(psess, keyword, url)
        if val is not None:
            difficulty = val
            difficulty_source = providers_module.PROVIDERS[provider_id]["label"]
            break
    if difficulty is None:
        kwe_creds = _APIKEY_SESSIONS.get(sid, {}).get("keywordseverywhere")
        if kwe_creds:
            try:
                kwe_result = api_key_auth.fetch_keyword_data("keywordseverywhere", kwe_creds, keyword)
                if kwe_result.get("competition") is not None:
                    difficulty = kwe_result["competition"]
                    difficulty_source = "Keywords Everywhere (competition)"
            except Exception:
                pass

    return JSONResponse({
        "keyword": keyword,
        "serp_source": serp_source,
        "scraped_fallback": scraped_fallback,
        "results": serp_data.get("results") or serp_data.get("result", {}).get("content"),
        "difficulty": difficulty,
        "difficulty_source": difficulty_source,
    })


app.mount("/", StaticFiles(directory="static", html=True), name="static")

