"""Isolated FRED process used to enforce a wall-clock timeout on Windows."""

from __future__ import annotations

import json
import os

from .data import FredCollector


def main() -> int:
    os.environ["FRED_ISOLATED_WORKER"] = "1"
    points = FredCollector(timeout=7, cache_ttl=60)._fetch_all_network()
    print(json.dumps([point.to_dict() for point in points], separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
