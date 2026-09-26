from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    label: str
    category: str
    unit: str
    frequency: str
    decimals: int = 2
    source_name: str = "FRED"

    @property
    def source_url(self) -> str:
        return f"https://fred.stlouisfed.org/series/{self.series_id}"


@dataclass
class MarketPoint:
    series_id: str
    label: str
    category: str
    value: float | None
    previous: float | None
    change: float | None
    date: str
    unit: str
    frequency: str
    status: str
    source_name: str
    source_url: str
    previous_date: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewsItem:
    title: str
    source: str
    published: str
    url: str
    category: str = "Official release"


@dataclass
class CalendarEvent:
    date: str
    time: str
    title: str
    institution: str
    url: str
    status: str = "official schedule"
    region: str = "United States"
    importance: str = "Medium"
    event_type: str = "Macro release"
    previous: str = "unavailable"
    consensus: str = "unavailable"
    actual: str = "unavailable"


@dataclass(frozen=True)
class Evidence:
    """One auditable item used by the research agent."""

    claim: str
    source: str
    url: str
    observation_date: str
    evidence_type: str = "verified fact"
    confidence: str = "high"
    value: str = ""


@dataclass
class ToolResult:
    tool: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ResearchTrace:
    classification: str
    plan: list[str]
    tools_used: list[str]
    evidence_count: int
    warnings: list[str] = field(default_factory=list)
    coverage_score: int = 0
    source_count: int = 0
    high_confidence_count: int = 0
