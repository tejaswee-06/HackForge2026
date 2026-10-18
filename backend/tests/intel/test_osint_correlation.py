"""OSINT extraction/validation and on-chain correlation edge cases."""
from datetime import datetime, timezone

from astra_intel.correlation import correlate, freshness, independence_groups
from astra_intel.dsa import Edge, GraphIndex
from astra_intel.keccak import checksum_status, keccak256, to_checksum_address
from astra_intel.osint import classify_stance, extract_indicators

GOOD = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"     # EIP-55 test vector
BAD = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"      # last letter case flipped
A1 = "0x" + "1" * 38 + "ab"
A2 = "0x" + "2" * 38 + "ab"
TX = "0x" + "c" * 64
T0 = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def doc(text, doc_id="d1", **kw):
    return {"doc_id": doc_id, "text": text, "source_name": kw.get("source_name", "S"), "url": "https://x.invalid/a",
            "published_at": kw.get("published_at", "2026-10-01T05:00:00Z"), "retrieved_at": "2026-10-01T06:00:00Z",
            "reliability": kw.get("reliability", 0.8), "content_hash": kw.get("content_hash", "h-" + doc_id),
            "publisher_group": kw.get("publisher_group"), "data_mode": "synthetic"}


def test_keccak_and_eip55():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert to_checksum_address(GOOD.lower()) == GOOD
    assert checksum_status(GOOD) == "checksum_valid" and checksum_status(BAD) == "checksum_invalid"
    assert checksum_status(GOOD.lower()) == "no_checksum"


def test_extraction_types_spans_and_no_overlap():
    text = f"Tx {TX} moved funds. The attacker {GOOD} paid {BAD}. Short form 0x1111…1ab. BTC 1BoatSLRHtKNngkdXEeobR76b53LETtpyT."
    inds = extract_indicators(doc(text))
    types = [i["type"] for i in inds]
    assert types.count("tx_hash") == 1 and types.count("evm_address") == 2
    assert "truncated_evm_address" in types and "bitcoin_address_candidate" in types
    for i in inds:
        a, b = i["span"]
        assert text[a:b] == i["raw_value"] and i["source_url"] and i["extraction_method"] and i["retrieved_at"]
    v = {i["raw_value"]: i["validation_status"] for i in inds}
    assert v[GOOD] == "checksum_valid" and v[BAD] == "checksum_invalid"
    assert all(i["normalized_value"] != TX[:42] for i in inds)  # tx hash is never also an address


def test_stance_classification_with_negation():
    assert classify_stance("The attacker drained the pool.")[0] == "incident_association"
    assert classify_stance("This is an exchange hot wallet.")[0] == "service_label"
    assert classify_stance("It is not an exchange; it is the attacker.")[0] == "incident_association"
    assert classify_stance("Funds entered a mixer.")[0] == "privacy_boundary"
    assert classify_stance("The exchange was hacked.")[0] == "mixed"
    assert classify_stance("A random sentence.")[0] == "neutral"


def graph():
    return GraphIndex([Edge("1:e1", f"1:{A1}", f"1:{A2}", T0 + 10, "1:native", 5, TX),
                       Edge("1:e2", f"1:{A2}", f"1:{'0x' + '3' * 40}", T0 + 20, "1:native", 5, "0x" + "d" * 64)])


def run(docs, **kw):
    inds = [i for d in docs for i in extract_indicators(d)]
    return correlate(inds, {d["doc_id"]: d for d in docs}, graph(), 1, as_of=T0 + 86400, incident_start=T0, **kw)


def test_match_grades_exact_strong_weak_ambiguous_none():
    d = doc(f"Attacker {A1}. Tx {TX}. Short 0x1111…1ab and 0x…. Ambiguous form 0x{'1' * 4}…ab? no. "
            f"Unknown {'0x' + '9' * 40}. Checksum {BAD}.")
    res = run([d])
    by_type = {}
    for f in res["findings"]:
        by_type.setdefault(f["indicator_type"], []).append(f["match_type"])
    assert by_type["evm_address"].count("exact") == 1 and by_type["evm_address"].count("no_match") == 2
    assert by_type["tx_hash"] == ["strong"]
    assert by_type["truncated_evm_address"] == ["weak"]
    tx_f = next(f for f in res["findings"] if f["indicator_type"] == "tx_hash")
    assert set(tx_f["matched_nodes"]) == {f"1:{A1}", f"1:{A2}"} and tx_f["evidence_kind"] == "indirect"


def test_ambiguous_shortened_form_is_not_attributed():
    # both A1 and A2 end with "ab"; prefix 0x... differs, so construct a form matching both by suffix only
    g = GraphIndex([Edge("1:e1", "1:0x1234" + "0" * 33 + "9ab", "1:0x1234" + "1" * 33 + "9ab", T0, "1:native", 1, TX)])
    d = doc("Someone posted 0x1234…9ab.")
    inds = extract_indicators(d)
    res = correlate(inds, {"d1": d}, g, 1, as_of=T0 + 10, incident_start=T0)
    f = res["findings"][0]
    assert f["match_type"] == "ambiguous" and f["match_quality"] == 0.0 and len(f["matched_nodes"]) == 2
    assert res["by_node"] == {}  # ambiguous findings are attached to no node


def test_contradiction_between_sources_is_flagged():
    d1 = doc(f"The attacker sent stolen funds to {A2}.", "d1")
    d2 = doc(f"{A2} is an exchange deposit address.", "d2", source_name="Labels")
    res = run([d1, d2])
    fs = [f for f in res["findings"] if f["matched_nodes"] == [f"1:{A2}"]]
    assert len(fs) == 2 and all(f["contradictory"] for f in fs)
    assert fs[0]["contradicting_finding_ids"] == [fs[1]["finding_id"]]


def test_freshness_unknown_stale_fresh_and_predates_incident():
    assert freshness(None, T0, 90, 180)["status"] == "unknown" and freshness(None, T0, 90, 180)["score"] is None
    assert freshness(T0 - 200 * 86400, T0, 90, 180)["stale"] is True
    f = freshness(T0 - 90 * 86400, T0, 90, 180)
    assert f["status"] == "fresh" and abs(f["score"] - 0.5) < 1e-9
    assert freshness(T0 + 10, T0, 90, 180)["status"] == "after_as_of"
    old = doc(f"{A1} was used in a phishing scam.", "old", published_at="2025-01-01T00:00:00Z")
    res = run([old])
    f = res["findings"][0]
    assert f["predates_incident"] and f["freshness"]["stale"] and any("predates" in u for u in f["unknowns"])
    undated = doc(f"Attacker {A1}.", "u", published_at=None)
    assert run([undated])["findings"][0]["freshness"]["status"] == "unknown"


def test_independence_groups_union_duplicates_and_same_publisher():
    docs = [doc("x", "a", content_hash="H1", publisher_group="p1"), doc("x", "b", content_hash="H1", publisher_group="p2"),
            doc("y", "c", content_hash="H2", publisher_group="p2"), doc("z", "d", content_hash="H3", publisher_group="p3",
                                                                       source_name="Other")]
    uf = independence_groups(docs)
    assert uf.find("a") == uf.find("b") == uf.find("c")  # identical text (a,b) + same publisher (b,c)
    assert uf.find("d") != uf.find("a")
