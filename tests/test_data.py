from datetime import datetime, timedelta, timezone
import io
import subprocess
import zipfile

import pandas as pd
import requests

from src.data import SERIES, FredCollector, LiveMarketCollector, MarketDataCollector, TreasuryCollector, load_snapshot
from tests.sample_data import sample_points


def test_live_snapshot_uses_collector_without_credentials(monkeypatch):
    collector = FredCollector()
    monkeypatch.setattr(collector, "fetch_all", sample_points)
    points, status = load_snapshot(collector)
    assert len(points) == len(SERIES)
    assert status["available"] == len(SERIES)
    assert all(point.status == "latest available" for point in points)


def test_source_failure_returns_unavailable(monkeypatch):
    collector = FredCollector(timeout=3)

    def fail(*args, **kwargs):
        raise requests.Timeout("synthetic timeout")

    monkeypatch.setattr(collector.session, "get", fail)
    monkeypatch.setattr(collector.fallback_session, "get", fail)
    point = collector.fetch(SERIES[0])
    assert point.value is None
    assert point.status == "unavailable"
    assert "synthetic" not in (point.error or "").lower()  # exception details are not surfaced


def test_fred_official_api_is_used_when_public_csv_fails(monkeypatch):
    collector = FredCollector(timeout=3)
    collector.api_key = "test-key"

    class FailedResponse:
        status_code = 503

        def raise_for_status(self):
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError("public CSV unavailable", response=response)

    class ApiResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"observations": [
                {"date": "2026-09-17", "value": "4.70"},
                {"date": "2026-09-18", "value": "4.76"},
            ]}

    def route(url, *args, **kwargs):
        return ApiResponse() if url == collector.api_url else FailedResponse()

    monkeypatch.setattr(collector.session, "get", route)
    point = collector.fetch(SERIES[0])
    assert point.value == 4.76
    assert point.previous == 4.70
    assert point.source_name == "FRED official API"


def test_dbnomics_is_used_when_fred_is_unreachable(monkeypatch):
    collector = FredCollector(timeout=3)

    def fred_timeout(*args, **kwargs):
        raise requests.Timeout("FRED blocked")

    class MirrorResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"series": {"docs": [{
                "period": ["2026-09-17", "2026-09-18"],
                "value": [4.70, 4.76],
            }]}}

    monkeypatch.setattr(collector.session, "get", fred_timeout)
    monkeypatch.setattr(collector.fallback_session, "get", lambda *args, **kwargs: MirrorResponse())
    point = collector.fetch(SERIES[0])
    assert point.value == 4.76
    assert point.previous == 4.70
    assert point.source_name == "DBnomics mirror of FRED"


def test_failed_connectivity_probe_stops_full_request_wave(monkeypatch):
    collector = FredCollector(timeout=3)
    calls = {"fred": 0, "mirror": 0}

    def fred_timeout(*args, **kwargs):
        calls["fred"] += 1
        raise requests.Timeout("FRED blocked")

    def mirror_timeout(*args, **kwargs):
        calls["mirror"] += 1
        raise requests.Timeout("mirror blocked")

    monkeypatch.setattr(collector.session, "get", fred_timeout)
    monkeypatch.setattr(collector.fallback_session, "get", mirror_timeout)
    points = collector.fetch_all()
    assert calls == {"fred": 1, "mirror": 1}
    assert len(points) == len(SERIES)
    assert all(point.value is None for point in points)


def test_windows_guard_converts_hard_timeout_to_unavailable(monkeypatch):
    collector = FredCollector(timeout=3)

    def hard_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=20)

    monkeypatch.setattr("src.data.subprocess.run", hard_timeout)
    points = collector._fetch_all_windows_guarded()
    assert len(points) == len(SERIES)
    assert all(point.value is None for point in points)
    assert all("deadline exceeded" in (point.error or "") for point in points)


def test_windows_native_curl_uses_real_fred_csv(monkeypatch):
    collector = FredCollector(timeout=3)
    frame = pd.DataFrame({
        "observation_date": ["2026-09-17", "2026-09-18"],
        "DGS2": [4.70, 4.76],
    })
    monkeypatch.setattr(collector, "_windows_curl", lambda url: frame.to_csv(index=False).encode())
    point = collector._fetch_windows_fred(SERIES[0])
    assert point is not None
    assert point.value == 4.76
    assert point.previous == 4.70
    assert point.source_name == "FRED"


def test_windows_native_snapshot_avoids_python_requests(monkeypatch):
    collector = FredCollector(timeout=3)
    points_by_id = {point.series_id: point for point in sample_points()}
    monkeypatch.setattr("src.data.shutil.which", lambda name: "C:\\Windows\\System32\\curl.exe")
    monkeypatch.setattr(collector, "_fetch_windows_fred", lambda spec: points_by_id[spec.series_id])

    def python_requests_must_not_run(*args, **kwargs):
        raise AssertionError("Windows snapshot should use native curl, not requests")

    monkeypatch.setattr(collector.session, "get", python_requests_must_not_run)
    points = collector._fetch_all_windows_native()
    assert len(points) == len(SERIES)
    assert all(point.value is not None for point in points)


def test_windows_curl_matches_validated_minimal_command(monkeypatch):
    collector = FredCollector(timeout=3)
    captured = {}
    executable = r"C:\Windows\System32\curl.exe"

    monkeypatch.setattr("src.data.shutil.which", lambda name: executable)

    def run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=b"csv", stderr=b"")

    monkeypatch.setattr("src.data.subprocess.run", run)
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
    assert collector._windows_curl(url) == b"csv"
    assert captured["command"] == [
        executable,
        "--fail",
        "--show-error",
        "--location",
        "--connect-timeout",
        "5",
        "--max-time",
        "30",
        url,
    ]


def test_windows_fred_url_has_only_the_validated_series_id(monkeypatch):
    collector = FredCollector(timeout=3)
    frame = pd.DataFrame({
        "observation_date": ["2026-09-17", "2026-09-18"],
        "DGS10": [4.70, 4.76],
    })
    captured = {}

    def curl_response(url):
        captured["url"] = url
        return frame.to_csv(index=False).encode()

    monkeypatch.setattr(collector, "_windows_curl", curl_response)
    point = collector._fetch_windows_fred(next(spec for spec in SERIES if spec.series_id == "DGS10"))
    assert point is not None
    assert captured["url"] == "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"


def test_windows_history_uses_single_series_native_curl(monkeypatch):
    collector = FredCollector(timeout=3)
    captured = []

    def curl_response(url):
        captured.append(url)
        series_id = url.rsplit("=", 1)[-1]
        return pd.DataFrame({
            "observation_date": ["2026-09-17", "2026-09-18"],
            series_id: [4.70, 4.76],
        }).to_csv(index=False).encode()

    monkeypatch.setattr("src.data.os.name", "nt")
    monkeypatch.setattr("src.data.shutil.which", lambda name: r"C:\Windows\System32\curl.exe")
    monkeypatch.setattr(collector, "_windows_curl", curl_response)
    frame, errors = collector.fetch_history(["DGS2", "DGS10"], 3650)
    assert not errors
    assert list(frame.columns) == ["Date", "US Treasury 2Y", "US Treasury 10Y"]
    assert sorted(captured) == [
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2",
    ]


def test_snapshot_uses_parallel_single_series_requests(monkeypatch):
    collector = FredCollector(timeout=3)
    frame = pd.DataFrame({"observation_date": ["2026-09-17", "2026-09-18"]})
    for index, spec in enumerate(SERIES):
        frame[spec.series_id] = [float(index), float(index + 1)]

    class Response:
        text = frame.to_csv(index=False)

        @staticmethod
        def raise_for_status():
            return None

    calls = []

    def success(*args, **kwargs):
        calls.append(kwargs["params"])
        return Response()

    monkeypatch.setattr(collector.session, "get", success)
    points = collector.fetch_all()
    assert len(calls) == len(SERIES)
    assert all("," not in call["id"] for call in calls)
    assert len(points) == len(SERIES)
    assert all(point.value is not None for point in points)


def test_fred_multi_frequency_zip_is_parsed(monkeypatch):
    collector = FredCollector(timeout=3)
    daily = pd.DataFrame({
        "observation_date": ["2026-09-17", "2026-09-18"],
        "DGS2": [4.70, 4.76],
    }).to_csv(index=False).encode()
    monthly = pd.DataFrame({
        "observation_date": ["2026-07-01", "2026-08-01"],
        "IRLTLT01DEM156N": [3.10, 3.18],
    }).to_csv(index=False).encode()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("README.txt", "FRED Graph Observations")
        archive.writestr("daily.csv", daily)
        archive.writestr("monthly.csv", monthly)

    class Response:
        content = payload.getvalue()
        headers = {"content-type": "application/zip"}

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(collector.session, "get", lambda *args, **kwargs: Response())
    points = collector._fetch_batch([SERIES[0], SERIES[4]])
    assert points["DGS2"].value == 4.76
    assert points["DGS2"].previous == 4.70
    assert points["IRLTLT01DEM156N"].value == 3.18


def test_fred_404_batch_retries_valid_series_individually(monkeypatch):
    collector = FredCollector(timeout=3)
    frame = pd.DataFrame({"observation_date": ["2026-09-17", "2026-09-18"]})
    for index, spec in enumerate(SERIES[:2]):
        frame[spec.series_id] = [float(index), float(index + 1)]

    class BatchResponse:
        status_code = 404

        def raise_for_status(self):
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError("batch contains retired series", response=response)

    class SingleResponse:
        text = frame.to_csv(index=False)

        @staticmethod
        def raise_for_status():
            return None

    def batch_fails(*args, **kwargs):
        return BatchResponse() if "," in kwargs["params"]["id"] else SingleResponse()

    monkeypatch.setattr(collector.session, "get", batch_fails)
    points = collector._fetch_batch(list(SERIES[:2]))
    assert points["DGS2"].value == 1.0
    assert points["DGS5"].value == 2.0


def test_one_failed_fred_series_does_not_blank_dashboard(monkeypatch):
    collector = FredCollector(timeout=3)
    frame = pd.DataFrame({"observation_date": ["2026-09-17", "2026-09-18"]})
    for index, spec in enumerate(SERIES):
        frame[spec.series_id] = [float(index), float(index + 1)]

    class Response:
        text = frame.to_csv(index=False)

        @staticmethod
        def raise_for_status():
            return None

    def partly_available(*args, **kwargs):
        if kwargs["params"]["id"] == "DGS2":
            raise requests.Timeout("one series failed")
        return Response()

    monkeypatch.setattr(collector.session, "get", partly_available)
    monkeypatch.setattr(collector.fallback_session, "get", partly_available)
    points = collector.fetch_all()
    assert sum(point.value is not None for point in points) == len(SERIES) - 1
    assert sum(point.status == "unavailable" for point in points) == 1
    assert next(point for point in points if point.series_id == "DGS2").error == "FRED request timed out: data source unavailable"


def test_treasury_uses_latest_two_available_sessions(monkeypatch):
    collector = TreasuryCollector(timeout=3)
    today = datetime.now(timezone.utc).date()
    previous = today - timedelta(days=3)
    xml = f"""<?xml version="1.0"?>
    <feed xmlns:d="urn:data" xmlns:m="urn:meta">
      <entry><content><m:properties><d:NEW_DATE>{previous.isoformat()}T00:00:00</d:NEW_DATE><d:BC_2YEAR>4.01</d:BC_2YEAR><d:BC_5YEAR>4.10</d:BC_5YEAR><d:BC_10YEAR>4.20</d:BC_10YEAR><d:BC_30YEAR>4.40</d:BC_30YEAR></m:properties></content></entry>
      <entry><content><m:properties><d:NEW_DATE>{today.isoformat()}T00:00:00</d:NEW_DATE><d:BC_2YEAR>4.05</d:BC_2YEAR><d:BC_5YEAR>4.12</d:BC_5YEAR><d:BC_10YEAR>4.25</d:BC_10YEAR><d:BC_30YEAR>4.42</d:BC_30YEAR></m:properties></content></entry>
    </feed>"""

    class Response:
        text = xml

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(collector.session, "get", lambda *args, **kwargs: Response())
    points = collector.fetch_all()
    assert points["DGS2"].value == 4.05
    assert points["DGS2"].previous == 4.01
    assert points["DGS2"].previous_date == previous.isoformat()
    assert points["DGS2"].status == "official close"


def test_live_quote_compares_intraday_with_previous_close(monkeypatch):
    collector = LiveMarketCollector(timeout=3)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    prior = now - timedelta(days=1)
    payload = {
        "chart": {"result": [{
            "meta": {"regularMarketPrice": 102.0, "regularMarketTime": int(now.timestamp()), "chartPreviousClose": 100.0},
            "timestamp": [int(prior.timestamp()), int(now.timestamp())],
            "indicators": {"quote": [{"close": [100.0, 102.0]}]},
        }]}
    }

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr(collector.session, "get", lambda *args, **kwargs: Response())
    point = collector._fetch_one("SP500", "^GSPC")
    assert point is not None
    assert point.value == 102.0
    assert point.previous == 100.0
    assert point.change == 2.0
    assert point.status == "live intraday"


def test_composite_prefers_live_and_keeps_fred_fallback():
    collector = MarketDataCollector()
    base = sample_points()
    live_point = next(point for point in sample_points() if point.series_id == "SP500")
    live_point.value = 6000.0
    live_point.status = "live intraday"

    class Provider:
        def __init__(self, result):
            self.result = result

        def fetch_all(self):
            return self.result

    collector.fred = Provider(base)
    collector.treasury = Provider({})
    collector.live = Provider({"SP500": live_point})
    points = {point.series_id: point for point in collector.fetch_all()}
    assert points["SP500"].value == 6000.0
    assert points["SP500"].status == "live intraday"
    assert points["CPIAUCSL"].value is not None
