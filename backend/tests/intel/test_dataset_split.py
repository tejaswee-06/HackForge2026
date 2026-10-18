import pytest

pd = pytest.importorskip("pandas", reason="optional training dependency (intel_training/requirements-training.txt)")
from astra_intel.train import chronological_split  # noqa: E402


def test_chronological_split_orders_time_steps():
    df = pd.DataFrame({"time_step": list(range(1, 50)), "y": [0,1] * 24 + [0]})
    train, val, test, info = chronological_split(df)
    assert max(train.time_step) < min(val.time_step)
    assert max(val.time_step) < min(test.time_step)
