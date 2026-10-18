import pytest
from astra_intel.risk_scoring import score_lead, rank_leads

def test_empty_signals_is_low_and_transparent():
    result = score_lead("wallet-1", {})
    assert result["priority_score"] == 0
    assert result["priority_tier"] == "low_priority"
    assert len(result["missing_signals"]) == 13
    assert result["score_type"] == "transparent_priority_score_not_probability"

def test_supported_signals_raise_score_and_explain_contributors():
    result = score_lead("wallet-2", {
        "model_illicit_score": 0.9,
        "graph": {"incident_path_proximity": 1.0},
        "osint": {"indicator_match": 1.0, "source_quality": 0.9},
    })
    assert result["priority_score"] > 0
    assert result["top_contributors"]
    assert any(x["signal"] == "graph.incident_path_proximity"
               for x in result["components"])

def test_contradictions_reduce_priority():
    base = score_lead("wallet-3", {"model_illicit_score": 0.8})
    contradicted = score_lead("wallet-3", {
        "model_illicit_score": 0.8,
        "evidence": {"contradiction_strength": 1.0},
    })
    assert contradicted["priority_score"] < base["priority_score"]

def test_invalid_signal_rejected():
    with pytest.raises(ValueError):
        score_lead("wallet-4", {"model_illicit_score": 1.5})

def test_rank_order():
    ranked = rank_leads([
        {"entity_id": "low", "signals": {}},
        {"entity_id": "high", "signals": {"model_illicit_score": 1.0,
                                           "graph": {"incident_path_proximity": 1.0}}},
    ])
    assert ranked[0]["entity_id"] == "high"
