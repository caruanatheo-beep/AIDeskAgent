from src.data import SERIES
from src.models import CalendarEvent, MarketPoint, NewsItem


VALUES = {
    "DGS2": (4.67, 4.68), "DGS5": (4.22, 4.24), "DGS10": (4.20, 4.24),
    "DGS30": (4.34, 4.40), "IRLTLT01DEM156N": (2.48, 2.52),
    "DEXUSEU": (1.0699, 1.0756), "DEXUSUK": (1.2685, 1.2764),
    "DEXJPUS": (157.28, 156.77), "DTWEXBGS": (124.17, 124.02),
    "SP500": (5431.60, 5433.74), "VIXCLS": (12.66, 11.94),
    "DCOILWTICO": (79.41, 79.61), "DCOILBRENTEU": (82.21, 82.12),
    "GOLDAMGBD228NLBM": (2331.45, 2304.00), "NASDAQCOM": (17688.88, 17667.56),
    "NIKKEI225": (38814.56, 38720.47), "CPIAUCSL": (313.04, 313.18),
    "UNRATE": (4.1, 3.9), "PAYEMS": (157695.0, 157608.0),
    "INDPRO": (102.03, 102.46), "EFFR": (5.33, 5.33), "ECBDFR": (3.75, 3.75),
    "BAMLC0A0CM": (0.95, 0.92), "BAMLH0A0HYM2": (3.29, 3.20),
}


def sample_points() -> list[MarketPoint]:
    points = []
    for spec in SERIES:
        value, previous = VALUES[spec.series_id]
        points.append(MarketPoint(
            spec.series_id, spec.label, spec.category, value, previous, value - previous,
            "2026-09-18", spec.unit, spec.frequency, "latest available", spec.source_name, spec.source_url,
        ))
    return points


SAMPLE_NEWS = [
    NewsItem("Federal Reserve policy update", "Federal Reserve", "2026-09-18 18:00 UTC", "https://www.federalreserve.gov/"),
    NewsItem("ECB policy update", "European Central Bank", "2026-09-18 12:00 UTC", "https://www.ecb.europa.eu/"),
]

SAMPLE_EVENTS = [
    CalendarEvent("2026-09-22", "12:30 UTC", "US CPI", "US Bureau of Labor Statistics", "https://www.bls.gov/schedule/news_release/", region="United States", importance="High", event_type="Inflation"),
    CalendarEvent("2026-09-23", "11:00 UTC", "Bank Rate decision", "Bank of England", "https://www.bankofengland.co.uk/", region="United Kingdom", importance="High", event_type="Central bank"),
]
