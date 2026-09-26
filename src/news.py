from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from cachetools import TTLCache
import feedparser
import requests

from .config import settings
from .models import NewsItem
from .security import safe_text, safe_url

OFFICIAL_FEEDS = (
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("Federal Reserve speeches", "https://www.federalreserve.gov/feeds/s_t_all.xml"),
    ("European Central Bank", "https://www.ecb.europa.eu/rss/press.html"),
    ("Bank of England", "https://www.bankofengland.co.uk/rss/news"),
    ("Bank of Japan", "https://www.boj.or.jp/en/rss/whatsnew.xml"),
)

_NEWS_CACHE: TTLCache = TTLCache(maxsize=1, ttl=settings.cache_ttl)
_NEWS_CACHE_LOCK = threading.Lock()


def _classify_title(title: str, source: str) -> str:
    lowered = title.lower()
    if any(term in lowered for term in ("interest rate", "federal funds", "monetary policy", "key rates")):
        return "Monetary-policy decision"
    if any(term in lowered for term in ("inflation", "consumer price", "employment", "payroll")):
        return "Macro release"
    if "speech" in source.lower() or any(term in lowered for term in ("remarks", "speech", "address")):
        return "Central-bank commentary"
    return "Official release"


def _published(entry: object) -> str:
    raw = entry.get("published") or entry.get("updated") or ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError):
        return "time unavailable"


def fetch_official_news(max_items: int | None = None) -> tuple[list[NewsItem], list[str]]:
    limit = min(max_items or settings.max_news_items, 20)
    with _NEWS_CACHE_LOCK:
        cached = _NEWS_CACHE.get("official")
    if cached is not None:
        items, errors = cached
        return list(items[:limit]), list(errors)

    items: list[NewsItem] = []
    errors: list[str] = []
    headers = {"User-Agent": "AI-Sales-Desk-Assistant/2.1 (public market research)"}

    def fetch_feed(source: str, url: str) -> tuple[list[NewsItem], str | None]:
        try:
            response = requests.get(url, timeout=min(settings.request_timeout, 6), headers=headers)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            feed_items: list[NewsItem] = []
            for entry in parsed.entries[:limit]:
                link = safe_url(entry.get("link", ""))
                if not link:
                    continue
                title = safe_text(entry.get("title", "Untitled official release"), 220)
                feed_items.append(NewsItem(title, source, _published(entry), link, _classify_title(title, source)))
            return feed_items, None
        except (requests.RequestException, ValueError) as exc:
            return [], f"{source}: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=len(OFFICIAL_FEEDS)) as pool:
        futures = {pool.submit(fetch_feed, source, url): source for source, url in OFFICIAL_FEEDS}
        for future in as_completed(futures):
            feed_items, error = future.result()
            items.extend(feed_items)
            if error:
                errors.append(error)

    deduplicated: dict[str, NewsItem] = {}
    for item in items:
        key = " ".join(item.title.lower().split())[:120]
        deduplicated.setdefault(key, item)
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: item.published if item.published != "time unavailable" else "",
        reverse=True,
    )
    result = (ordered[: settings.max_news_items], errors)
    if result[0]:
        with _NEWS_CACHE_LOCK:
            _NEWS_CACHE["official"] = result
    return list(result[0][:limit]), list(result[1])


def load_news() -> tuple[list[NewsItem], list[str]]:
    items, errors = fetch_official_news()
    return items, errors
