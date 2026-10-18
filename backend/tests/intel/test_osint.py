import pytest
from astra_intel.osint import extract_candidate_indicators, validate_indicator_match

def test_extracts_candidate_with_source_provenance():
    address = "0x" + "a" * 40
    out = extract_candidate_indicators(f"Incident report mentions {address}", source_url="https://example.org/report", source_name="Example report")
    assert len(out["candidate_indicators"]) == 1
    item = out["candidate_indicators"][0]
    assert item["source_url"] == "https://example.org/report"
    assert item["verification_status"] == "candidate_unverified"
    assert validate_indicator_match(item, address)["exact_match"] is True

def test_rejects_non_http_source():
    with pytest.raises(ValueError):
        extract_candidate_indicators("text", source_url="file:///etc/passwd", source_name="bad")
