from __future__ import annotations

import json
from dataclasses import asdict

from .models import Evidence, ToolResult
from .security import MARKET_DATA_HOSTS, safe_url


class EvidenceRegistry:
    """Deduplicated, bounded evidence collected before synthesis."""

    def __init__(self, limit: int = 60):
        self.limit = limit
        self._items: list[Evidence] = []
        self._keys: set[tuple[str, str, str]] = set()
        self.warnings: list[str] = []

    def add_result(self, result: ToolResult) -> None:
        self.warnings.extend(result.warnings)
        for item in result.evidence:
            if len(self._items) >= self.limit:
                self.warnings.append("Evidence limit reached; later items were omitted.")
                break
            if safe_url(item.url, MARKET_DATA_HOSTS) is None:
                self.warnings.append(f"Rejected a non-allowlisted source from {item.source}.")
                continue
            key = (item.claim.casefold(), item.source.casefold(), item.observation_date)
            if key not in self._keys:
                self._keys.add(key)
                self._items.append(item)

    @property
    def items(self) -> list[Evidence]:
        return list(self._items)

    def verified_context(self, char_limit: int) -> str:
        payload = [asdict(item) for item in self._items]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:char_limit]

    def source_markdown(self, limit: int = 12) -> str:
        lines: list[str] = []
        for item in self._items[:limit]:
            label = f"{item.source}: {item.observation_date}"
            lines.append(f"- [{label}]({item.url})")
        return "\n".join(lines) or "- No admissible source was collected."

    def metrics(self) -> dict[str, int]:
        """Return a transparent, deterministic evidence-quality summary."""
        if not self._items:
            return {"coverage_score": 0, "source_count": 0, "high_confidence_count": 0}
        source_count = len({(item.source.casefold(), item.url) for item in self._items})
        high_confidence_count = sum(item.confidence == "high" for item in self._items)
        dated_count = sum(bool(item.observation_date and item.observation_date != "unavailable") for item in self._items)
        # The score measures evidence coverage, not forecast accuracy. It is intentionally
        # simple so it remains explainable and adds no model or network cost.
        breadth = min(40, source_count * 10)
        confidence = round(35 * high_confidence_count / len(self._items))
        freshness = round(25 * dated_count / len(self._items))
        return {
            "coverage_score": min(100, breadth + confidence + freshness),
            "source_count": source_count,
            "high_confidence_count": high_confidence_count,
        }

    def audit(self) -> tuple[bool, list[str]]:
        warnings = list(dict.fromkeys(self.warnings))
        if not self._items:
            warnings.append("No admissible evidence was available; the answer must remain a fallback.")
            return False, warnings
        low_confidence = sum(item.confidence == "low" for item in self._items)
        if low_confidence:
            warnings.append(f"{low_confidence} evidence item(s) are labelled low confidence.")
        observed: dict[str, set[str]] = {}
        for item in self._items:
            if item.value:
                subject = item.claim.split(":", 1)[0].strip().casefold()
                observed.setdefault(subject, set()).add(item.value)
        conflicts = [subject for subject, values in observed.items() if len(values) > 1]
        if conflicts:
            warnings.append("Potential source disagreement detected for: " + ", ".join(conflicts[:5]) + ".")
        return True, warnings
