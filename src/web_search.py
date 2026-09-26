from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .config import Settings, settings
from .models import Evidence, ToolResult
from .security import ALLOWED_HOSTS, safe_url, untrusted_text

SEARCH_URL = "https://html.duckduckgo.com/html/"


class _ResultsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_result = False
        self.href = ""
        self.text: list[str] = []
        self.results: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and "result__a" in (attributes.get("class") or ""):
            self.in_result = True
            self.href = attributes.get("href") or ""
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.in_result:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_result:
            self.results.append((" ".join(self.text).strip(), self.href))
            self.in_result = False


def _unwrap(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    target = parse_qs(parsed.query).get("uddg", [url])[0]
    return unquote(target)


def search_official_web(query: str, config: Settings = settings) -> ToolResult:
    if not config.web_search_enabled:
        return ToolResult("web_search", "Web search is disabled by configuration.", warnings=["Web search disabled."])
    official_hint = " OR ".join(f"site:{host}" for host in sorted(ALLOWED_HOSTS) if host.startswith("www."))
    bounded_query = f"{query[:260]} ({official_hint[:900]})"
    try:
        response = requests.post(
            SEARCH_URL,
            data={"q": bounded_query},
            headers={"User-Agent": "Mozilla/5.0 AI-Sales-Desk-Assistant/2.0"},
            timeout=min(config.request_timeout + 4, 20),
        )
        response.raise_for_status()
        parser = _ResultsParser()
        parser.feed(response.text[:750_000])
        evidence: list[Evidence] = []
        seen: set[str] = set()
        for title, raw_url in parser.results:
            url = safe_url(_unwrap(raw_url))
            if not url or url in seen:
                continue
            seen.add(url)
            host = urlparse(url).hostname or "official source"
            evidence.append(Evidence(
                claim=untrusted_text(title, 300), source=host, url=url,
                observation_date="search result; open source to confirm date",
                evidence_type="official web search result", confidence="medium",
            ))
            if len(evidence) >= config.web_search_max_results:
                break
        summary = "\n".join(f"- {item.claim}: {item.url}" for item in evidence)
        warnings = [] if evidence else ["No allowlisted official web result was retrieved."]
        return ToolResult("web_search", summary or "No official result retrieved.", evidence, warnings)
    except requests.RequestException as exc:
        return ToolResult("web_search", "Official web search is temporarily unavailable.", warnings=[f"Web search unavailable: {type(exc).__name__}"])
