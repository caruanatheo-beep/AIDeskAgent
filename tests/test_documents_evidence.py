from src.evidence import EvidenceRegistry
from src.models import Evidence, ToolResult


def test_evidence_registry_rejects_non_allowlisted_url():
    registry = EvidenceRegistry()
    registry.add_result(ToolResult("bad", "", [
        Evidence("Untrusted claim", "Unknown", "https://example.com/x", "today")
    ]))
    admissible, warnings = registry.audit()
    assert not admissible
    assert any("Rejected" in warning for warning in warnings)
