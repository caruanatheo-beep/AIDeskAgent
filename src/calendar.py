from __future__ import annotations

import html as html_lib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import os
import shutil
import subprocess
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cachetools import TTLCache
import requests

from .config import settings
from .models import CalendarEvent

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_CALENDAR = "https://www.bls.gov/schedule/news_release/"
BEA_CALENDAR = "https://www.bea.gov/news/schedule"
FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
ECB_CALENDAR = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
BOE_CALENDAR = "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/calendar;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}

_CALENDAR_CACHE: TTLCache = TTLCache(maxsize=4, ttl=settings.cache_ttl)
_CALENDAR_CACHE_LOCK = threading.Lock()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return "\n".join(self.parts)


def _html_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    return html_lib.unescape(parser.text())


def _request_text(url: str, timeout: int | None = None) -> str:
    response = requests.get(
        url,
        headers=_HEADERS,
        timeout=(5, min(timeout or settings.request_timeout, 20)),
    )
    response.raise_for_status()
    return response.text


def _utc(date_value: datetime, timezone_name: str) -> datetime:
    try:
        return date_value.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    except ZoneInfoNotFoundError:
        return date_value.replace(tzinfo=timezone.utc)


def _event(
    dt: datetime,
    title: str,
    institution: str,
    url: str,
    region: str,
    importance: str,
    event_type: str,
    *,
    time_tbc: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        date=dt.strftime("%Y-%m-%d"),
        time="Time TBC" if time_tbc else dt.strftime("%H:%M UTC"),
        title=title[:180],
        institution=institution,
        url=url,
        region=region,
        importance=importance,
        event_type=event_type,
    )


def _in_window(dt: datetime, days_ahead: int) -> bool:
    now = datetime.now(timezone.utc)
    return now - timedelta(hours=18) <= dt <= now + timedelta(days=days_ahead)


def _download_bls_calendar(timeout: int = 20) -> str:
    """Download the official BLS calendar with a Windows-safe transport."""
    if os.name == "nt":
        executable = shutil.which("curl.exe")
        if executable:
            command = [
                executable, "--fail", "--silent", "--show-error", "--location",
                "--user-agent", _HEADERS["User-Agent"],
                "--connect-timeout", "5", "--max-time", str(timeout), BLS_ICS,
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=timeout + 2,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0 and result.stdout:
                    return result.stdout.decode("utf-8-sig", errors="replace")
            except (OSError, subprocess.TimeoutExpired):
                pass

    return _request_text(BLS_ICS, timeout)


def _metadata(title: str) -> tuple[str, str]:
    lower = title.casefold()
    if any(term in lower for term in ("cpi", "consumer price", "producer price", "pce", "inflation")):
        return "High", "Inflation"
    if any(term in lower for term in ("employment", "payroll", "job openings", "jolts", "unemployment", "earnings")):
        return "High", "Labour"
    if any(term in lower for term in ("fomc", "monetary policy", "bank rate", "governing council", "mpc")):
        return "High", "Central bank"
    if any(term in lower for term in ("gdp", "personal income", "personal consumption", "trade", "industrial", "productivity")):
        return "High", "Activity"
    return "Medium", "Macro release"


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_dt(value: str, timezone_name: str = "") -> datetime | None:
    raw_value = value.split(":", 1)[-1].strip()
    is_utc = raw_value.endswith("Z")
    raw = raw_value.removesuffix("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if is_utc or not timezone_name:
                return parsed.replace(tzinfo=timezone.utc)
            try:
                return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
            except ZoneInfoNotFoundError:
                return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_bls_events(days_ahead: int = 90, limit: int = 80) -> tuple[list[CalendarEvent], str | None]:
    try:
        calendar_text = _download_bls_calendar(max(12, min(settings.request_timeout + 8, 25)))
        events: list[CalendarEvent] = []
        current: dict[str, str] | None = None
        for line in _unfold_ics(calendar_text):
            if line == "BEGIN:VEVENT":
                current = {}
            elif line == "END:VEVENT" and current is not None:
                dt = _parse_dt(current.get("DTSTART", ""), current.get("DTSTART_TZID", ""))
                if dt and _in_window(dt, days_ahead):
                    title = current.get("SUMMARY", "BLS release")[:180]
                    importance, event_type = _metadata(title)
                    events.append(_event(
                        dt, title, "US Bureau of Labor Statistics", BLS_CALENDAR,
                        "United States", importance, event_type,
                    ))
                current = None
            elif current is not None and ":" in line:
                key, value = line.split(":", 1)
                base_key = key.split(";", 1)[0]
                current[base_key] = value.replace("\\,", ",")
                if base_key == "DTSTART" and "TZID=" in key:
                    current["DTSTART_TZID"] = key.split("TZID=", 1)[1].split(";", 1)[0]
        events = sorted(events, key=lambda event: (event.date, event.time))[:limit]
        return events, None if events else "BLS calendar unavailable: no upcoming event found in the official feed"
    except Exception as exc:
        return [], f"BLS calendar unavailable: {type(exc).__name__}"


def fetch_bea_events(days_ahead: int = 90) -> tuple[list[CalendarEvent], str | None]:
    """Load GDP, PCE, income and trade releases from the official BEA schedule."""
    try:
        text = _html_text(_request_text(BEA_CALENDAR))
        pattern = re.compile(
            r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
            r"(?P<day>\d{1,2})\s+(?P<time>\d{1,2}:\d{2}\s+[AP]M)\s+"
            r"(?:N\s*ews|D\s*ata|V\s*isual Data|A\s*rticle)?\s*"
            r"(?P<title>.*?)(?=\n(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2}\s+\d{1,2}:\d{2}\s+[AP]M|\nTo Be Announced|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        now = datetime.now(timezone.utc)
        events: list[CalendarEvent] = []
        for match in pattern.finditer(text):
            title = " ".join(match.group("title").split())
            title = re.sub(
                r"^(?:N\s*ews|D\s*ata|V\s*isual Data|A\s*rticle)\s*", "", title, flags=re.I,
            )
            local = datetime.strptime(
                f"{match.group('month')} {match.group('day')} {now.year} {match.group('time').upper()}",
                "%B %d %Y %I:%M %p",
            )
            dt = _utc(local, "America/New_York")
            if dt < now - timedelta(hours=18):
                dt = _utc(local.replace(year=now.year + 1), "America/New_York")
            if _in_window(dt, days_ahead):
                importance, event_type = _metadata(title)
                events.append(_event(
                    dt, title, "US Bureau of Economic Analysis", BEA_CALENDAR,
                    "United States", importance, event_type,
                ))
        return events, None if events else "BEA: no upcoming events returned"
    except Exception as exc:
        return [], f"BEA unavailable ({type(exc).__name__})"


def fetch_fomc_events(days_ahead: int = 180) -> tuple[list[CalendarEvent], str | None]:
    """Load scheduled FOMC decisions from the official Federal Reserve calendar."""
    try:
        text = _html_text(_request_text(FOMC_CALENDAR))
        now = datetime.now(timezone.utc)
        events: list[CalendarEvent] = []
        month_re = r"January|February|March|April|May|June|July|August|September|October|November|December"
        pattern = re.compile(
            rf"(?P<month>{month_re})\s+(?P<days>\d{{1,2}}(?:\s*[-–]\s*\d{{1,2}})?)\*?", re.I,
        )
        for year in (now.year, now.year + 1):
            marker = re.search(rf"\b{year}\s+FOMC Meetings\b", text, re.I)
            if not marker:
                continue
            following = text[marker.end():]
            next_year = re.search(r"\b20\d{2}\s+FOMC Meetings\b", following, re.I)
            section = following[:next_year.start()] if next_year else following[:5000]
            for match in pattern.finditer(section):
                final_day = int(re.split(r"[-–]", match.group("days"))[-1].strip())
                try:
                    local = datetime.strptime(
                        f"{match.group('month')} {final_day} {year} 2:00 PM", "%B %d %Y %I:%M %p",
                    )
                except ValueError:
                    continue
                dt = _utc(local, "America/New_York")
                if _in_window(dt, days_ahead):
                    events.append(_event(
                        dt, "FOMC monetary policy decision", "Federal Reserve", FOMC_CALENDAR,
                        "United States", "High", "Central bank",
                    ))
        return events, None if events else "Federal Reserve: no upcoming FOMC meeting in window"
    except Exception as exc:
        return [], f"Federal Reserve unavailable ({type(exc).__name__})"


def fetch_ecb_events(days_ahead: int = 180) -> tuple[list[CalendarEvent], str | None]:
    """Load market-relevant ECB Governing Council meetings."""
    try:
        text = _html_text(_request_text(ECB_CALENDAR))
        pattern = re.compile(
            r"(?P<date>\d{2}/\d{2}/\d{4})\s+Governing Council of the ECB:\s*"
            r"(?P<title>.*?)(?=\n\d{2}/\d{2}/\d{4}|\nGeneral Council|\Z)",
            re.I | re.DOTALL,
        )
        events: list[CalendarEvent] = []
        for match in pattern.finditer(text):
            description = " ".join(match.group("title").split())
            lower = description.casefold()
            if "non-monetary" in lower or "monetary policy" not in lower or "day 1" in lower:
                continue
            local_date = datetime.strptime(match.group("date"), "%d/%m/%Y")
            dt = _utc(local_date.replace(hour=12), "Europe/Paris")
            if _in_window(dt, days_ahead):
                events.append(_event(
                    dt, "ECB monetary policy decision and press conference", "European Central Bank",
                    ECB_CALENDAR, "Euro Area", "High", "Central bank", time_tbc=True,
                ))
        return events, None if events else "ECB: no upcoming monetary-policy meeting in window"
    except Exception as exc:
        return [], f"ECB unavailable ({type(exc).__name__})"


def fetch_boe_events(days_ahead: int = 180) -> tuple[list[CalendarEvent], str | None]:
    """Load official Bank of England MPC announcement dates."""
    try:
        text = _html_text(_request_text(BOE_CALENDAR))
        now = datetime.now(timezone.utc)
        events: list[CalendarEvent] = []
        pattern = re.compile(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday)\s+(?P<day>\d{1,2})\s+"
            r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)",
            re.I,
        )
        for year in (now.year, now.year + 1):
            marker = re.search(rf"{year}\s+confirmed dates", text, re.I)
            if not marker:
                continue
            following = text[marker.end():]
            next_heading = re.search(r"20\d{2}\s+confirmed dates", following, re.I)
            section = following[:next_heading.start()] if next_heading else following[:4000]
            for match in pattern.finditer(section):
                local = datetime.strptime(
                    f"{match.group('day')} {match.group('month')} {year} 12:00", "%d %B %Y %H:%M",
                )
                dt = _utc(local, "Europe/London")
                if _in_window(dt, days_ahead):
                    events.append(_event(
                        dt, "Bank of England MPC decision", "Bank of England", BOE_CALENDAR,
                        "United Kingdom", "High", "Central bank",
                    ))
        return events, None if events else "Bank of England: no upcoming MPC meeting in window"
    except Exception as exc:
        return [], f"Bank of England unavailable ({type(exc).__name__})"


def _deduplicate(events: list[CalendarEvent]) -> list[CalendarEvent]:
    seen: set[tuple[str, str, str]] = set()
    output: list[CalendarEvent] = []
    for event in events:
        key = (
            event.date,
            event.institution.casefold(),
            re.sub(r"\W+", " ", event.title.casefold()).strip(),
        )
        if key not in seen:
            seen.add(key)
            output.append(event)
    return output


def _sort_key(event: CalendarEvent) -> tuple[str, str, str]:
    time_key = event.time if re.match(r"^\d{2}:\d{2}", event.time) else "99:99"
    return event.date, time_key, event.title.casefold()


def load_calendar(days_ahead: int = 90, limit: int = 80) -> tuple[list[CalendarEvent], str | None]:
    """Load independent official sources concurrently and retain partial results."""
    cache_key = f"calendar:{days_ahead}:{limit}"
    with _CALENDAR_CACHE_LOCK:
        cached = _CALENDAR_CACHE.get(cache_key)
    if cached is not None:
        events, status = cached
        return list(events), status

    collectors = (
        fetch_bls_events,
        fetch_bea_events,
        fetch_fomc_events,
        fetch_ecb_events,
        fetch_boe_events,
    )
    events: list[CalendarEvent] = []
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=len(collectors), thread_name_prefix="calendar") as pool:
        futures = [(collector.__name__, pool.submit(collector, days_ahead)) for collector in collectors]
        for name, future in futures:
            try:
                source_events, warning = future.result()
            except Exception as exc:
                source_events, warning = [], f"{name} unavailable ({type(exc).__name__})"
            events.extend(source_events)
            if warning:
                warnings.append(warning)

    events = sorted(_deduplicate(events), key=_sort_key)[:limit]
    if not events:
        status = "Calendar unavailable. " + "; ".join(warnings)
    elif warnings:
        status = "Partial calendar: " + "; ".join(warnings)
    else:
        status = None

    result = (events, status)
    with _CALENDAR_CACHE_LOCK:
        _CALENDAR_CACHE[cache_key] = result
    return list(events), status


def filter_events(
    events: list[CalendarEvent], region: str = "All", importance: str = "All", event_type: str = "All",
    date_from: str = "", date_to: str = "",
) -> list[CalendarEvent]:
    return [event for event in events if (
        (region == "All" or event.region == region)
        and (importance == "All" or event.importance == importance)
        and (event_type == "All" or event.event_type == event_type)
        and (not date_from.strip() or event.date >= date_from.strip())
        and (not date_to.strip() or event.date <= date_to.strip())
    )]
