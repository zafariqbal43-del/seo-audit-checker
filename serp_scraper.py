"""
Top-of-SERP fetcher for a keyword, plus keyword difficulty.

Two paths, tried in this order:
  1. If any connected MCP provider (Ahrefs/Semrush/Ubersuggest/etc.) exposes
     a SERP-shaped tool, use that — it's their own infrastructure, reliable,
     and won't get blocked.
  2. Otherwise, fall back to scraping google.com/search directly.

Path 2 is inherently fragile: Google actively detects and blocks scraping
(CAPTCHAs, IP blocks, markup changes breaking selectors). This is a
best-effort fallback for when no SERP-capable tool is connected, not a
production-grade solution — see README.
"""
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote

import mcp_functions


def serper_fetch_serp(api_key: str, keyword: str, num_results: int = 10) -> list[dict]:
    """Real call to Serper.dev — a dedicated Google SERP API, not scraping.
    https://serper.dev — free tier available, simple single-endpoint API."""
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": keyword, "num": num_results},
        timeout=15,
    )
    if resp.status_code == 401:
        raise RuntimeError("Serper rejected this API key (401 Unauthorized).")
    if resp.status_code == 403:
        raise RuntimeError("Serper API key is valid but forbidden for this request (403) — check your plan/quota.")
    resp.raise_for_status()
    data = resp.json()
    results = []
    for r in data.get("organic", [])[:num_results]:
        link = r.get("link", "")
        results.append({
            "position": r.get("position", len(results) + 1),
            "title": r.get("title", link),
            "url": link,
            "domain": urlparse(link).netloc.removeprefix("www."),
        })
    if not results:
        raise RuntimeError("Serper returned no organic results for this keyword.")
    return results


def scrape_google_serp(keyword: str, num_results: int = 10) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(
        "https://www.google.com/search",
        params={"q": keyword, "num": num_results, "hl": "en"},
        headers=headers, timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Google returned HTTP {resp.status_code} (likely rate-limited or blocking this request).")
    if "unusual traffic" in resp.text.lower() or "captcha" in resp.text.lower():
        raise RuntimeError("Google is showing a CAPTCHA/bot-check page instead of results. "
                            "Direct scraping is unreliable for sustained use — connect a provider "
                            "with a real SERP tool instead (Ahrefs, Semrush) for reliable results.")

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    seen_urls = set()

    for h3 in soup.find_all("h3"):
        a = h3.find_parent("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = unquote(qs.get("q", [href])[0])
        if not href.startswith("http") or "google.com" in href:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        results.append({
            "position": len(results) + 1,
            "title": h3.get_text(strip=True),
            "url": href,
            "domain": urlparse(href).netloc.removeprefix("www."),
        })
        if len(results) >= num_results:
            break

    if not results:
        raise RuntimeError("Could not parse any organic results from Google's response — "
                            "the page markup may have changed, or this request was blocked silently.")
    return results


async def serp_via_provider(psess, keyword: str, page_url: str) -> dict | None:
    outcome = await mcp_functions.run_category(psess, "serp", page_url, keyword)
    if not outcome.get("available") or outcome.get("error"):
        return None
    return outcome


async def keyword_difficulty_via_provider(psess, keyword: str, page_url: str) -> float | None:
    outcome = await mcp_functions.run_category(psess, "keyword_difficulty", page_url, keyword)
    if outcome.get("available") and not outcome.get("error"):
        val = mcp_functions.extract_difficulty_hint(outcome["result"])
        if val is not None:
            return val
    # fall back to the keyword_data category, which often has a difficulty/competition field too
    outcome = await mcp_functions.run_category(psess, "keyword_data", page_url, keyword)
    if outcome.get("available") and not outcome.get("error"):
        return mcp_functions.extract_difficulty_hint(outcome["result"])
    return None
