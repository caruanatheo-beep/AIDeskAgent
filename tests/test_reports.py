from pathlib import Path

from src.reports import build_pdf, email_ready, report_sources
from tests.sample_data import SAMPLE_EVENTS, SAMPLE_NEWS, sample_points


def test_pdf_and_email_report_generation():
    body = "## Verified facts\n\n- US 10Y was observed at 4.20%.\n\nSources: FRED"
    path = Path(build_pdf("Test Market Brief", body, "FRED — https://fred.stlouisfed.org/series/DGS10"))
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
    email = email_ready("Test Market Brief", body)
    assert "Suggested subject" in email
    assert "not sent automatically" in email


def test_pdf_can_include_selected_chart():
    path = Path(build_pdf(
        "Chart Report", "Observed data", "FRED",
        {"Date": ["2024-01-01", "2024-01-02"], "US Treasury 10Y": [4.1, 4.2]},
    ))
    assert path.exists()
    assert path.stat().st_size > 1000


def test_professional_pdf_contains_market_sections():
    from pypdf import PdfReader

    points = sample_points()
    path = Path(build_pdf(
        "Professional Desk Report",
        "## Executive summary\n\n- Rates moved lower across the curve.",
        report_sources(points, SAMPLE_EVENTS, SAMPLE_NEWS),
        {"Date": ["2026-09-17", "2026-09-18"], "US Treasury 10Y": [4.24, 4.20]},
        points,
        SAMPLE_EVENTS,
        SAMPLE_NEWS,
    ))
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Market snapshot" in text
    assert "US Treasury curve" in text
    assert "Upcoming official events" in text
    assert "Sources and controls" in text
