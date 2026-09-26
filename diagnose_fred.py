"""Small Windows-friendly diagnostic for the live market-data pipeline."""

from __future__ import annotations

import platform
import sys
from time import perf_counter

from src.data import FredCollector, MarketDataCollector


def _line(series_id: str, ok: bool, date: str, detail: str) -> str:
    state = "OK" if ok else "FAILED"
    return f"{series_id:<20} {state:<6} {date:<20} {detail}"


def main() -> int:
    print(f"Python {sys.version.split()[0]} | {platform.system()} {platform.release()}", flush=True)
    print("Build 2.5.0 | direct FRED test (no Yahoo/Treasury merge)", flush=True)
    print("Windows uses the minimal curl.exe request validated locally...\n", flush=True)

    started = perf_counter()
    fred_points = FredCollector(timeout=12, cache_ttl=60).fetch_all()
    fred_elapsed = perf_counter() - started
    for point in fred_points:
        print(_line(point.series_id, point.value is not None, point.date, point.error or point.source_name), flush=True)
    fred_available = sum(point.value is not None for point in fred_points)
    print(f"\nDirect FRED: {fred_available}/{len(fred_points)} available in {fred_elapsed:.2f}s")

    started = perf_counter()
    merged = MarketDataCollector().fetch_all()
    merged_elapsed = perf_counter() - started
    print("\nMerged dashboard providers:")
    for point in merged:
        print(_line(point.series_id, point.value is not None, point.date, point.source_name))
    merged_available = sum(point.value is not None for point in merged)
    print(f"\nMerged dashboard: {merged_available}/{len(merged)} available in {merged_elapsed:.2f}s")
    return 0 if merged_available == len(merged) and fred_available >= 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
