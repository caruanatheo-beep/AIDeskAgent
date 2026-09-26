from __future__ import annotations

import html
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import CalendarEvent, MarketPoint, NewsItem
from .security import redact_secrets


def _plain(markdown: str) -> str:
    text = re.sub(r"\[([^]]+)]\(([^)]+)\)", r"\1", markdown or "")
    text = re.sub(r"[*_`#>]", "", text)
    return redact_secrets(text).strip()


def _safe(value: object) -> str:
    return html.escape(redact_secrets(str(value or "")).replace("—", "-").replace("–", "-"))


def _clean_line(value: str) -> str:
    return _plain(value).strip(" -•\t")


def email_ready(title: str, body: str) -> str:
    sections = _parse_sections(body)
    summary = sections.get("EXECUTIVE SUMMARY", [])[:3]
    talking = sections.get("SALES TALKING POINTS", [])[:3]
    points = talking or summary
    key_points = "\n".join(f"- {x}" for x in points) or "- Please see the attached market update for today's key themes and catalysts."
    return f"""### Suggested subject

{title}

### Email-ready draft

Hello,

Please find attached our latest market update.

**Key points**

{key_points}

Please let me know if you would like to discuss any of these themes in more detail.

Best regards,

*Draft generated for human review. It was not sent automatically.*
"""


def report_sources(points: Iterable[MarketPoint], events: Iterable[CalendarEvent], news: Iterable[NewsItem]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for point in points:
        if point.value is not None and point.source_url and point.source_url not in seen:
            lines.append(f"{point.source_name} | {point.label} | {point.date} | {point.source_url}")
            seen.add(point.source_url)
    for event in events:
        if event.url and event.url not in seen:
            lines.append(f"{event.institution} | Calendar | {event.date} | {event.url}")
            seen.add(event.url)
    for item in news:
        if item.url and item.url not in seen:
            lines.append(f"{item.source} | Official release | {item.published} | {item.url}")
            seen.add(item.url)
    return "\n".join(lines)


SECTION_ALIASES = {
    "EXECUTIVE SUMMARY": "EXECUTIVE SUMMARY",
    "MARKET SUMMARY": "EXECUTIVE SUMMARY",
    "KEY TAKEAWAYS": "KEY TAKEAWAYS",
    "MACRO BACKDROP": "MACRO BACKDROP",
    "MACRO & POLICY": "MACRO BACKDROP",
    "RATES & CENTRAL BANKS": "RATES & CENTRAL BANKS",
    "RATES AND CENTRAL BANKS": "RATES & CENTRAL BANKS",
    "FX & CROSS-ASSET": "FX & CROSS-ASSET",
    "FX AND CROSS-ASSET": "FX & CROSS-ASSET",
    "FX & CROSS ASSET": "FX & CROSS-ASSET",
    "TODAY'S CATALYSTS": "TODAY'S CATALYSTS",
    "TODAYS CATALYSTS": "TODAY'S CATALYSTS",
    "CATALYSTS": "TODAY'S CATALYSTS",
    "SALES TALKING POINTS": "SALES TALKING POINTS",
    "CLIENT TALKING POINTS": "SALES TALKING POINTS",
    "RISKS TO WATCH": "RISKS TO WATCH",
    "RISKS": "RISKS TO WATCH",
    "VERIFIED FACTS": "EXECUTIVE SUMMARY",
    "OBSERVED MOVEMENTS": "FX & CROSS-ASSET",
    "INTERPRETATION": "SALES TALKING POINTS",
    "RISKS / WHAT TO WATCH": "RISKS TO WATCH",
}

STOP_HEADINGS = {
    "AUDITED SOURCES", "SOURCES", "AGENT STATUS", "RESEARCH TRACE",
    "VERIFICATION NOTE", "SOURCE REGISTER",
}


def _parse_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {name: [] for name in set(SECTION_ALIASES.values())}
    current: str | None = None
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line or line == "---":
            continue
        heading = re.sub(r"^[#*\s]+|[#*\s]+$", "", line).strip().upper().rstrip(":")
        if heading in STOP_HEADINGS:
            current = None
            continue
        if heading in SECTION_ALIASES:
            current = SECTION_ALIASES[heading]
            continue
        if current is None:
            continue
        cleaned = _clean_line(line)
        lowered = cleaned.casefold()
        if cleaned and not lowered.startswith((
            "model unavailable", "deterministic fallback", "agent status",
            "rate limit", "tool-call limit", "decision support only",
        )):
            sections[current].append(cleaned)
    return sections


def _agent_failed(body: str) -> bool:
    text = (body or "").strip().lower()
    failure_markers = (
        "input not accepted", "question is limited", "error:",
        "request failed", "unable to process", "invalid input",
        "model unavailable", "deterministic fallback", "rate limit",
        "tool-call limit", "no admissible evidence",
    )
    return not text or any(marker in text for marker in failure_markers)


def _fallback_sections(points: list[MarketPoint], events: list[CalendarEvent], news: list[NewsItem]) -> dict[str, list[str]]:
    """Deterministic institutional copy if the LLM is unavailable or misses a section."""
    sections = {name: [] for name in set(SECTION_ALIASES.values())}
    by_id = {p.series_id: p for p in points if p.value is not None}

    market = [p for p in points if p.value is not None and p.previous not in (None, 0) and p.category != "Macro"]
    ranked = sorted(market, key=lambda p: abs((float(p.value) - float(p.previous)) / float(p.previous)), reverse=True)
    leaders = ranked[:4]
    if leaders:
        sections["EXECUTIVE SUMMARY"] = [
            "Cross-asset price action is being led by " + ", ".join(f"{p.label} ({_format_move(p)})" for p in leaders[:3]) + ".",
            "The rates backdrop should be assessed through both the level and shape of the Treasury curve, while FX and risk assets provide the main cross-market confirmation signals.",
            "Macro releases in the dashboard are latest available observations rather than same-day prints, so release dates matter when interpreting the growth and inflation backdrop.",
            "Near-term attention should remain on official central-bank communication and the next scheduled macro catalysts.",
            "For client discussions, the key question is whether current cross-asset moves persist after the next policy or data catalyst rather than whether one session establishes a durable trend.",
        ]

    macro_ids = ("CPIAUCSL", "UNRATE", "PAYEMS", "INDPRO", "EFFR", "ECBDFR")
    for sid in macro_ids:
        p = by_id.get(sid)
        if p:
            sections["MACRO BACKDROP"].append(f"{p.label}: {_format_value(p)} as of {p.date}; latest available change {_format_move(p)}.")
    if not sections["MACRO BACKDROP"]:
        sections["MACRO BACKDROP"] = ["No current macro series were available in the verified snapshot; rely on the dated official calendar and market data below."]

    rate_ids = ("DGS2", "DGS5", "DGS10", "DGS30", "BAMLC0A0CM", "BAMLH0A0HYM2")
    for sid in rate_ids:
        p = by_id.get(sid)
        if p:
            sections["RATES & CENTRAL BANKS"].append(f"{p.label}: {_format_value(p)}, {_format_move(p)} versus the previous observation.")
    if by_id.get("DGS2") and by_id.get("DGS10"):
        spread = (float(by_id["DGS10"].value) - float(by_id["DGS2"].value)) * 100
        sections["RATES & CENTRAL BANKS"].append(f"US 2s10s stands near {spread:+.0f} bp, framing the current curve discussion around front-end policy sensitivity versus longer-dated term premium and growth expectations.")

    cross_ids = ("DEXUSEU", "DEXJPUS", "DTWEXBGS", "SP500", "NASDAQCOM", "VIXCLS", "DCOILWTICO", "GOLDAMGBD228NLBM")
    for sid in cross_ids:
        p = by_id.get(sid)
        if p:
            sections["FX & CROSS-ASSET"].append(f"{p.label}: {_format_value(p)} ({_format_move(p)}); observation {p.date}.")

    sections["TODAY'S CATALYSTS"] = [f"{e.date} {e.time} - {e.title} ({e.institution})." for e in events[:6]]
    sections["KEY TAKEAWAYS"] = [f"{p.label}: {_format_move(p)}." for p in leaders[:4]]
    sections["SALES TALKING POINTS"] = [
        "Rates: frame client conversations around curve shape and the relative sensitivity of the front end to policy expectations.",
        "FX: test whether dollar direction is confirmed across EUR/USD, USD/JPY and the broader trade-weighted index rather than relying on one pair.",
        "Risk assets: compare equity strength with volatility and credit spreads to distinguish broad risk appetite from index-specific performance.",
        "Commodities: monitor oil and gold as separate signals for growth, inflation and defensive demand; avoid treating them as a single risk factor.",
        "Catalysts: anchor tactical conversations to the next dated official event and refresh the narrative after the release rather than extrapolating stale macro data.",
    ]
    sections["RISKS TO WATCH"] = [
        "Different source timestamps can create apparent cross-asset confirmation that is not synchronous.",
        "Large one-day percentage moves may reflect contract rolls, market holidays or source conventions and should be checked before external use.",
        "Macro series can be revised and may be released with a lag; observation date and release date are not interchangeable.",
        "Unexpected central-bank communication or geopolitical developments can invalidate a tactical narrative quickly.",
    ]
    return sections

def _merge_with_fallback(body: str, points: list[MarketPoint], events: list[CalendarEvent], news: list[NewsItem]) -> dict[str, list[str]]:
    fallback = _fallback_sections(points, events, news)
    if _agent_failed(body):
        return fallback
    parsed = _parse_sections(body)
    for key, fallback_lines in fallback.items():
        if not parsed.get(key):
            parsed[key] = fallback_lines
    return parsed


def _format_value(point: MarketPoint) -> str:
    if point.value is None:
        return "N/A"
    if point.series_id in {"DEXUSEU", "DEXUSUK"}:
        value = f"{float(point.value):,.4f}"
    elif point.series_id == "DEXJPUS":
        value = f"{float(point.value):,.2f}"
    else:
        value = f"{float(point.value):,.2f}"
    if point.unit == "%":
        value += "%"
    return value


def _format_move(point: MarketPoint) -> str:
    if point.change is None:
        return "N/A"
    if point.unit == "%" and point.category in {"Rates", "Policy", "Credit"}:
        return f"{float(point.change) * 100:+.1f} bp"
    if point.previous not in (None, 0) and point.category not in {"Macro"}:
        pct = (float(point.value) - float(point.previous)) / float(point.previous) * 100
        return f"{pct:+.2f}%"
    return f"{float(point.change):+,.2f}"


def _pick(points: list[MarketPoint], ids: tuple[str, ...]) -> list[MarketPoint]:
    by_id = {p.series_id: p for p in points if p.value is not None}
    return [by_id[sid] for sid in ids if sid in by_id]


def _yield_curve_drawing(points: list[MarketPoint], width=245, height=155):
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    by_id = {p.series_id: p for p in points}
    tenors = [(2, "DGS2"), (5, "DGS5"), (10, "DGS10"), (30, "DGS30")]
    latest = [(t, float(by_id[s].value)) for t, s in tenors if s in by_id and by_id[s].value is not None]
    previous = [(t, float(by_id[s].previous)) for t, s in tenors if s in by_id and by_id[s].previous is not None]
    if len(latest) < 2:
        return None
    d = Drawing(width, height)
    d.add(String(8, height - 13, "US Treasury curve", fontName="Helvetica-Bold", fontSize=8.5, fillColor=colors.HexColor("#17324d")))
    p = LinePlot(); p.x, p.y, p.width, p.height = 28, 28, width - 42, height - 55
    p.data = [latest] + ([previous] if len(previous) >= 2 else [])
    p.lines[0].strokeColor = colors.HexColor("#167d78"); p.lines[0].strokeWidth = 2
    if len(p.data) > 1:
        p.lines[1].strokeColor = colors.HexColor("#94a3b8"); p.lines[1].strokeWidth = 1
    p.xValueAxis.valueSteps = [2, 5, 10, 30]; p.xValueAxis.labelTextFormat = lambda v: f"{int(v)}Y"
    p.xValueAxis.labels.fontSize = 6.5; p.yValueAxis.labels.fontSize = 6.5
    p.yValueAxis.visibleGrid = 1; p.yValueAxis.gridStrokeColor = colors.HexColor("#e5e7eb")
    d.add(p)
    return d


def _cross_asset_drawing(points: list[MarketPoint], width=245, height=155):
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    ids = ("DEXUSEU", "SP500", "VIXCLS", "DCOILWTICO", "GOLDAMGBD228NLBM", "NASDAQCOM")
    selected = [p for p in points if p.series_id in ids and p.value is not None and p.previous not in (None, 0)]
    if not selected:
        return None
    vals = [round((float(p.value) - float(p.previous)) / float(p.previous) * 100, 2) for p in selected]
    d = Drawing(width, height)
    d.add(String(8, height - 13, "Cross-asset move (%)", fontName="Helvetica-Bold", fontSize=8.5, fillColor=colors.HexColor("#17324d")))
    c = VerticalBarChart(); c.x, c.y, c.width, c.height = 30, 36, width - 44, height - 65
    c.data = [vals]; c.categoryAxis.categoryNames = [p.label.replace(" Composite", "") for p in selected]
    c.categoryAxis.labels.angle = 25; c.categoryAxis.labels.fontSize = 5.5; c.valueAxis.labels.fontSize = 6
    c.valueAxis.visibleGrid = 1; c.valueAxis.gridStrokeColor = colors.HexColor("#e5e7eb")
    c.bars[0].fillColor = colors.HexColor("#167d78"); c.barWidth = 8
    for index, value in enumerate(vals):
        c.bars[(0, index)].fillColor = colors.HexColor("#167d78" if value >= 0 else "#B54747")
    d.add(c); return d


def _history_drawing(chart_data: dict[str, list[object]] | None, width=500, height=220):
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    if not chart_data:
        return None
    raw_dates = list(chart_data.get("Date", []))
    series = [(n, v) for n, v in chart_data.items() if n != "Date" and v]
    normalized = []
    for name, values in series[:4]:
        clean, base = [], None
        trimmed_values = list(values)[-260:]
        trimmed_dates = raw_dates[-len(trimmed_values):] if raw_dates else []
        for i, value in enumerate(trimmed_values):
            try: numeric = float(value)
            except (TypeError, ValueError): continue
            try:
                x_value = datetime.fromisoformat(str(trimmed_dates[i])[:10]).date().toordinal() if trimmed_dates else i
            except (ValueError, TypeError, IndexError):
                x_value = i
            if base is None: base = numeric
            if base not in (None, 0): clean.append((x_value, numeric / base * 100))
        if clean: normalized.append((name, clean))
    if not normalized: return None
    d = Drawing(width, height)
    d.add(String(10, height - 15, "Selected market history - rebased to 100", fontName="Helvetica-Bold", fontSize=9, fillColor=colors.HexColor("#17324d")))
    p = LinePlot(); p.x, p.y, p.width, p.height = 38, 45, width - 58, height - 75
    p.data = [v for _, v in normalized]
    palette = ("#167d78", "#2563eb", "#7c3aed", "#c2410c")
    for i in range(len(normalized)):
        p.lines[i].strokeColor = colors.HexColor(palette[i]); p.lines[i].strokeWidth = 1.5
    p.yValueAxis.visibleGrid = 1; p.yValueAxis.gridStrokeColor = colors.HexColor("#e5e7eb")
    p.xValueAxis.labels.fontSize = 6; p.yValueAxis.labels.fontSize = 6
    if raw_dates and normalized:
        x_values = [x for _, values in normalized for x, _ in values]
        low, high = min(x_values), max(x_values)
        if high > low:
            p.xValueAxis.valueMin = low
            p.xValueAxis.valueMax = high
            p.xValueAxis.valueSteps = [round(low + (high - low) * ratio) for ratio in (0, .25, .5, .75, 1)]
            p.xValueAxis.labelTextFormat = lambda value: datetime.fromordinal(int(value)).strftime("%b %y")
    d.add(p)
    d.add(String(40, 20, "  |  ".join(n for n, _ in normalized)[:100], fontSize=7, fillColor=colors.HexColor("#64748b")))
    return d


def build_pdf(title: str, body: str, sources: str = "", chart_data: dict[str, list[object]] | None = None,
              points: list[MarketPoint] | None = None, events: list[CalendarEvent] | None = None,
              news: list[NewsItem] | None = None, report_type: str = "Client Market Update",
              audience: str = "", focus: str = "Global Macro") -> str:
    """Adaptive institutional market note.

    report_type changes editorial density; focus changes the dashboard, page order,
    charts and client-conversation blocks. The layout is original and uses common
    sell-side research conventions rather than copying any bank template.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle, KeepTogether)

    points, events, news = list(points or []), list(events or []), list(news or [])
    sections = _merge_with_fallback(body, points, events, news)
    focus = focus if focus in {"Global Macro", "Rates", "FX", "Cross-Asset"} else "Global Macro"
    report_type = report_type if report_type in {"Client Market Update", "Morning Brief", "Event Preview"} else "Client Market Update"
    audience_text = redact_secrets(audience).strip()[:100] or "Institutional clients"
    destination = Path(tempfile.mkdtemp(prefix="sales_desk_v5_")) / f"{report_type.replace(' ', '_')}.pdf"

    base = getSampleStyleSheet()
    navy=colors.HexColor('#102A43'); ink=colors.HexColor('#243B53'); teal=colors.HexColor('#0F766E')
    blue=colors.HexColor('#2F5D8A'); slate=colors.HexColor('#627D98'); pale=colors.HexColor('#F5F8FB')
    pale2=colors.HexColor('#EAF1F6'); line=colors.HexColor('#D6E0E8'); white=colors.white
    green=colors.HexColor('#13795B'); red=colors.HexColor('#B54747'); amber=colors.HexColor('#9A6700')
    styles={
      'Title':ParagraphStyle('Title',parent=base['Title'],fontName='Helvetica-Bold',textColor=navy,fontSize=22,leading=24,spaceAfter=2),
      'Deck':ParagraphStyle('Deck',parent=base['BodyText'],textColor=slate,fontSize=7.5,leading=9.5),
      'Hero':ParagraphStyle('Hero',parent=base['BodyText'],textColor=ink,fontSize=9.2,leading=13.2,spaceAfter=3),
      'Section':ParagraphStyle('Section',parent=base['Heading2'],fontName='Helvetica-Bold',textColor=navy,fontSize=12,leading=14,spaceAfter=4),
      'Subsection':ParagraphStyle('Subsection',parent=base['Heading2'],fontName='Helvetica-Bold',textColor=navy,fontSize=10.4,leading=12.5,spaceBefore=2,spaceAfter=2),
      'Kicker':ParagraphStyle('Kicker',parent=base['BodyText'],fontName='Helvetica-Bold',textColor=teal,fontSize=7.2,leading=9,spaceAfter=3),
      'Body':ParagraphStyle('Body',parent=base['BodyText'],textColor=ink,fontSize=8.3,leading=11.3,spaceAfter=3),
      'Dense':ParagraphStyle('Dense',parent=base['BodyText'],textColor=ink,fontSize=7.6,leading=10,spaceAfter=2.4),
      'Small':ParagraphStyle('Small',parent=base['BodyText'],textColor=slate,fontSize=6.8,leading=8.5),
      'Tiny':ParagraphStyle('Tiny',parent=base['BodyText'],textColor=slate,fontSize=6.1,leading=7.6),
      'TileLabel':ParagraphStyle('TileLabel',parent=base['BodyText'],fontName='Helvetica-Bold',textColor=slate,fontSize=5.8,leading=7,alignment=TA_CENTER),
      'TileValue':ParagraphStyle('TileValue',parent=base['BodyText'],fontName='Helvetica-Bold',textColor=navy,fontSize=10.5,leading=12,alignment=TA_CENTER),
      'TileMove':ParagraphStyle('TileMove',parent=base['BodyText'],textColor=teal,fontSize=6.3,leading=7.5,alignment=TA_CENTER),
    }
    generated=datetime.now(timezone.utc).strftime('%d %b %Y | %H:%M UTC')
    doc=SimpleDocTemplate(str(destination),pagesize=A4,leftMargin=14*mm,rightMargin=14*mm,topMargin=17*mm,bottomMargin=14*mm)

    def sec(title_, deck=''):
        out=[Paragraph(_safe(title_),styles['Section'])]
        if deck: out.append(Paragraph(_safe(deck),styles['Deck']))
        out.append(Spacer(1,1.5*mm)); return out

    def subsection(title_, deck=''):
        out=[Paragraph(_safe(title_),styles['Subsection'])]
        if deck:
            out.append(Paragraph(_safe(deck),styles['Deck']))
        out.append(Spacer(1,1.2*mm))
        return out

    def bullet_flow(lines_, limit=6):
        return [Paragraph(f"<bullet>&bull;</bullet>{_safe(x)}", styles['Dense']) for x in (lines_ or [])[:limit]]

    def metric_tiles(ids):
        pts=_pick(points, ids)
        if not pts: return Paragraph('Verified market data unavailable.',styles['Small'])
        cells=[]
        for p in pts[:6]:
            move=_format_move(p)
            cells.append([Paragraph(_safe(p.label),styles['TileLabel']),Paragraph(_safe(_format_value(p)),styles['TileValue']),Paragraph(_safe(move),styles['TileMove'])])
        t=Table([cells],colWidths=[(178*mm)/len(cells)]*len(cells))
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),pale),('BOX',(0,0),(-1,-1),.35,line),('INNERGRID',(0,0),(-1,-1),.35,line),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t

    def market_table(ids, max_rows=10):
        pts=_pick(points,ids) if isinstance(ids,tuple) else ids
        rows=[["Market","Latest","Move","As of"]]
        for p in pts[:max_rows]: rows.append([Paragraph(_safe(p.label),styles['Small']),_format_value(p),_format_move(p),str(p.date)[:16]])
        t=Table(rows,colWidths=[68*mm,28*mm,28*mm,46*mm],repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('TEXTCOLOR',(0,0),(-1,0),white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),6.5),('ROWBACKGROUNDS',(0,1),(-1,-1),[white,pale]),('LINEBELOW',(0,0),(-1,-1),.25,line),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3.1),('BOTTOMPADDING',(0,0),(-1,-1),3.1)]))
        return t

    def event_table(limit=8):
        if not events: return None
        rows=[["Date","Time","Event","Institution"]]
        for e in events[:limit]: rows.append([str(e.date),str(e.time),Paragraph(_safe(e.title),styles['Small']),Paragraph(_safe(e.institution),styles['Small'])])
        t=Table(rows,colWidths=[24*mm,18*mm,96*mm,32*mm],repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),teal),('TEXTCOLOR',(0,0),(-1,0),white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),6.3),('ROWBACKGROUNDS',(0,1),(-1,-1),[white,pale]),('LINEBELOW',(0,0),(-1,-1),.25,line),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        return t

    focus_cfg={
      'Global Macro': {'tag':'GLOBAL MACRO','tiles':('DGS2','DGS10','DEXUSEU','SP500','VIXCLS','DCOILWTICO'),'table':('DGS2','DGS5','DGS10','DGS30','DEXUSEU','DEXJPUS','SP500','NASDAQCOM','VIXCLS','DCOILWTICO','GOLDAMGBD228NLBM'),'lead':'Macro regime, policy pricing and cross-asset confirmation'},
      'Rates': {'tag':'RATES STRATEGY','tiles':('DGS2','DGS5','DGS10','DGS30','EFFR','BAMLH0A0HYM2'),'table':('DGS2','DGS5','DGS10','DGS30','EFFR','ECBDFR','BAMLC0A0CM','BAMLH0A0HYM2'),'lead':'Curve shape, policy sensitivity and credit conditions'},
      'FX': {'tag':'FX STRATEGY','tiles':('DEXUSEU','DEXUSUK','DEXJPUS','DTWEXBGS','DGS2','DGS10'),'table':('DEXUSEU','DEXUSUK','DEXJPUS','DTWEXBGS','DGS2','DGS10','ECBDFR','EFFR'),'lead':'Dollar direction, relative rates and central-bank divergence'},
      'Cross-Asset': {'tag':'CROSS-ASSET','tiles':('SP500','NASDAQCOM','VIXCLS','DCOILWTICO','GOLDAMGBD228NLBM','BAMLH0A0HYM2'),'table':('SP500','NASDAQCOM','VIXCLS','DCOILWTICO','GOLDAMGBD228NLBM','BAMLC0A0CM','BAMLH0A0HYM2','DGS10'),'lead':'Risk appetite, volatility, credit and commodity confirmation'},
    }
    cfg=focus_cfg[focus]
    story=[]

    # COVER / PAGE 1
    story += [Paragraph(cfg['tag'],styles['Kicker']),Paragraph(_safe(title),styles['Title']),Paragraph(f"{_safe(report_type)} | {_safe(audience_text)} | {generated}",styles['Deck']),Spacer(1,3*mm)]
    story.append(metric_tiles(cfg['tiles'])); story.append(Spacer(1,4*mm))
    summary=' '.join(sections.get('EXECUTIVE SUMMARY',[])[:5])
    if summary: story += [*subsection('The Big Picture','The core macro and market narrative for the session'),Paragraph(_safe(summary),styles['Hero']),Spacer(1,2*mm)]

    left=subsection('What Matters Now')+bullet_flow(sections.get('KEY TAKEAWAYS',[]) or sections.get('FX & CROSS-ASSET',[])[:4],5)
    right=subsection('Client Lens')+bullet_flow(sections.get('SALES TALKING POINTS',[]),4)
    box=Table([[left,right]],colWidths=[88*mm,88*mm])
    box.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(0,0),pale),('BACKGROUND',(1,0),(1,0),pale2),('BOX',(0,0),(-1,-1),.4,line),('INNERGRID',(0,0),(-1,-1),.4,line),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story += [box,Spacer(1,4*mm),*subsection('Market snapshot','Latest verified levels and moves across the selected market focus'),market_table(cfg['table'],6 if report_type=='Event Preview' else 8)]
    if report_type=='Event Preview':
        et=event_table(3)
        if et: story += [Spacer(1,4*mm),*subsection('Events in Focus'),et]

    def add_focus_detail(compact=False):
        chart_height=145 if compact else 170
        if focus in {'Global Macro','Rates'}:
            story.extend(sec('Rates, Policy & Macro',cfg['lead']))
            curve=_yield_curve_drawing(points,width=255,height=chart_height)
            rate_text=[Paragraph(_safe(x),styles['Dense']) for x in sections.get('RATES & CENTRAL BANKS',[])[:4 if compact else 6]]
            cell2=[curve] if curve else [Paragraph('Curve unavailable.',styles['Small'])]
            table_focus=Table([[rate_text,cell2]],colWidths=[91*mm,82*mm])
            table_focus.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),3)]))
            story.append(table_focus)
            story.extend([Spacer(1,2*mm),*subsection('Macro Backdrop','Growth, inflation, labour and policy signals shaping the market regime')])
            story.extend(bullet_flow(sections.get('MACRO BACKDROP',[]),3 if compact else 5))
            if not compact:
                macro_ids=('CPIAUCSL','UNRATE','PAYEMS','INDPRO','EFFR','ECBDFR','BAMLC0A0CM','BAMLH0A0HYM2')
                story.extend([Spacer(1,2*mm),market_table(macro_ids,7)])
        elif focus=='FX':
            story.extend(sec('FX: Dollar, Relative Rates & Policy',cfg['lead']))
            story.extend(bullet_flow(sections.get('FX & CROSS-ASSET',[]),4 if compact else 6))
            story.extend([Spacer(1,2*mm),market_table(cfg['table'],5 if compact else 7)])
            if compact:
                hist=_history_drawing(chart_data,width=505,height=150)
                if hist: story.extend([Spacer(1,2*mm),hist])
            else:
                story.extend([Spacer(1,3*mm),*subsection('Policy & Macro Differentials','Relative policy and macro signals relevant for currency pricing')])
                story.extend(bullet_flow(sections.get('MACRO BACKDROP',[]),5))
        else:
            story.extend(sec('Cross-Asset Risk Map',cfg['lead']))
            cross=_cross_asset_drawing(points,width=510,height=160 if compact else 205)
            if cross: story.append(cross)
            story.extend([Spacer(1,2*mm),*subsection('Cross-Asset Read','Confirmation and divergence across equities, volatility, commodities and credit')])
            story.extend(bullet_flow(sections.get('FX & CROSS-ASSET',[]),4 if compact else 6))
            if not compact: story.extend([Spacer(1,2*mm),market_table(cfg['table'],7)])

    def add_complementary_lens():
        if focus in {'Global Macro','Rates'}:
            story.extend(sec('Cross-Asset Confirmation','Does price action outside rates confirm or challenge the macro narrative?'))
            cross=_cross_asset_drawing(points,width=510,height=195)
            if cross: story.append(cross)
            story.extend([Spacer(1,2*mm),*subsection('Cross-Asset Read')])
            story.extend(bullet_flow(sections.get('FX & CROSS-ASSET',[]),6))
        elif focus=='FX':
            story.extend(sec('Cross-Asset Confirmation','Equities, volatility, commodities and credit as confirmation signals for the FX narrative.'))
            cross=_cross_asset_drawing(points,width=510,height=195)
            if cross: story.append(cross)
            story.extend([Spacer(1,2*mm),*subsection('Confirmation / Divergence')])
            story.extend(bullet_flow(sections.get('FX & CROSS-ASSET',[]),6))
        else:
            story.extend(sec('Rates & Macro Anchor','The macro and policy backdrop behind current risk-asset price action.'))
            curve=_yield_curve_drawing(points,width=255,height=160)
            rate_text=[Paragraph(_safe(x),styles['Dense']) for x in sections.get('RATES & CENTRAL BANKS',[])[:5]]
            table_focus=Table([[rate_text,[curve] if curve else [Paragraph('Curve unavailable.',styles['Small'])]]],colWidths=[91*mm,82*mm])
            table_focus.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0)]))
            story.append(table_focus)
            story.extend([Spacer(1,2*mm),*subsection('Macro Backdrop')])
            story.extend(bullet_flow(sections.get('MACRO BACKDROP',[]),5))
        hist=_history_drawing(chart_data,width=505,height=185)
        if hist: story.extend([Spacer(1,3*mm),hist])

    def add_conversation(compact=False, include_sources=False):
        event_limit=4 if compact else 6
        et=event_table(event_limit)
        if et: story.extend([*subsection('Upcoming official events','Scheduled macro and policy events with potential market impact'),et,Spacer(1,3*mm)])
        talking_limit=3 if compact else 5
        risk_limit=3 if compact else 5
        left=subsection('Client Talking Points','Concise themes for client conversations')
        for i,x in enumerate(sections.get('SALES TALKING POINTS',[])[:talking_limit],1):
            left.append(Paragraph(f"<b>{i:02d}</b>&nbsp;&nbsp;{_safe(x)}",styles['Dense']))
        right=subsection('Risk Checklist','Developments that could challenge the current read')+bullet_flow(sections.get('RISKS TO WATCH',[]),risk_limit)
        conv=Table([[left,right]],colWidths=[88*mm,88*mm])
        conv.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(0,0),pale),('BACKGROUND',(1,0),(1,0),pale2),('BOX',(0,0),(-1,-1),.4,line),('INNERGRID',(0,0),(-1,-1),.4,line),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.append(conv)
        if news and not compact:
            story.extend([Spacer(1,3*mm),*subsection('Official Flow','Latest relevant communication from official institutions')])
            for item in news[:3]:
                story.append(Paragraph(f"<b>{_safe(item.source)}</b> | {_safe(item.published)} - {_safe(item.title)}",styles['Dense']))
        if include_sources:
            source_lines=[x for x in _plain(sources).splitlines() if x.strip()]
            if source_lines:
                story.extend([Spacer(1,3*mm),*subsection('Sources and controls','Primary public references; observation dates may differ')])
                source_cells=[]
                for i,line_text in enumerate(source_lines[:10],1):
                    parts=[part.strip() for part in line_text.split('|')]
                    url=parts[-1] if parts and parts[-1].startswith('http') else ''
                    label=' | '.join(parts[:-1]) if url else line_text
                    text=f"[{i}] {_safe(label)}"+(f" | <link href='{html.escape(url,quote=True)}' color='#0F766E'>source</link>" if url else '')
                    source_cells.append(Paragraph(text,styles['Tiny']))
                midpoint=(len(source_cells)+1)//2
                left_sources=source_cells[:midpoint]
                right_sources=source_cells[midpoint:]
                source_table=Table([[left_sources,right_sources]],colWidths=[88*mm,88*mm])
                source_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4)]))
                story.append(source_table)
            story.extend([Spacer(1,2*mm),Paragraph('Important information: Public-source decision support only. Observations may be delayed or revised. Commentary does not constitute investment advice, research advice, a recommendation or an offer to transact.',styles['Tiny'])])

    if report_type=='Morning Brief':
        story.append(PageBreak())
        add_focus_detail(compact=True)
        story.extend([Spacer(1,3*mm),*sec('Catalysts & Client Conversation','The next events and the risks to the current read.')])
        add_conversation(compact=True)
    elif report_type=='Event Preview':
        story.append(PageBreak())
        add_focus_detail(compact=False)
        story.append(PageBreak())
        story.extend(sec('Catalysts & Client Conversation','Event risk, client questions and verification controls.'))
        add_conversation(compact=False,include_sources=True)
    else:
        story.append(PageBreak())
        add_focus_detail(compact=False)
        story.append(PageBreak())
        add_complementary_lens()
        story.append(PageBreak())
        story.extend(sec('Catalysts & Client Conversation','The next events, client questions and risks to the current read.'))
        add_conversation(compact=False,include_sources=True)

    class ResearchCanvas(Canvas):
        def showPage(self):
            self.saveState(); w,h=A4
            self.setFillColor(navy); self.rect(0,h-8*mm,w,8*mm,fill=1,stroke=0)
            self.setFillColor(white); self.setFont('Helvetica-Bold',6.2); self.drawString(14*mm,h-5.2*mm,'AI SALES DESK  |  MARKET INTELLIGENCE')
            self.setFillColor(slate); self.setFont('Helvetica',6.2); self.drawString(14*mm,7*mm,f'{report_type} | {focus}'); self.drawRightString(w-14*mm,7*mm,f'{generated}   |   Page {self._pageNumber}')
            self.restoreState(); super().showPage()
    doc.build(story,canvasmaker=ResearchCanvas)
    return str(destination)
