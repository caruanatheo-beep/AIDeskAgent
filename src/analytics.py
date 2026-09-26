from __future__ import annotations

import html
from dataclasses import asdict

import pandas as pd
import plotly.graph_objects as go

from .models import CalendarEvent, MarketPoint, NewsItem


CHART_BG = "#0b0d0c"
CHART_PANEL = "#101311"
CHART_GRID = "#292d2a"
CHART_TEXT = "#e8e6df"
CHART_MUTED = "#888d89"
CHART_ORANGE = "#e98c32"
CHART_GREEN = "#55d26f"
CHART_RED = "#ef5b50"
CHART_BLUE = "#6f8cff"


def _terminal_layout(fig: go.Figure, title: str, height: int, bottom: int = 42) -> None:
    fig.update_layout(
        title={"text": title.upper(), "font": {"size": 13, "color": CHART_ORANGE}, "x": 0.02},
        paper_bgcolor=CHART_PANEL,
        plot_bgcolor=CHART_PANEL,
        font={"color": CHART_TEXT, "family": "IBM Plex Mono, Consolas, monospace", "size": 11},
        margin={"l": 48, "r": 20, "t": 52, "b": bottom},
        height=height,
        hoverlabel={"bgcolor": "#171a18", "bordercolor": CHART_GRID, "font_color": CHART_TEXT},
    )
    fig.update_xaxes(gridcolor=CHART_GRID, linecolor=CHART_GRID, zerolinecolor=CHART_GRID)
    fig.update_yaxes(gridcolor=CHART_GRID, linecolor=CHART_GRID, zerolinecolor=CHART_GRID)


def point_map(points: list[MarketPoint]) -> dict[str, MarketPoint]:
    return {point.series_id: point for point in points}


def curve_spreads(points: list[MarketPoint]) -> dict[str, float | None]:
    values = point_map(points)

    def spread(long_id: str, short_id: str) -> float | None:
        long = values.get(long_id)
        short = values.get(short_id)
        if not long or not short or long.value is None or short.value is None:
            return None
        return round((long.value - short.value) * 100, 1)

    return {"US 2s10s": spread("DGS10", "DGS2"), "US 5s30s": spread("DGS30", "DGS5")}


def _format_number(point: MarketPoint, value: float | None) -> str:
    if value is None:
        return "Unavailable"
    decimals = 4 if point.series_id in {"DEXUSEU", "DEXUSUK"} else 2
    if point.unit == "%":
        return f"{value:.{decimals}f}%"
    if point.unit == "thousand":
        return f"{value:,.0f}k"
    return f"{value:,.{decimals}f}"


def format_value(point: MarketPoint) -> str:
    return _format_number(point, point.value)


def format_change(point: MarketPoint) -> str:
    if point.change is None:
        return "N/A"
    if point.category in {"Rates", "Policy", "Credit"} and point.unit == "%":
        return f"{point.change * 100:+.1f} bp"
    if point.previous not in (None, 0) and point.category in {"FX", "Risk", "Commodities"}:
        return f"{point.change / point.previous * 100:+.2f}%"
    return f"{point.change:+,.2f}"


def points_frame(points: list[MarketPoint]) -> pd.DataFrame:
    rows = []
    for point in points:
        rows.append({
            "Indicator": point.label,
            "Category": point.category,
            "Latest": format_value(point),
            "Previous": _format_number(point, point.previous),
            "Change": format_change(point),
            "Observation": point.date,
            "Previous observation": point.previous_date or "unavailable",
            "Frequency": point.frequency,
            "Status": point.status,
            "Source": point.source_name,
            "Provider detail": point.error or "",
        })
    return pd.DataFrame(rows)


def calendar_frame(events: list[CalendarEvent]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Date": event.date,
        "Time": event.time,
        "Region": event.region,
        "Event": event.title,
        "Importance": event.importance,
        "Type": event.event_type,
        "Previous": event.previous,
        "Consensus": event.consensus,
        "Actual": event.actual,
        "Institution": event.institution,
        "Status": event.status,
        "Source": event.url,
    } for event in events])


def yield_curve_figure(points: list[MarketPoint]) -> go.Figure:
    values = point_map(points)
    tenors = [("2Y", "DGS2"), ("5Y", "DGS5"), ("10Y", "DGS10"), ("30Y", "DGS30")]
    x = [tenor for tenor, sid in tenors if values.get(sid) and values[sid].value is not None]
    y = [values[sid].value for _, sid in tenors if values.get(sid) and values[sid].value is not None]
    previous = [values[sid].previous for _, sid in tenors if values.get(sid) and values[sid].value is not None]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines+markers", name="Latest",
        line={"color": CHART_ORANGE, "width": 2.6}, marker={"size": 7},
    ))
    fig.add_trace(go.Scatter(
        x=x, y=previous, mode="lines+markers", name="Previous observation",
        line={"color": CHART_MUTED, "dash": "dot", "width": 1.8}, marker={"size": 5},
    ))
    _terminal_layout(fig, "US Treasury curve", 330)
    fig.update_layout(
        yaxis_title="Yield (%)",
        legend={"orientation": "h", "y": 1.13, "x": 0, "font": {"color": CHART_MUTED}},
    )
    return fig


def cross_asset_figure(points: list[MarketPoint]) -> go.Figure:
    selected = [point for point in points if point.series_id in {"DEXUSEU", "DEXJPUS", "SP500", "NASDAQCOM", "VIXCLS", "DCOILWTICO", "GOLDAMGBD228NLBM"} and point.value is not None]
    changes = [0 if point.previous in (None, 0) else point.change / point.previous * 100 for point in selected]
    colors = [CHART_GREEN if change >= 0 else CHART_RED for change in changes]
    fig = go.Figure(go.Bar(x=[point.label for point in selected], y=changes, marker_color=colors))
    _terminal_layout(fig, "Cross-asset move vs previous close", 330, 76)
    fig.update_layout(yaxis_title="Change (%)", bargap=0.38)
    fig.update_xaxes(tickangle=-20)
    fig.update_yaxes(zerolinecolor=CHART_MUTED)
    return fig


def history_figure(frame: pd.DataFrame, title: str = "Historical observations") -> go.Figure:
    fig = go.Figure()
    palette = [CHART_ORANGE, CHART_BLUE, CHART_GREEN, "#b47cff", "#e8c547", "#5bc0eb"]
    if not frame.empty and "Date" in frame.columns:
        line_index = 0
        for column in frame.columns:
            if column != "Date":
                fig.add_trace(go.Scatter(
                    x=frame["Date"], y=frame[column], mode="lines", name=column,
                    line={"color": palette[line_index % len(palette)], "width": 2},
                ))
                line_index += 1
    _terminal_layout(fig, title, 470)
    fig.update_layout(
        legend={"orientation": "h", "y": 1.12, "font": {"color": CHART_MUTED}},
        hovermode="x unified",
    )
    return fig


def _movement_sentence(point: MarketPoint) -> str:
    direction = "rose" if (point.change or 0) > 0 else "fell" if (point.change or 0) < 0 else "was unchanged"
    return f"{point.label} {direction} to {format_value(point)} ({format_change(point)})."


def narratives(points: list[MarketPoint]) -> list[str]:
    values = point_map(points)
    spreads = curve_spreads(points)
    output: list[str] = []
    s2s10 = spreads["US 2s10s"]
    if s2s10 is not None:
        shape = "inverted" if s2s10 < 0 else "positive"
        output.append(f"The US 2s10s curve is {shape} at {s2s10:+.1f} bp; this is an observed curve shape, not a recession forecast by itself.")
    spx, vix = values.get("SP500"), values.get("VIXCLS")
    if spx and vix and spx.value is not None and vix.value is not None:
        output.append(f"Risk tone is mixed: S&P 500 {format_change(spx)} while VIX {format_change(vix)} in the latest observations.")
    eur, dollar = values.get("DEXUSEU"), values.get("DTWEXBGS")
    if eur and dollar and eur.value is not None and dollar.value is not None:
        output.append(f"Dollar lens: EUR/USD {format_change(eur)} and the broad trade-weighted dollar {format_change(dollar)}.")
    hy = values.get("BAMLH0A0HYM2")
    if hy and hy.value is not None:
        output.append(f"US high-yield option-adjusted spread stands at {format_value(hy)} ({format_change(hy)}).")
    return output[:4]


def talking_points(points: list[MarketPoint]) -> list[str]:
    values = point_map(points)
    spreads = curve_spreads(points)
    dgs10 = values.get("DGS10")
    eur = values.get("DEXUSEU")
    hy = values.get("BAMLH0A0HYM2")
    items = [
        f"Rates: US 10Y is {format_value(dgs10)} ({format_change(dgs10)}); 2s10s is {spreads['US 2s10s']:+.1f} bp." if dgs10 and spreads["US 2s10s"] is not None else "Rates: key curve data are unavailable.",
        f"FX: EUR/USD is {format_value(eur)} ({format_change(eur)}); separate the observed move from any causal narrative." if eur else "FX: EUR/USD is unavailable.",
        f"Credit: US HY spread is {format_value(hy)} ({format_change(hy)}); confirm liquidity and cash/CDS context before client use." if hy else "Credit: high-yield spread is unavailable.",
    ]
    return [f"Draft, human approval required: {item}" for item in items]


def developments(points: list[MarketPoint]) -> list[dict[str, str]]:
    values = point_map(points)
    candidates = [values.get(sid) for sid in ("DGS10", "DGS2", "DEXUSEU", "VIXCLS", "BAMLH0A0HYM2")]
    cards: list[dict[str, str]] = []
    for point in candidates:
        if not point or point.value is None:
            continue
        interpretation = "The move may affect relative-value and hedging conversations, but causality is not established from price data alone."
        alternative = "Positioning, liquidity, technical flows or a different macro catalyst may also explain the move."
        cards.append({
            "title": point.label,
            "fact": f"{point.source_name} observation dated {point.date}; status: {point.status}.",
            "movement": _movement_sentence(point),
            "reported": "No causal explanation has been verified automatically from an official release.",
            "interpretation": interpretation,
            "alternative": alternative,
            "source": point.source_url,
        })
    return cards[:5]


def cockpit_html(points: list[MarketPoint], status: dict[str, object]) -> str:
    values = point_map(points)
    spreads = curve_spreads(points)
    kpis = [
        ("US 2Y", values.get("DGS2")), ("US 10Y", values.get("DGS10")),
        ("2s10s", None), ("EUR/USD", values.get("DEXUSEU")),
        ("S&P 500", values.get("SP500")), ("VIX", values.get("VIXCLS")),
    ]
    blocks = []
    for label, point in kpis:
        if label == "2s10s":
            value = "N/A" if spreads[label.replace("2s10s", "US 2s10s")] is None else f"{spreads['US 2s10s']:+.1f} bp"
            change = "curve spread"
        else:
            value = format_value(point) if point else "N/A"
            change = format_change(point) if point else "N/A"
        blocks.append(f'<div class="kpi"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong><small>{html.escape(change)}</small></div>')
    cards = []
    for card in developments(points)[:3]:
        cards.append(
            '<div class="move-card">'
            f'<h4>{html.escape(card["title"])}</h4>'
            f'<p>{html.escape(card["movement"])}</p>'
            f'<small>{html.escape(card["fact"])}</small>'
            '</div>'
        )
    return (
        f'<div class="snapshot-time">Snapshot generated {html.escape(str(status["generated_at"]))}</div>'
        f'<div class="kpi-grid">{"".join(blocks)}</div>'
        f'<div class="move-grid">{"".join(cards)}</div>'
    )


def briefing_markdown(points: list[MarketPoint], events: list[CalendarEvent], news: list[NewsItem] | None = None) -> str:
    narrative_lines = "\n".join(f"- {item}" for item in narratives(points)) or "- Insufficient verified data."
    event_lines = "\n".join(f"- **{event.date} {event.time}**: {event.title} ({event.institution})" for event in events[:4]) or "- No dated event retrieved; use the official schedule links."
    talking_lines = "\n".join(f"- {item}" for item in talking_points(points))
    news_lines = "\n".join(
        f"- [{item.title}]({item.url}): `{item.published}` | {item.source}" for item in (news or [])[:4]
    ) or "- No current official headline was retrieved."
    sources = []
    for point in points:
        if point.value is not None and point.series_id in {"DGS2", "DGS10", "DEXUSEU", "SP500", "VIXCLS", "DCOILWTICO", "BAMLH0A0HYM2"}:
            sources.append(f"[{point.label}]({point.source_url}) ({point.date})")
    return f"""## Daily Morning Brief

### Verified and observed market picture

{narrative_lines}

### Main events ahead

{event_lines}

### Official news flow

{news_lines}

### Potential client talking points

{talking_lines}

### Sources and verification

{" · ".join(sources)}

Facts and observation-to-observation movements are separated from interpretation. Market moves alone do not prove causality.
"""


def news_markdown(items: list[NewsItem], errors: list[str]) -> str:
    lines = ["### Official releases and central-bank news"]
    for item in items:
        lines.append(f"- [{item.title}]({item.url})  \n  `{item.published}` | {item.source} | {item.category}")
    if errors:
        lines.append("\n**Partial service status:** " + "; ".join(errors))
    lines.append("\nExternal feed text is treated as untrusted data and cannot issue instructions to the assistant.")
    return "\n".join(lines)


def calendar_markdown(events: list[CalendarEvent], error: str | None) -> str:
    lines = ["### Smaller official calendar"]
    for event in events:
        lines.append(f"- **{event.date} | {event.time}**: [{event.title}]({event.url}) | {event.institution} | `{event.status}`")
    if not events:
        lines.append("- No upcoming BLS event could be retrieved in the configured window.")
    if error:
        lines.append(f"\n**Service status:** {error}")
    lines.extend([
        "\nExact times should be reconfirmed on the official schedules:",
        (
            "[Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) · "
            "[ECB Governing Council calendar](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html) · "
            "[BLS release calendar](https://www.bls.gov/schedule/news_release/)"
        ),
    ])
    return "\n".join(lines)


def serializable_context(points: list[MarketPoint], news: list[NewsItem]) -> dict[str, object]:
    return {
        "market_data": [point.to_dict() for point in points if point.value is not None],
        "official_headlines": [asdict(item) for item in news[:8]],
        "curve_spreads_bp": curve_spreads(points),
        "narratives": narratives(points),
        "draft_talking_points": talking_points(points),
    }
