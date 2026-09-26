from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import pandas as pd
import requests
from cachetools import TTLCache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import settings
from .models import MarketPoint, SeriesSpec

SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("DGS2", "US Treasury 2Y", "Rates", "%", "daily"),
    SeriesSpec("DGS5", "US Treasury 5Y", "Rates", "%", "daily"),
    SeriesSpec("DGS10", "US Treasury 10Y", "Rates", "%", "daily"),
    SeriesSpec("DGS30", "US Treasury 30Y", "Rates", "%", "daily"),
    SeriesSpec("IRLTLT01DEM156N", "Germany 10Y benchmark", "Rates", "%", "monthly"),
    SeriesSpec("DEXUSEU", "EUR/USD", "FX", "USD per EUR", "daily", 4),
    SeriesSpec("DEXUSUK", "GBP/USD", "FX", "USD per GBP", "daily", 4),
    SeriesSpec("DEXJPUS", "USD/JPY", "FX", "JPY per USD", "daily", 2),
    SeriesSpec("DTWEXBGS", "Trade-weighted US dollar", "FX", "index", "daily", 2),
    SeriesSpec("SP500", "S&P 500", "Risk", "index", "daily", 2),
    SeriesSpec("VIXCLS", "VIX", "Risk", "index", "daily", 2),
    SeriesSpec("DCOILWTICO", "WTI crude oil", "Commodities", "USD/bbl", "daily", 2),
    SeriesSpec("DCOILBRENTEU", "Brent crude oil", "Commodities", "USD/bbl", "daily", 2),
    SeriesSpec("GOLDAMGBD228NLBM", "Gold fixing", "Commodities", "USD/oz", "daily", 2),
    SeriesSpec("NASDAQCOM", "NASDAQ Composite", "Risk", "index", "daily", 2),
    SeriesSpec("NIKKEI225", "Nikkei 225", "Risk", "index", "daily", 2),
    SeriesSpec("CPIAUCSL", "US CPI", "Macro", "index", "monthly", 3),
    SeriesSpec("UNRATE", "US unemployment rate", "Macro", "%", "monthly", 1),
    SeriesSpec("PAYEMS", "US nonfarm payrolls", "Macro", "thousand", "monthly", 0),
    SeriesSpec("INDPRO", "US industrial production", "Macro", "index", "monthly", 2),
    SeriesSpec("EFFR", "Effective federal funds rate", "Policy", "%", "daily"),
    SeriesSpec("ECBDFR", "ECB deposit facility rate", "Policy", "%", "daily"),
    SeriesSpec("BAMLC0A0CM", "US investment-grade spread", "Credit", "%", "daily"),
    SeriesSpec("BAMLH0A0HYM2", "US high-yield spread", "Credit", "%", "daily"),
)

SERIES_BY_ID = {spec.series_id: spec for spec in SERIES}
FRED_BATCH_SIZE = 5
FRED_SNAPSHOT_WORKERS = 6
FRED_LOOKBACK_DAYS = 450
FRED_CONNECT_TIMEOUT_SECONDS = 5
FRED_READ_TIMEOUT_SECONDS = 25
FRED_WINDOWS_TOTAL_TIMEOUT_SECONDS = 20

# Intraday/last-market-price layer for instruments where a public quote exists.
# The FRED observation remains the fallback if a quote is temporarily unavailable.
LIVE_SYMBOLS: dict[str, str] = {
    "DEXUSEU": "EURUSD=X",
    "DEXUSUK": "GBPUSD=X",
    "DEXJPUS": "JPY=X",
    "SP500": "^GSPC",
    "VIXCLS": "^VIX",
    "DCOILWTICO": "CL=F",
    "DCOILBRENTEU": "BZ=F",
    "GOLDAMGBD228NLBM": "GC=F",
    "NASDAQCOM": "^IXIC",
    "NIKKEI225": "^N225",
}

TREASURY_FIELDS = {
    "DGS2": "BC_2YEAR",
    "DGS5": "BC_5YEAR",
    "DGS10": "BC_10YEAR",
    "DGS30": "BC_30YEAR",
}


class FredCollector:
    """Small, bounded FRED CSV collector; no API key is required."""

    base_url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    api_url = "https://api.stlouisfed.org/fred/series/observations"
    dbnomics_url = "https://api.db.nomics.world/v22/series/FRED"

    def __init__(self, timeout: int | None = None, cache_ttl: int | None = None):
        self.timeout = timeout or settings.request_timeout
        self.api_key = settings.fred_api_key
        self.cache = TTLCache(maxsize=64, ttl=cache_ttl or settings.cache_ttl)
        self._lock = threading.Lock()
        self.session = requests.Session()
        # A connectivity probe and provider fallback replace long retry chains.
        retries = Retry(total=0, connect=0, read=0, redirect=2)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({
            "User-Agent": "AI-Sales-Desk-Assistant/2.4 (public market research)",
            "Accept": "text/csv,application/json,application/zip;q=0.9,*/*;q=0.8",
        })
        self.fallback_session = requests.Session()
        self.fallback_session.mount("https://", HTTPAdapter(max_retries=Retry(total=0, redirect=2)))
        self.fallback_session.headers.update({
            "User-Agent": "AI-Sales-Desk-Assistant/2.4.1 (public market research)",
            "Accept": "application/json,*/*;q=0.8",
        })

    def _timeout(self) -> tuple[int, int]:
        return (
            min(FRED_CONNECT_TIMEOUT_SECONDS, self.timeout),
            min(FRED_READ_TIMEOUT_SECONDS, max(3, self.timeout)),
        )

    @staticmethod
    def _status(observation_date: date, frequency: str) -> str:
        age = (datetime.now(timezone.utc).date() - observation_date).days
        threshold = 10 if frequency == "daily" else 75
        return "latest available" if age <= threshold else "stale"

    @staticmethod
    def _date_column(frame: pd.DataFrame) -> str:
        for column in frame.columns:
            if str(column).casefold() in {"observation_date", "date"}:
                return str(column)
        raise ValueError("Observation date column missing")

    @staticmethod
    def _response_frames(response: requests.Response) -> list[pd.DataFrame]:
        """Parse FRED's single-series CSV or multi-series ZIP response."""
        content = getattr(response, "content", None)
        if content is None:
            content = response.text.encode("utf-8")
        content_type = str(getattr(response, "headers", {}).get("content-type", "")).casefold()
        return FredCollector._content_frames(content, content_type)

    @staticmethod
    def _content_frames(content: bytes, content_type: str = "") -> list[pd.DataFrame]:
        if content.startswith(b"PK\x03\x04") or "application/zip" in content_type:
            frames: list[pd.DataFrame] = []
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for name in archive.namelist():
                    if name.casefold().endswith(".csv"):
                        frames.append(pd.read_csv(io.BytesIO(archive.read(name))))
            if not frames:
                raise ValueError("FRED ZIP response contains no CSV file")
            return frames
        return [pd.read_csv(io.BytesIO(content))]

    def _point_from_frames(self, frames: list[pd.DataFrame], spec: SeriesSpec) -> MarketPoint:
        for frame in frames:
            if spec.series_id in frame.columns:
                return self._point_from_frame(frame, spec)
        raise ValueError("Series column missing from FRED response")

    def _point_from_frame(self, frame: pd.DataFrame, spec: SeriesSpec) -> MarketPoint:
        if spec.series_id not in frame.columns:
            raise ValueError("Series column missing from response")
        date_column = self._date_column(frame)
        series = frame[[date_column, spec.series_id]].copy()
        series[spec.series_id] = pd.to_numeric(series[spec.series_id], errors="coerce")
        series[date_column] = pd.to_datetime(series[date_column], errors="coerce")
        series = series.dropna(subset=[date_column, spec.series_id]).sort_values(date_column)
        if len(series) < 2:
            raise ValueError("Fewer than two observations available")
        latest, previous = series.iloc[-1], series.iloc[-2]
        obs_date = latest[date_column].date()
        previous_date = previous[date_column].date()
        return MarketPoint(
            series_id=spec.series_id,
            label=spec.label,
            category=spec.category,
            value=float(latest[spec.series_id]),
            previous=float(previous[spec.series_id]),
            change=float(latest[spec.series_id] - previous[spec.series_id]),
            date=obs_date.isoformat(),
            unit=spec.unit,
            frequency=spec.frequency,
            status=self._status(obs_date, spec.frequency),
            source_name=spec.source_name,
            source_url=spec.source_url,
            previous_date=previous_date.isoformat(),
        )

    def _fetch_official_api(self, spec: SeriesSpec, start: date) -> MarketPoint | None:
        """Use the documented FRED API when the optional key is configured."""
        if not self.api_key:
            return None
        try:
            response = self.session.get(
                self.api_url,
                params={
                    "series_id": spec.series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "observation_start": start.isoformat(),
                    "sort_order": "asc",
                },
                timeout=self._timeout(),
            )
            response.raise_for_status()
            observations = response.json().get("observations", [])
            frame = pd.DataFrame({
                "observation_date": [item.get("date") for item in observations],
                spec.series_id: [item.get("value") for item in observations],
            })
            point = self._point_from_frame(frame, spec)
            point.source_name = "FRED official API"
            return point
        except (requests.RequestException, ValueError, KeyError, TypeError, pd.errors.ParserError):
            return None

    def _fetch_dbnomics(self, spec: SeriesSpec) -> MarketPoint | None:
        """Fetch DBnomics' public mirror when the FRED hosts are unreachable."""
        try:
            response = self.fallback_session.get(
                f"{self.dbnomics_url}/{quote(spec.series_id, safe='')}",
                params={"observations": "1"},
                timeout=self._timeout(),
            )
            response.raise_for_status()
            docs = response.json()["series"]["docs"]
            document = docs[0]
            frame = pd.DataFrame({
                "observation_date": document["period"],
                spec.series_id: document["value"],
            })
            point = self._point_from_frame(frame, spec)
            point.source_name = "DBnomics mirror of FRED"
            point.source_url = f"{self.dbnomics_url}/{quote(spec.series_id, safe='')}?observations=1"
            return point
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, pd.errors.ParserError):
            return None

    def _fallback_point(self, spec: SeriesSpec, start: date) -> MarketPoint | None:
        return self._fetch_official_api(spec, start) or self._fetch_dbnomics(spec)

    def _windows_curl(
        self,
        url: str,
        *,
        max_time: int = FRED_CONNECT_TIMEOUT_SECONDS + FRED_READ_TIMEOUT_SECONDS,
    ) -> bytes | None:
        """Run the minimal curl.exe request proven to work on the target PC."""
        executable = shutil.which("curl.exe")
        if not executable:
            return None
        try:
            command = [
                executable,
                "--fail",
                "--show-error",
                "--location",
                "--connect-timeout",
                str(FRED_CONNECT_TIMEOUT_SECONDS),
                "--max-time",
                str(max_time),
                url,
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=max_time + 2,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout if result.returncode == 0 and result.stdout else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _fetch_windows_fred(self, spec: SeriesSpec) -> MarketPoint | None:
        # Deliberately match the command validated on the user's Windows PC:
        # fredgraph.csv?id=DGS10. Adding a custom UA, compressed transfer,
        # cookie reuse or even a grouped query can trigger the CDN challenge.
        url = f"{self.base_url}?{urlencode({'id': spec.series_id})}"
        content = self._windows_curl(url)
        if content is None:
            return None
        try:
            point = self._point_from_frame(pd.read_csv(io.BytesIO(content)), spec)
            point.source_name = "FRED"
            return point
        except (ValueError, KeyError, pd.errors.ParserError):
            return None

    def _fetch_windows_dbnomics(self, spec: SeriesSpec) -> MarketPoint | None:
        url = f"{self.dbnomics_url}/{quote(spec.series_id, safe='')}?observations=1"
        content = self._windows_curl(url)
        if content is None:
            return None
        try:
            document = json.loads(content.decode("utf-8"))["series"]["docs"][0]
            frame = pd.DataFrame({
                "observation_date": document["period"],
                spec.series_id: document["value"],
            })
            point = self._point_from_frame(frame, spec)
            point.source_name = "DBnomics mirror of FRED"
            point.source_url = url
            return point
        except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError, pd.errors.ParserError):
            return None

    def fetch(self, spec: SeriesSpec) -> MarketPoint:
        with self._lock:
            cached = self.cache.get(spec.series_id)
        if cached is not None:
            return MarketPoint(**cached.to_dict())

        start = datetime.now(timezone.utc).date() - pd.Timedelta(days=FRED_LOOKBACK_DAYS)
        params = {"id": spec.series_id, "cosd": start.isoformat()}
        try:
            response = self.session.get(
                self.base_url,
                params=params,
                timeout=self._timeout(),
            )
            response.raise_for_status()
            point = self._point_from_frames(self._response_frames(response), spec)
            with self._lock:
                self.cache[spec.series_id] = point
            return point
        except requests.HTTPError as exc:
            fallback_point = self._fallback_point(spec, start)
            if fallback_point is not None:
                with self._lock:
                    self.cache[spec.series_id] = fallback_point
                return fallback_point
            status_code = getattr(exc.response, "status_code", None)
            return self._unavailable(spec, f"FRED HTTP {status_code or 'error'}")
        except requests.Timeout:
            fallback_point = self._fallback_point(spec, start)
            if fallback_point is not None:
                with self._lock:
                    self.cache[spec.series_id] = fallback_point
                return fallback_point
            return self._unavailable(spec, "FRED request timed out")
        except requests.ConnectionError:
            fallback_point = self._fallback_point(spec, start)
            if fallback_point is not None:
                with self._lock:
                    self.cache[spec.series_id] = fallback_point
                return fallback_point
            return self._unavailable(spec, "FRED connection failed")
        except (requests.RequestException, ValueError, KeyError, pd.errors.ParserError, zipfile.BadZipFile) as exc:
            fallback_point = self._fallback_point(spec, start)
            if fallback_point is not None:
                with self._lock:
                    self.cache[spec.series_id] = fallback_point
                return fallback_point
            return self._unavailable(spec, f"FRED {type(exc).__name__}")

    def _fetch_batch(self, specs: list[SeriesSpec]) -> dict[str, MarketPoint]:
        start = datetime.now(timezone.utc).date() - pd.Timedelta(days=1100)
        try:
            response = self.session.get(
                self.base_url,
                params={"id": ",".join(spec.series_id for spec in specs), "cosd": start.isoformat()},
                timeout=min(self.timeout, 8),
            )
            response.raise_for_status()
            frames = self._response_frames(response)
            output: dict[str, MarketPoint] = {}
            for spec in specs:
                try:
                    output[spec.series_id] = self._point_from_frames(frames, spec)
                except (ValueError, KeyError, pd.errors.ParserError) as exc:
                    output[spec.series_id] = self._unavailable(spec, type(exc).__name__)
            return output
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in {400, 404} and len(specs) > 1:
                # FRED rejects the whole batch when one legacy series has been
                # retired. Retrying separately preserves every valid series.
                with ThreadPoolExecutor(max_workers=min(5, len(specs)), thread_name_prefix="fred-fallback") as pool:
                    points = list(pool.map(self.fetch, specs))
                return {point.series_id: point for point in points}
            return {spec.series_id: self._unavailable(spec, f"HTTP {status_code or 'error'}") for spec in specs}
        except (requests.RequestException, ValueError, KeyError, pd.errors.ParserError, zipfile.BadZipFile) as exc:
            return {spec.series_id: self._unavailable(spec, type(exc).__name__) for spec in specs}

    def fetch_all(self) -> list[MarketPoint]:
        if os.name == "nt" and os.getenv("FRED_ISOLATED_WORKER") != "1":
            return self._fetch_all_windows_native()
        return self._fetch_all_network()

    def _fetch_all_windows_native(self) -> list[MarketPoint]:
        """Retrieve real data through curl.exe instead of hanging Python sockets."""
        if not shutil.which("curl.exe"):
            return self._fetch_all_windows_guarded()

        with self._lock:
            results = {
                spec.series_id: MarketPoint(**cached.to_dict())
                for spec in SERIES if (cached := self.cache.get(spec.series_id)) is not None
            }
        pending = [spec for spec in SERIES if spec.series_id not in results]
        if not pending:
            return [results[spec.series_id] for spec in SERIES]

        probe_spec = next((spec for spec in pending if spec.series_id == "DGS10"), pending[0])
        pending.remove(probe_spec)
        probe_point = self._fetch_windows_fred(probe_spec)
        if probe_point is not None:
            provider_fetch = lambda spec: self._fetch_windows_fred(spec) or self._fetch_windows_dbnomics(spec)
        else:
            probe_point = self._fetch_windows_dbnomics(probe_spec)
            provider_fetch = self._fetch_windows_dbnomics if probe_point is not None else None

        if probe_point is None:
            results[probe_spec.series_id] = self._unavailable(probe_spec, "FRED and DBnomics Windows access failed")
            for spec in pending:
                results[spec.series_id] = self._unavailable(spec, "Market-data connectivity probe failed")
            return [results[spec.series_id] for spec in SERIES]

        results[probe_spec.series_id] = probe_point
        if pending and provider_fetch is not None:
            with ThreadPoolExecutor(
                max_workers=min(FRED_SNAPSHOT_WORKERS, len(pending)),
                thread_name_prefix="fred-windows-curl",
            ) as pool:
                futures = {pool.submit(provider_fetch, spec): spec for spec in pending}
                for future in as_completed(futures):
                    spec = futures[future]
                    try:
                        point = future.result()
                    except Exception as exc:
                        point = self._unavailable(spec, f"Windows provider {type(exc).__name__}")
                    if point is None:
                        point = self._unavailable(spec, "FRED and DBnomics series unavailable")
                    results[spec.series_id] = point

        for point in results.values():
            if point.value is not None:
                with self._lock:
                    self.cache[point.series_id] = point
        return [results[spec.series_id] for spec in SERIES]

    def _fetch_all_windows_guarded(self) -> list[MarketPoint]:
        """Enforce a real wall-clock deadline around Windows network calls."""
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["FRED_ISOLATED_WORKER"] = "1"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.fred_worker"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=FRED_WINDOWS_TOTAL_TIMEOUT_SECONDS,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise ValueError("isolated FRED worker failed")
            payload = json.loads(result.stdout.strip())
            points = [MarketPoint(**item) for item in payload]
            if len(points) != len(SERIES):
                raise ValueError("isolated FRED worker returned an incomplete snapshot")
            for point in points:
                if point.value is not None:
                    with self._lock:
                        self.cache[point.series_id] = point
            return points
        except subprocess.TimeoutExpired:
            reason = f"FRED Windows deadline exceeded ({FRED_WINDOWS_TOTAL_TIMEOUT_SECONDS}s)"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            reason = "FRED isolated Windows worker failed"
        return [self._unavailable(spec, reason) for spec in SERIES]

    def _fetch_all_network(self) -> list[MarketPoint]:
        """Retrieve each FRED series independently and concurrently.

        fredgraph.csv may return a ZIP for grouped IDs and that behaviour is
        not consistent across every proxy/CDN route.  Single-series CSV is the
        stable public contract, so snapshots use it directly.  Concurrency
        keeps the full dashboard fast without letting one retired series or
        malformed response blank unrelated indicators.
        """
        with self._lock:
            results = {
                spec.series_id: MarketPoint(**cached.to_dict())
                for spec in SERIES if (cached := self.cache.get(spec.series_id)) is not None
            }
        pending = [spec for spec in SERIES if spec.series_id not in results]
        if pending:
            # Probe once before creating a full request wave. If FRED is blocked,
            # all remaining series go directly to the successful fallback.
            probe_spec = next((spec for spec in pending if spec.series_id == "DGS10"), pending[0])
            pending.remove(probe_spec)
            probe_point = self.fetch(probe_spec)
            results[probe_spec.series_id] = probe_point
            start = datetime.now(timezone.utc).date() - pd.Timedelta(days=FRED_LOOKBACK_DAYS)
            if probe_point.source_name == "DBnomics mirror of FRED":
                provider_fetch = self._fetch_dbnomics
            elif probe_point.source_name == "FRED official API":
                provider_fetch = lambda spec: self._fetch_official_api(spec, start)
            elif probe_point.value is not None:
                provider_fetch = self.fetch
            else:
                provider_fetch = None

        if pending and provider_fetch is not None:
            with ThreadPoolExecutor(
                max_workers=min(FRED_SNAPSHOT_WORKERS, len(pending)),
                thread_name_prefix="fred-single",
            ) as pool:
                futures = {pool.submit(provider_fetch, spec): spec for spec in pending}
                for future in as_completed(futures):
                    spec = futures[future]
                    try:
                        point = future.result()
                    except Exception as exc:  # defensive isolation at the worker boundary
                        point = self._unavailable(spec, f"FRED worker {type(exc).__name__}")
                    if point is None:
                        point = self._unavailable(spec, "FRED fallback unavailable")
                    results[point.series_id] = point
        elif pending:
            for spec in pending:
                results[spec.series_id] = self._unavailable(spec, "FRED connectivity probe failed")

        for point in results.values():
            if point.value is not None:
                with self._lock:
                    self.cache[point.series_id] = point
        return [results[spec.series_id] for spec in SERIES]

    @staticmethod
    def _unavailable(spec: SeriesSpec, reason: str) -> MarketPoint:
        return MarketPoint(
            series_id=spec.series_id, label=spec.label, category=spec.category,
            value=None, previous=None, change=None, date="unavailable", unit=spec.unit,
            frequency=spec.frequency, status="unavailable", source_name=spec.source_name,
            source_url=spec.source_url, error=f"{reason}: data source unavailable",
        )

    def fetch_history(self, series_ids: list[str], days: int = 365) -> tuple[pd.DataFrame, list[str]]:
        """Fetch each history separately, using the proven curl path on Windows."""
        selected = [spec for spec in SERIES if spec.series_id in set(series_ids)][:6]
        if not selected:
            return pd.DataFrame(columns=["Date"]), ["Select at least one supported series."]
        start = datetime.now(timezone.utc).date() - pd.Timedelta(days=max(30, min(days, 3650)))
        errors: list[str] = []

        def load_one(spec: SeriesSpec) -> tuple[SeriesSpec, pd.DataFrame | None, str | None]:
            url = f"{self.base_url}?{urlencode({'id': spec.series_id})}"
            try:
                if os.name == "nt" and shutil.which("curl.exe"):
                    content = self._windows_curl(url)
                    if content is None:
                        raise ConnectionError("native curl failed")
                    source = pd.read_csv(io.BytesIO(content))
                else:
                    response = self.session.get(self.base_url, params={"id": spec.series_id}, timeout=self._timeout())
                    response.raise_for_status()
                    source = pd.read_csv(io.StringIO(response.text))
                date_column = self._date_column(source)
                frame = source[[date_column, spec.series_id]].copy().rename(
                    columns={date_column: "Date", spec.series_id: spec.label},
                )
                frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
                frame[spec.label] = pd.to_numeric(frame[spec.label], errors="coerce")
                frame = frame.loc[frame["Date"].dt.date >= start].dropna(subset=["Date", spec.label])
                return spec, frame, None
            except (requests.RequestException, ConnectionError, ValueError, KeyError, pd.errors.ParserError) as exc:
                return spec, None, type(exc).__name__

        frames: list[pd.DataFrame] = []
        with ThreadPoolExecutor(max_workers=min(FRED_SNAPSHOT_WORKERS, len(selected)), thread_name_prefix="fred-history") as pool:
            futures = [pool.submit(load_one, spec) for spec in selected]
            for future in futures:
                spec, frame, error = future.result()
                if frame is None or frame.empty:
                    errors.append(f"{spec.label}: {error or 'unavailable'}")
                else:
                    frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=["Date"]), errors
        output = frames[0]
        for frame in frames[1:]:
            output = output.merge(frame, on="Date", how="outer")
        output["Date"] = pd.to_datetime(output["Date"], errors="coerce")
        return output.sort_values("Date"), errors


class TreasuryCollector:
    """Official daily Treasury curve with automatic previous-session comparison."""

    base_url = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    source_url = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"

    def __init__(self, timeout: int | None = None, cache_ttl: int | None = None):
        self.timeout = timeout or settings.request_timeout
        self.cache = TTLCache(maxsize=1, ttl=cache_ttl or settings.cache_ttl)
        self._lock = threading.Lock()
        self.session = requests.Session()
        retries = Retry(total=1, connect=1, read=1, backoff_factor=0.2, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({"User-Agent": "AI-Sales-Desk-Assistant/2.2 (public market research)"})

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _rows(self, text: str) -> list[dict[str, str]]:
        root = ET.fromstring(text)
        rows: list[dict[str, str]] = []
        for entry in root.iter():
            if self._local_name(entry.tag) != "entry":
                continue
            row: dict[str, str] = {}
            for node in entry.iter():
                name = self._local_name(node.tag)
                if node.text and name in {"NEW_DATE", *TREASURY_FIELDS.values()}:
                    row[name] = node.text.strip()
            if "NEW_DATE" in row:
                rows.append(row)
        return rows

    def _year(self, year: int) -> list[dict[str, str]]:
        response = self.session.get(
            self.base_url,
            params={"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(year)},
            timeout=min(self.timeout, 8),
        )
        response.raise_for_status()
        return self._rows(response.text)

    def fetch_all(self) -> dict[str, MarketPoint]:
        with self._lock:
            cached = self.cache.get("curve")
        if cached is not None:
            return {key: MarketPoint(**point.to_dict()) for key, point in cached.items()}

        try:
            current_year = datetime.now(timezone.utc).year
            rows = self._year(current_year)
            if len(rows) < 2:
                rows = self._year(current_year - 1) + rows
            dated_rows: list[tuple[date, dict[str, str]]] = []
            for row in rows:
                parsed = pd.to_datetime(row.get("NEW_DATE"), errors="coerce")
                if pd.notna(parsed):
                    dated_rows.append((parsed.date(), row))
            dated_rows.sort(key=lambda item: item[0])
            output: dict[str, MarketPoint] = {}
            for series_id, field in TREASURY_FIELDS.items():
                observations: list[tuple[date, float]] = []
                for obs_date, row in dated_rows:
                    value = pd.to_numeric(row.get(field), errors="coerce")
                    if pd.notna(value):
                        observations.append((obs_date, float(value)))
                if len(observations) < 2:
                    continue
                (previous_date, previous), (obs_date, value) = observations[-2:]
                spec = SERIES_BY_ID[series_id]
                status = "official close" if obs_date == datetime.now(timezone.utc).date() else "last official close"
                output[series_id] = MarketPoint(
                    series_id, spec.label, spec.category, value, previous, value - previous,
                    obs_date.isoformat(), spec.unit, spec.frequency, status,
                    "U.S. Treasury", self.source_url, previous_date.isoformat(),
                )
            if output:
                with self._lock:
                    self.cache["curve"] = output
            return output
        except (requests.RequestException, ValueError, KeyError, ET.ParseError):
            return {}


class LiveMarketCollector:
    """Best-effort intraday quotes with previous-close comparison and daily fallback."""

    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, timeout: int | None = None, cache_ttl: int | None = None):
        self.timeout = timeout or settings.request_timeout
        self.cache = TTLCache(maxsize=len(LIVE_SYMBOLS), ttl=cache_ttl or settings.live_cache_ttl)
        self._lock = threading.Lock()
        self.session = requests.Session()
        retries = Retry(total=1, connect=1, read=1, backoff_factor=0.15, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({"User-Agent": "Mozilla/5.0 AI-Sales-Desk-Assistant/2.2"})

    def _fetch_one(self, series_id: str, symbol: str) -> MarketPoint | None:
        with self._lock:
            cached = self.cache.get(series_id)
        if cached is not None:
            return MarketPoint(**cached.to_dict())
        try:
            response = self.session.get(
                f"{self.base_url}/{quote(symbol, safe='')}",
                params={"interval": "5m", "range": "5d", "includePrePost": "true", "events": "div,splits"},
                timeout=min(self.timeout, 6),
            )
            response.raise_for_status()
            payload = response.json()
            result = payload["chart"]["result"][0]
            meta = result.get("meta", {})
            timestamps = result.get("timestamp", [])
            close_values = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            observations = [
                (int(timestamp), float(value))
                for timestamp, value in zip(timestamps, close_values)
                if value is not None
            ]
            if not observations:
                return None
            latest_timestamp, chart_price = observations[-1]
            regular_market_timestamp = int(meta.get("regularMarketTime") or latest_timestamp)
            extended_session = latest_timestamp > regular_market_timestamp + 60
            value = float(chart_price if extended_session else (meta.get("regularMarketPrice") or chart_price))
            previous_raw = meta.get("chartPreviousClose") or meta.get("previousClose")
            session_dates = []
            for timestamp, _ in observations:
                day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
                if not session_dates or session_dates[-1] != day:
                    session_dates.append(day)
            previous_date = session_dates[-2] if len(session_dates) >= 2 else None
            previous = float(previous_raw) if previous_raw not in (None, 0) else None
            if previous is None:
                daily_closes: dict[str, float] = {}
                for timestamp, close in observations:
                    daily_closes[datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()] = close
                closes = list(daily_closes.items())
                if len(closes) >= 2:
                    previous_date, previous = closes[-2]
            obs_datetime = datetime.fromtimestamp(latest_timestamp, timezone.utc)
            if obs_datetime.date() == datetime.now(timezone.utc).date():
                status = "live pre/post-market" if extended_session else "live intraday"
            else:
                status = "last market close"
            spec = SERIES_BY_ID[series_id]
            point = MarketPoint(
                series_id, spec.label, spec.category, value, previous,
                None if previous is None else value - previous,
                obs_datetime.strftime("%Y-%m-%d %H:%M UTC"), spec.unit, "intraday", status,
                "Yahoo Finance public quote", f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}",
                previous_date,
            )
            with self._lock:
                self.cache[series_id] = point
            return point
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            return None

    def fetch_all(self) -> dict[str, MarketPoint]:
        output: dict[str, MarketPoint] = {}
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="live-quote") as pool:
            futures = {
                pool.submit(self._fetch_one, series_id, symbol): series_id
                for series_id, symbol in LIVE_SYMBOLS.items()
            }
            for future in as_completed(futures):
                point = future.result()
                if point is not None:
                    output[point.series_id] = point
        return output


class MarketDataCollector:
    """Merge resilient official/delayed data with the best available live layer."""

    def __init__(self):
        self.fred = FredCollector()
        self.treasury = TreasuryCollector()
        self.live = LiveMarketCollector()

    def fetch_all(self) -> list[MarketPoint]:
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="market-provider") as pool:
            fred_future = pool.submit(self.fred.fetch_all)
            treasury_future = pool.submit(self.treasury.fetch_all)
            live_future = pool.submit(self.live.fetch_all)
            points = {point.series_id: point for point in fred_future.result()}
            points.update(treasury_future.result())
            points.update(live_future.result())
        return [points.get(spec.series_id, FredCollector._unavailable(spec, "All providers unavailable")) for spec in SERIES]

    def fetch_history(self, series_ids: list[str], days: int = 365) -> tuple[pd.DataFrame, list[str]]:
        return self.fred.fetch_history(series_ids, days)


def empty_points(status: str = "loading") -> list[MarketPoint]:
    return [MarketPoint(
        series_id=spec.series_id, label=spec.label, category=spec.category,
        value=None, previous=None, change=None, date="loading", unit=spec.unit,
        frequency=spec.frequency, status=status, source_name=spec.source_name,
        source_url=spec.source_url,
    ) for spec in SERIES]


def service_summary(points: list[MarketPoint], elapsed: float = 0.0) -> dict[str, object]:
    available = sum(point.value is not None for point in points)
    unavailable = len(points) - available
    statuses = sorted({point.status for point in points})
    return {
        "available": available,
        "unavailable": unavailable,
        "total": len(points),
        "intraday": sum(point.status.startswith("live ") for point in points),
        "official_close": sum("official close" in point.status for point in points),
        "latest_observation": sum(point.value is not None and point.status in {"latest available", "stale"} for point in points),
        "statuses": statuses,
        "elapsed_seconds": round(elapsed, 2),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def load_snapshot(collector: MarketDataCollector | FredCollector | None = None) -> tuple[list[MarketPoint], dict[str, object]]:
    started = time.monotonic()
    points = (collector or MarketDataCollector()).fetch_all()
    return points, service_summary(points, time.monotonic() - started)
