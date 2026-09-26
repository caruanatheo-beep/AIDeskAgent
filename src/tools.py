from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import CalendarEvent, Evidence, MarketPoint, NewsItem, ToolResult
from .security import safe_text
from .web_search import search_official_web


@dataclass
class ToolContext:
    query: str
    points: list[MarketPoint]
    news: list[NewsItem]
    events: list[CalendarEvent]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    handler: Callable[[ToolContext], ToolResult]


def _snapshot(context: ToolContext) -> ToolResult:
    terms = set(context.query.casefold().replace("/", " ").split())
    selected = [point for point in context.points if point.value is not None and (
        not terms or any(term in f"{point.label} {point.category}".casefold() for term in terms)
    )]
    if len(selected) < 4:
        selected = [point for point in context.points if point.value is not None]
    selected = selected[:24]
    evidence = [Evidence(
        claim=(f"{point.label}: {point.value} {point.unit} at {point.date}; previous "
               f"{point.previous} at {point.previous_date or 'unavailable'}; change {point.change}; status {point.status}"),
        value=str(point.value), source=point.source_name, url=point.source_url,
        observation_date=point.date, evidence_type="observed market or macro value",
        confidence="medium" if point.source_name.startswith("Yahoo") else "high",
    ) for point in selected]
    summary = "\n".join(item.claim for item in evidence)
    return ToolResult("market_snapshot", summary, evidence)


def _news(context: ToolContext) -> ToolResult:
    evidence = [Evidence(
        claim=safe_text(item.title, 280), source=item.source, url=item.url,
        observation_date=item.published, evidence_type="official headline", confidence="medium",
    ) for item in context.news[:10]]
    return ToolResult("official_news", "\n".join(item.claim for item in evidence), evidence)


def _central_banks(context: ToolContext) -> ToolResult:
    markers = ("reserve", "central bank", "england", "japan")
    items = [item for item in context.news if any(marker in item.source.casefold() for marker in markers)]
    evidence = [Evidence(
        claim=safe_text(item.title, 280), source=item.source, url=item.url,
        observation_date=item.published, evidence_type="central-bank communication", confidence="high",
    ) for item in items[:10]]
    return ToolResult("central_banks", "\n".join(item.claim for item in evidence), evidence,
                      [] if evidence else ["No central-bank item was available in the current feed snapshot."])


def _calendar(context: ToolContext) -> ToolResult:
    evidence = [Evidence(
        claim=(f"{event.title}; region {event.region}; importance {event.importance}; "
               f"previous {event.previous}; consensus {event.consensus}; actual {event.actual}"),
        source=event.institution, url=event.url, observation_date=f"{event.date} {event.time}",
        evidence_type="official scheduled event", confidence="high",
    ) for event in context.events[:12]]
    return ToolResult("macro_calendar", "\n".join(item.claim for item in evidence), evidence)


def _web(context: ToolContext) -> ToolResult:
    return search_official_web(context.query)


TOOL_REGISTRY: dict[str, AgentTool] = {
    "market_snapshot": AgentTool("market_snapshot", "Current rates, macro, FX, equities, credit and commodities snapshot.", _snapshot),
    "official_news": AgentTool("official_news", "Dated official economic and policy headlines.", _news),
    "central_banks": AgentTool("central_banks", "Federal Reserve, ECB, Bank of England and Bank of Japan communications.", _central_banks),
    "macro_calendar": AgentTool("macro_calendar", "Upcoming official macroeconomic releases and policy events.", _calendar),
    "web_search": AgentTool("web_search", "Bounded search restricted to allowlisted official domains.", _web),
}


def classify_and_plan(query: str, max_steps: int) -> tuple[str, list[str], list[str]]:
    lower = query.casefold()
    tools = ["market_snapshot"]
    classification = "cross-asset market question"
    if any(word in lower for word in ("calendar", "release", "event", "today", "week", "payroll", "cpi", "pce")):
        tools.append("macro_calendar")
        classification = "macro calendar and market question"
    if any(word in lower for word in ("fed", "ecb", "boe", "boj", "central bank", "policy", "speech")):
        tools.append("central_banks")
        classification = "central-bank research question"
    if any(word in lower for word in ("news", "headline", "latest", "development", "morning", "brief")):
        tools.append("official_news")
    # Web search is the only tool that may add a live network call. Keep it for
    # explicit causal or announcement requests; current headlines already come
    # from the dashboard snapshot.
    if any(word in lower for word in ("why", "drove", "cause", "speech", "announcement")):
        tools.append("web_search")
    tools = list(dict.fromkeys(tools))[:max_steps]
    plan = [f"Collect {TOOL_REGISTRY[name].description}" for name in tools]
    plan.append("Build and audit the evidence register before synthesis.")
    return classification, plan, tools


def execute_tools(context: ToolContext, names: list[str], session: dict[str, object], max_per_tool: int) -> list[ToolResult]:
    counts = dict(session.get("tool_counts", {}))
    results: list[ToolResult] = []
    for name in names:
        if counts.get(name, 0) >= max_per_tool:
            results.append(ToolResult(name, "Tool call limit reached.", warnings=[f"{name}: per-session limit reached."]))
            continue
        tool = TOOL_REGISTRY[name]
        results.append(tool.handler(context))
        counts[name] = counts.get(name, 0) + 1
    session["tool_counts"] = counts
    return results


def registry_markdown() -> str:
    lines = ["### Available research tools"]
    lines.extend(f"- **{tool.name}**: {tool.description}" for tool in TOOL_REGISTRY.values())
    return "\n".join(lines)
