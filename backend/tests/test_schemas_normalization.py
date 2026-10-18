import pytest
from pydantic import ValidationError

from app.schemas.common import SourceRef
from app.schemas.transaction import NormalizedTransfer, format_units
from app.services.ingestion.normalizer import NormalizationError, normalize_batch, normalize_row

from .conftest import SCEN_ID

SRC = SourceRef(provider="unit-test", data_mode="synthetic", retrieved_at="2026-10-01T00:00:00+00:00", reference="t")
A, B = "0x" + "AB" * 20, "0x" + "cd" * 20
H = "0x" + "1" * 64


def row(**kw):
    r = {"hash": H, "blockNumber": "5", "timeStamp": "1790000000", "from": A, "to": B, "value": "123",
         "isError": "0", "txreceipt_status": "1"}
    r.update(kw)
    return r


def native(**kw):
    return normalize_row(row(**kw), "native", 1, SRC)


def test_valid_native_transaction_fields():
    ev = native()
    assert ev.event_id == f"1:{H}:tx" and ev.asset_id == "1:native" and ev.status == "success"
    assert ev.from_address == A.lower() and ev.to_address == B.lower()  # case-normalised, same identity
    assert ev.raw_value == "123" and ev.token_decimals == 18 and ev.block_number == 5
    assert ev.timestamp.utcoffset().total_seconds() == 0 and ev.source.provider == "unit-test"


@pytest.mark.parametrize("bad", [{"from": "0x123"}, {"to": "zz" * 21}, {"hash": "0xabc"}, {"value": "-5"},
                                 {"value": "1.5"}, {"value": "1e18"}, {"timeStamp": "yesterday"}, {"blockNumber": "x"}])
def test_invalid_fields_rejected(bad):
    with pytest.raises(NormalizationError):
        native(**bad)


@pytest.mark.parametrize("missing", ["hash", "from", "to", "value", "timeStamp"])
def test_missing_required_field_rejected(missing):
    r = row()
    del r[missing]
    with pytest.raises(NormalizationError):
        normalize_row(r, "native", 1, SRC)


def test_non_object_row_rejected():
    with pytest.raises(NormalizationError):
        normalize_row("not-a-dict", "native", 1, SRC)


def test_contract_creation_rejected():
    with pytest.raises(NormalizationError):
        native(to="")


def test_exact_large_raw_amount_no_float():
    big = str(2**200 + 1)
    ev = native(value=big)
    assert ev.raw_value == big and int(ev.raw_value) == 2**200 + 1
    assert format_units(10**18 + 1, 18) == "1.000000000000000001"  # float would lose this


def test_zero_is_not_unknown():
    ev = native(value="0")
    assert ev.raw_value == "0" and ev.normalized_value == "0.000000000000000000"


def test_unknown_token_decimals_not_invented():
    tok = {"hash": H, "timeStamp": "1790000000", "from": A, "to": B, "value": "1000", "contractAddress": "0x" + "ee" * 20}
    ev = normalize_row(tok, "erc20", 1, SRC)
    assert ev.token_decimals is None and ev.normalized_value is None and ev.raw_value == "1000"
    assert ev.status == "success" and ev.asset_id == f"1:erc20:{'0x' + 'ee' * 20}"


def test_known_token_decimals_normalised_exactly():
    tok = {"hash": H, "timeStamp": "1790000000", "from": A, "to": B, "value": "1500000", "tokenDecimal": "6",
           "contractAddress": "0x" + "ee" * 20, "logIndex": "7"}
    ev = normalize_row(tok, "erc20", 1, SRC)
    assert ev.normalized_value == "1.500000" and ev.log_index == 7 and ev.event_id.endswith(":log-7")


def test_status_failed_and_unknown():
    assert native(isError="1", txreceipt_status="0").status == "failed"
    r = row(); del r["isError"]; del r["txreceipt_status"]
    assert normalize_row(r, "native", 1, SRC).status == "unknown"


def test_naive_timestamp_rejected_by_model():
    d = native().model_dump()
    d["timestamp"] = "2026-10-01T00:00:00"
    with pytest.raises(ValidationError):
        NormalizedTransfer(**d)


def test_normalized_value_inconsistency_rejected():
    d = native().model_dump()
    d["normalized_value"] = "9.0"
    with pytest.raises(ValidationError):
        NormalizedTransfer(**d)


def test_duplicate_identical_event_dropped_deterministically():
    res = normalize_batch([row(), row()], [], 1, SRC)
    assert len(res.events) == 1 and res.duplicates_dropped == 1 and res.conflicts == []


def test_conflicting_duplicates_flagged_and_resolved_deterministically():
    a, b = row(value="100"), row(value="200")
    r1 = normalize_batch([a, b], [], 1, SRC)
    r2 = normalize_batch([b, a], [], 1, SRC)  # input order must not matter
    assert r1.conflicts == [r1.events[0].event_id] and len(r1.events) == 1
    assert r1.events[0].raw_value == r2.events[0].raw_value == "100"


def test_batch_reports_rejected_rows_without_failing():
    res = normalize_batch([row(), {"hash": "bad"}, row(hash="0x" + "2" * 64)], [], 1, SRC)
    assert len(res.events) == 2 and len(res.rejected) == 1 and res.rejected[0]["index"] == 1


def test_source_metadata_preserved(ingested):
    assert all(e.source.provider == "astra-fixture" and e.source.data_mode == "synthetic" for e in ingested.events)
    assert all(e.source.reference == f"{SCEN_ID}@1.0.0" for e in ingested.events)


def test_scenario_normalisation_is_deterministic(scenario):
    from app.services.ingestion.service import ingest_scenario
    a = [e.model_dump_json() for e in ingest_scenario(scenario).events]
    b = [e.model_dump_json() for e in ingest_scenario(scenario).events]
    assert a == b and len(a) == scenario.expected["counts"]["unique_events"]


def test_scenario_counts_and_synthetic_labelling(scenario, ingested):
    c = scenario.expected["counts"]
    assert ingested.duplicates_dropped == c["duplicates_dropped"] and not ingested.rejected
    assert scenario.synthetic and scenario.data_mode == "synthetic" and "SYNTHETIC" in scenario.synthetic_notice
    assert ingested.data_mode == "synthetic" and any("SYNTHETIC" in l for l in ingested.limitations)
    assert all(r.get("astraSynthetic") for r in scenario.native_rows + scenario.erc20_rows)
