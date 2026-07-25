# Discovery Fallback (Agent-Driven Enrichment)

When the primary Scout sources (LinkedIn MCP, `web_search`, SearXNG) are degraded or
unavailable, use this fallback chain. Order matters: prefer the highest-quality source
that is actually responding. Run `python3 scripts/discovery_probe.py` at pipeline start
to learn which sources are live in ~30s — never waste a full run discovering mid-way that
everything is down.

## SearXNG (preferred fallback when LinkedIn MCP is absent)
- Health: `curl -s "http://localhost:8888/search?q=test&format=json&limit=3"`
- Engines report in `unresponsive_engines`:
  - `brave` → "too many requests" = **rate-limited, recovers after backoff** (not dead).
  - `karmasearch` → "access denied" = needs re-grant, **won't recover this run**.
- **Backoff pattern (critical):** sequential queries trip brave's rate limit fast. Space
  queries ≥15–20s apart. On an `unresponsive_engines` entry containing a brave rate-limit,
  sleep `2**(attempt+1)*5`s (10/20/40/80) and retry. A single query CAN return results
  even when the prior returned 0 — so don't conclude "SearXNG dead" from one empty response.
- If SearXNG returns 0 results for a *known* public person (e.g. "Elon Musk"), it's a
  backend outage, not a no-match.

## DuckDuckGo HTML (last-resort discovery)
When SearXNG is fully down:
```bash
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" \
  "https://html.duckduckgo.com/html/?q=NAME+HINT" -o /tmp/ddg.html
```
Parse result links (DDG wraps them in a redirect):
```python
import re, urllib.parse
t = open('/tmp/ddg.html', encoding='utf-8', errors='ignore').read()
links = re.findall(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', t)
for u, txt in links:
    m = re.search(r'uddg=([^&]+)', u)
    real = urllib.parse.unquote(m.group(1)) if m else u
```
Caveats:
- **DDG hard-rate-limits:** after a few queries in a session it returns HTTP 202 with an
  anomaly/block page (`result__a` count = 0, body contains "anomaly"). Treat 0 links as
  "unavailable," not "no results."
- **Do NOT burn DDG on test queries** — every probe consumes the limited budget. Probe once,
  then decide. Space real DDG queries ≥3s apart.

## Page fetch for Sift (when you have candidate URLs)
- The SearXNG backend makes `web_extract` fail ("search-only backend cannot extract URL
  content"). Use `curl -s "https://r.jina.ai/URL"` instead — but Jina returns HTTP 403 for
  LinkedIn (`AbuseAlleviationError`).
- `web_read` (getmd/Readability) may be advertised by `tool_search` but is **not always
  loaded** in a cron profile — verify with a real call before depending on it.
- Direct `curl -A "Mozilla/5.0"` on personal sites/portfolios works; skip authwalled domains.

## All sources down → defer, do not fabricate
See "Discovery Source Availability & No-Fabrication Rule" in SKILL.md. Summary: skip
non-persons + unresolvables normally, **defer** real people who already carry recoverable
Google-sync context (missing only one field), log `pipeline_blocked` with `stage: scout`,
and write NO enrichment facts.
