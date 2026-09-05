"""Export GitHub repository candidates for Suns Chan research.

This is a discovery tool, not a quality filter. GitHub full-text search cannot
prove that it has found every relevant project, so each output record includes
the query and retrieval timestamp. Use GITHUB_TOKEN to increase GitHub API rate
limits; the public endpoint also works within its lower anonymous limit.

Example:
    python tools/discover_github_repos.py --output research/github-candidates.json
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_QUERIES = [
    '"persistent memory" "AI agent"',
    '"AI companion" memory',
    '"episodic memory" LLM agent',
    '"agent memory" language:Python',
    '"autonomous agent" "long-term memory"',
    '"self evolving" "AI agent"',
    '"cognitive architecture" LLM agent',
    '"memory consolidation" LLM agent',
    '"agent memory" MCP',
    '"temporal knowledge graph" agent',
    '"autonomous AI companion"',
    '"affective memory" AI',
    '"agent identity" persistent memory',
]


def search(query: str, pages: int, token: str | None) -> tuple[list[dict[str, Any]], str | None]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "suns-chan-research"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    results: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        parameters = urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 100, "page": page})
        request = Request(f"https://api.github.com/search/repositories?{parameters}", headers=headers)
        try:
            with urlopen(request, timeout=30) as response:  # nosec B310: fixed GitHub API endpoint
                data = json.load(response)
        except HTTPError as error:
            return results, f"HTTP {error.code} on page {page}: {error.reason}"
        except URLError as error:
            return results, f"network error on page {page}: {error.reason}"
        for repo in data["items"]:
            results.append(
                {
                    "query": query,
                    "full_name": repo["full_name"],
                    "url": repo["html_url"],
                    "description": repo["description"],
                    "stars": repo["stargazers_count"],
                    "updated_at": repo["updated_at"],
                    "archived": repo["archived"],
                    "fork": repo["fork"],
                    "license": repo["license"]["spdx_id"] if repo["license"] else None,
                }
            )
        if len(data["items"]) < 100:
            break
    return results, None


def write_snapshot(output: Path, queries: list[str], candidates: dict[str, dict[str, Any]], failures: dict[str, str]) -> None:
    payload = {
        "retrieved_at": datetime.now(UTC).isoformat(),
        "queries": queries,
        "completed_queries": [query for query in queries if query not in failures],
        "failures": failures,
        "unique_candidates": sorted(candidates.values(), key=lambda item: (-item["stars"], item["full_name"])),
        "limitations": [
            "GitHub search is relevance-ranked and capped at 1,000 results per query.",
            "A result is a candidate, not a security, maintenance, or license approval.",
            "Descriptions are author-provided claims and require source review.",
            "This file checkpoints after each query; inspect failures before treating it as a complete scan.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=5, help="pages per query, maximum 10")
    parser.add_argument("--query", action="append", dest="queries", help="additional or replacement query")
    args = parser.parse_args()
    if not 1 <= args.pages <= 10:
        parser.error("--pages must be between 1 and 10 (GitHub search exposes at most 1,000 results/query)")

    queries = args.queries or DEFAULT_QUERIES
    candidates: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for query in queries:
        items, error = search(query, args.pages, os.getenv("GITHUB_TOKEN"))
        if error:
            failures[query] = error
        for item in items:
            existing = candidates.setdefault(item["full_name"], item | {"queries": []})
            existing["queries"].append(query)
        write_snapshot(args.output, queries, candidates, failures)
        status = error or f"{len(items)} records"
        print(f"{query}: {status}")
    print(f"Wrote {len(candidates)} unique candidates to {args.output}; failures: {len(failures)}")


if __name__ == "__main__":
    main()
