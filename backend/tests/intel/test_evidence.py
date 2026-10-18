from astra_intel.evidence import EvidenceItem, Hypothesis, score_hypothesis
from astra_intel.falsification import run_falsification


def ev(eid, direction, reliability=0.9, strength=0.8):
    return EvidenceItem(evidence_id=eid, kind="on_chain_flow", direction=direction,
                        reliability=reliability, strength=strength,
                        source="fixture", description="test evidence")


def test_falsification_recomputes_after_removing_support():
    h = Hypothesis(hypothesis_id="H1", title="test", evidence=[ev("E1","supports"), ev("E2","contradicts",0.6,0.3)])
    result = run_falsification(h, remove_ids=["E1"])
    assert result["counterfactual_score"] != result["baseline_score"]
    assert result["counterfactual"]["evidence_count"] == 1


def test_downgrade_recomputes_score():
    h = Hypothesis(hypothesis_id="H1", title="test", evidence=[ev("E1","supports")])
    result = run_falsification(h, downgrade_ids=["E1"], downgrade_factor=0.1)
    assert result["counterfactual_score"] < result["baseline_score"]


def test_unknown_or_empty_evidence_does_not_crash():
    h = Hypothesis(hypothesis_id="H0", title="empty", evidence=[])
    assert 0 <= score_hypothesis(h)["heuristic_score"] <= 1
