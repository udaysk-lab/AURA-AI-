"""Web research.

Search providers are pluggable because none of them is obviously right and they
all change pricing. Set SEARCH_PROVIDER plus the matching key; with none
configured, research falls back to what AURA already knows from your own inbox,
contacts and documents, and says so rather than inventing sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings
from app.llm import get_provider

log = logging.getLogger("aura.research")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def _brave(query: str, limit: int) -> list[SearchResult]:
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": settings.search_api_key, "Accept": "application/json"},
        params={"q": query, "count": limit},
        timeout=20,
    )
    resp.raise_for_status()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
        )
        for item in resp.json().get("web", {}).get("results", [])[:limit]
    ]


def _serper(query: str, limit: int) -> list[SearchResult]:
    resp = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": settings.search_api_key, "Content-Type": "application/json"},
        json={"q": query, "num": limit},
        timeout=20,
    )
    resp.raise_for_status()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in resp.json().get("organic", [])[:limit]
    ]


def _tavily(query: str, limit: int) -> list[SearchResult]:
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": settings.search_api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        },
        timeout=25,
    )
    resp.raise_for_status()
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("content", "")[:500],
        )
        for item in resp.json().get("results", [])[:limit]
    ]


PROVIDERS = {"brave": _brave, "serper": _serper, "tavily": _tavily}


def search_configured() -> bool:
    return bool(settings.search_provider in PROVIDERS and settings.search_api_key)


def search(query: str, limit: int = 6) -> list[SearchResult]:
    if not search_configured():
        raise SearchUnavailable(
            "No search provider configured. Set SEARCH_PROVIDER (brave|serper|tavily) "
            "and SEARCH_API_KEY to enable web research."
        )
    fn = PROVIDERS[settings.search_provider]
    try:
        return fn(query, limit)
    except httpx.HTTPStatusError as exc:
        raise SearchUnavailable(
            f"Search provider returned {exc.response.status_code}. Check SEARCH_API_KEY."
        ) from exc
    except Exception as exc:
        raise SearchUnavailable(f"Search failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT = """Answer the question using only the sources below.

Rules:
- Be specific and factual. No hedging, no filler.
- Cite sources inline as [1], [2] matching the numbering.
- If the sources don't answer it, say so plainly rather than guessing.
- Six sentences maximum.

Question: {query}

Sources:
{sources}"""


def synthesise(query: str, results: list[SearchResult]) -> str:
    if not results:
        return "No sources found for that."

    provider = get_provider()
    numbered = "\n\n".join(
        f"[{i + 1}] {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(results)
    )

    if provider.name == "mock":
        lines = [f"Found {len(results)} sources (demo mode — no synthesis):"]
        lines += [f"{i + 1}. {r.title} — {r.url}" for i, r in enumerate(results)]
        return "\n".join(lines)

    try:
        resp = provider.complete(
            [
                {
                    "role": "system",
                    "content": "You are a research analyst. Concise, sourced, no speculation.",
                },
                {"role": "user", "content": SYNTHESIS_PROMPT.format(query=query, sources=numbered)},
            ]
        )
        return resp.text.strip()
    except Exception as exc:
        log.warning("Synthesis failed: %s", exc)
        return "\n".join(f"{i + 1}. {r.title} — {r.url}" for i, r in enumerate(results))


def research(query: str, limit: int = 6) -> dict:
    try:
        results = search(query, limit=limit)
    except SearchUnavailable as exc:
        return {"query": query, "error": str(exc), "sources": [], "summary": ""}

    return {
        "query": query,
        "summary": synthesise(query, results),
        "sources": [r.as_dict() for r in results],
    }
