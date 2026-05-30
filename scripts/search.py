#!/usr/bin/env python3
import argparse
import json
import re
import sys
from urllib.parse import unquote, quote_plus

import requests
from html import unescape


def _unwrap_ddg_url(url: str) -> str:
    if "//duckduckgo.com/l/" not in url:
        return url
    m = re.search(r"uddg=([^&]+)", url)
    if m:
        return unquote(m.group(1))
    return url


HTML_PARSER = "html.parser"


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def _parse_ddg_html(html: str, max_results: int):
    results = []
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )
    for url, title, _, snippet in blocks[:max_results]:
        results.append(
            {
                "title": _strip_tags(unescape(title)),
                "url": _unwrap_ddg_url(url),
                "snippet": _strip_tags(unescape(snippet)),
            }
        )
    if results:
        return results
    link_blocks = re.findall(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL
    )
    for url, title in link_blocks[:max_results]:
        t = _strip_tags(unescape(title))
        if len(t) < 3 or t.lower() in ("images", "videos", "news", "maps"):
            continue
        if "duckduckgo" in url:
            continue
        results.append({"title": t, "url": url, "snippet": ""})
    return results[:max_results]


def _parse_ddg_lite(html: str, max_results: int):
    results = []
    rows = re.findall(
        r'<tr[^>]*>.*?'
        r'<td[^>]*>.*?</td>\s*'
        r'<td[^>]*>.*?</td>\s*'
        r'<td[^>]*>.*?</td>\s*'
        r'<td[^>]*>(.*?)</td>\s*'
        r'<td[^>]*>(.*?)</td>',
        html,
        re.DOTALL,
    )
    for snippet_cell, link_cell in rows[:max_results]:
        link_match = re.search(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', link_cell, re.DOTALL
        )
        if not link_match:
            continue
        url = link_match.group(1)
        title = _strip_tags(unescape(link_match.group(2)))
        snippet = _strip_tags(unescape(snippet_cell))
        if not title or "duckduckgo" in url.lower():
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
    return results[:max_results]


def search_ddg(query: str, max_results: int = 10):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        results = _parse_ddg_html(resp.text, max_results)
        if results:
            return results
    except Exception:
        pass

    try:
        resp = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return _parse_ddg_lite(resp.text, max_results)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(description="Web search via DuckDuckGo")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument(
        "-n", "--max", type=int, default=10, help="Max results (default: 10)"
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    query = " ".join(args.query)
    results = search_ddg(query, args.max)

    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No results found.")
            sys.exit(1)
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r['title']}")
            print(f"    {r['url']}")
            if r["snippet"]:
                print(f"    {r['snippet']}")
            print()


if __name__ == "__main__":
    main()
