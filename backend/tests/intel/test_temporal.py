from astra_intel.temporal import temporal_fingerprint

def test_temporal_fingerprint_detects_short_deltas_and_fanout():
    rows = [
        {"source":"A","target":"B","timestamp":100,"amount":1,"asset":"BTC"},
        {"source":"A","target":"C","timestamp":110,"amount":2,"asset":"BTC"},
        {"source":"A","target":"D","timestamp":120,"amount":3,"asset":"BTC"},
        {"source":"E","target":"Z","timestamp":500,"amount":4,"asset":"ETH"},
    ]
    out = temporal_fingerprint(rows, rapid_window_seconds=60)
    assert out["transfer_count"] == 4
    assert out["rapid_forwarding_ratio"] == 0.6667
    assert out["fragmentation_ratio"] == 0.5
    assert out["flow_by_asset"]["BTC"]["amount_total"] == 6
    assert out["flow_by_asset"]["ETH"]["amount_total"] == 4

def test_empty_temporal_input_is_safe():
    assert temporal_fingerprint([])["transfer_count"] == 0
