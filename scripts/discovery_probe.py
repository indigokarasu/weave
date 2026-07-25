#!/usr/bin/env python3
"""Quick discovery-source probe for Weave agent-driven enrichment.

Prints which Scout discovery sources are usable in this environment so the pipeline can
decide to proceed, fall back, or defer (never fabricate). Run before contacting anyone.

Usage: python3 scripts/discovery_probe.py
"""
import json
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def probe_searxng():
    try:
        url = "http://localhost:8888/search?q=test&format=json&limit=3"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        res = d.get("results", [])
        unresp = d.get("unresponsive_engines", [])
        if res:
            return f"OK ({len(res)} results; unresponsive={unresp})"
        return f"DOWN (0 results; unresponsive={unresp})"
    except Exception as e:  # noqa: BLE001
        return f"ERROR ({e})"


def probe_ddg():
    try:
        url = "https://html.duckduckgo.com/html/?q=test"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read().decode("utf-8", "ignore")
        links = re.findall(r"result__a\"", t)
        if links:
            return f"OK ({len(links)} links)"
        if "anomaly" in t.lower() or "captcha" in t.lower():
            return "BLOCKED (anomaly/rate-limit page)"
        return "DOWN (0 links, no block marker)"
    except Exception as e:  # noqa: BLE001
        return f"ERROR ({e})"


def main():
    print("=== Weave discovery-source probe ===")
    print("web_search (MCP) : N/A (MCP tool — verify in-session that it returns")
    print("                    non-empty data.web; empty success:true = non-functional)")
    print(f"SearXNG         : {probe_searxng()}")
    print(f"DuckDuckGo HTML : {probe_ddg()}")
    print("LinkedIn MCP    : N/A (MCP tool — verify in-session that mcp_linkedin_*")
    print("                    tools are loaded)")
    print("\nDecision guide:")
    print("  - If SearXNG OK or DDG OK -> proceed with fallback chain.")
    print("  - If ALL down -> defer real people, skip non-persons/unresolvables,")
    print("    log pipeline_blocked (stage: scout), write NO facts.")
    sys.exit(0)


if __name__ == "__main__":
    main()
