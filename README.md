# SEO Audit Checker

A real, deployable on-page SEO audit tool. Paste a URL, get a graded
inspection (A+ to F) — plus visitors can connect **their own** accounts
from a growing list of SEO tools, which unlocks real backlink data,
domain overviews, rank tracking, competitor gaps, keyword difficulty, and
top-of-SERP results for the page's top keyword.

Currently connectable: **Google Search Console**, **Ubersuggest**,
**Ahrefs**, **Semrush**, **Moz**, **Keywords Everywhere**, **Serper (SERP API)**.

## Run locally

    pip install -r requirements.txt
    uvicorn app:app --reload

Then open http://127.0.0.1:8000

## What each connection unlocks

| Function | Where it comes from |
|---|---|
| Clicks / impressions / CTR / position | Google Search Console (your real data for this page) |
| Keyword volume, CPC, difficulty | Whichever keyword tool is connected |
| Backlinks / referring domains | Ahrefs, Semrush, or Ubersuggest — whichever exposes it |
| Domain overview (traffic, authority) | Same, whichever account has it |
| Rank tracking | Same |
| Competitor gap | Same |
| Top 10 SERP results for the keyword | A connected tool's own SERP data if available, else a direct Google scrape fallback |

**Important nuance:** these functions aren't hardcoded per-provider. Each
connected MCP account (Ubersuggest/Ahrefs/Semrush) is asked what tools it
actually has (`list_tools()`), and `mcp_functions.py` matches those real
tool names/descriptions against six categories (keyword data, keyword
difficulty, backlinks, domain overview, rank tracking, competitor gap) by
keyword-in-name matching. If an account doesn't expose a matching tool —
e.g. a free-tier Ahrefs account without backlink access — the panel
honestly shows "Not available on this account" rather than a fake result.

## SERP results: three paths, in priority order

1. **Serper (dedicated SERP API)**, if connected — the most reliable
   option, a purpose-built search API, not scraping. Get a free-tier key
   at https://serper.dev
2. **A connected MCP provider's own SERP tool** (Ahrefs/Semrush/etc.), if
   one exists on that account.
3. **Direct Google scrape** (`serp_scraper.py`), only as a last resort
   when neither of the above is connected. This is inherently fragile —
   Google actively detects and blocks scraping (CAPTCHAs, rate limits,
   markup changes breaking the parser). It's a reasonable "something is
   better than nothing" fallback, not a production-grade solution.

Keyword difficulty follows the same idea: tries a connected tool's own
difficulty figure first, falls back to Keywords Everywhere's competition
score if that's connected, and shows nothing (not a guess) if neither is
available.

## Adding a new tool

Still just one entry in `providers.py` — see the code comments there.
`kind: "mcp_oauth"` gets all six functions above automatically (as long
as the account has matching tools); `kind: "api_key"` gets the keyword
lookup pattern from `api_key_auth.py`.

## A note on testing

Tested end-to-end locally:
- The audit engine and all provider status endpoints
- The full Keywords Everywhere connect → live lookup → disconnect cycle
  (a real 403 came back from their actual API using a fake key)
- `mcp_functions.py`'s category matching and argument-guessing, against
  realistic tool name/schema samples modeled on Ubersuggest's real tools
  (keyword_overview, backlinks_overview, domain_overview, competitors) —
  correctly matched each to its category and built valid arguments
- `serp_scraper.py`'s HTML parsing, CAPTCHA-detection, and empty-result
  error paths, against representative mocked Google SERP markup

**Not testable from the sandbox this was built in** (its network egress
blocks these hosts, confirmed via direct 403s): the live OAuth handshake
against Ubersuggest/Ahrefs/Semrush's real servers, and a live scrape
against the real google.com. Both are architecturally sound and were
validated as thoroughly as possible without real network access — test
them for real once this is running somewhere with normal internet access.
If the OAuth flow does error, the message shown will contain the real
root cause, not a generic one.

## Deploying it publicly

Standard FastAPI app — deploys anywhere that runs Python (Render, Railway,
Fly.io, a VPS, Docker). Set `BASE_URL` to your real public URL, and (if
using GSC) update the Google Cloud redirect URI to match.

## Extending it

- Moz's live call isn't wired up yet (format depends on your API tier)
- A real SERP API (rather than scraping) would be a strict upgrade if
  you don't want to require Ahrefs/Semrush for reliable SERP data
- Cache results briefly so re-running an audit doesn't re-fetch everything
- Rate limiting before going public — every audit, keyword lookup, and
  SERP fetch makes a live outbound request
