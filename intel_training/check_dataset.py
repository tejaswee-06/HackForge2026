import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'backend'))  # integrated layout

from pathlib import Path
import pandas as pd
from astra_intel.dataset import load_transaction_data

if __name__ == "__main__":
    frame, edges, sources = load_transaction_data(Path("data/ellipticpp"))
    print("Sources:", sources)
    print("Rows:", len(frame), "Columns:", len(frame.columns), "Edges:", len(edges))
    print("Time steps:", sorted(pd.to_numeric(frame["time_step"], errors="coerce").dropna().unique()))
    print("Label counts:", frame["label_name"].value_counts(dropna=False).to_dict())
