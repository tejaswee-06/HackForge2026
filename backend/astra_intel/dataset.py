from __future__ import annotations
from pathlib import Path
import pandas as pd

FEATURE_CANDIDATES = ("txId", "tx_id", "transaction_id", "id")
TIME_CANDIDATES = ("Time step", "time_step", "timestep", "timeStep", "time")
LABEL_CANDIDATES = ("class", "label", "Class", "target", "illicit")


def _find_col(df: pd.DataFrame, candidates: tuple[str, ...], required: bool = True):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    if required:
        raise ValueError(
            f"Could not find expected column from {candidates}. "
            f"Observed columns: {list(df.columns[:15])}"
        )
    return None


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Download Elliptic++ from "
            "https://github.com/git-disl/EllipticPlusPlus and place the CSV here."
        )
    return pd.read_csv(path, low_memory=False)


def load_transaction_data(data_dir: str | Path):
    """Load official Elliptic++ transaction features, classes and edges."""
    d = Path(data_dir)
    features = _read_csv(d / "txs_features.csv")
    classes = _read_csv(d / "txs_classes.csv")
    edges = _read_csv(d / "txs_edgelist.csv")

    # Official CSVs may use a leading unnamed index; remove only fully empty columns.
    features = features.dropna(axis=1, how="all")
    classes = classes.dropna(axis=1, how="all")
    edges = edges.dropna(axis=1, how="all")

    id_col = _find_col(features, FEATURE_CANDIDATES)
    class_id_col = _find_col(classes, FEATURE_CANDIDATES)
    label_col = _find_col(classes, LABEL_CANDIDATES)
    time_col = _find_col(features, TIME_CANDIDATES, required=False)

    if id_col != "txId":
        features = features.rename(columns={id_col: "txId"})
    if class_id_col != "txId":
        classes = classes.rename(columns={class_id_col: "txId"})
    if label_col != "class":
        classes = classes.rename(columns={label_col: "class"})
    if time_col and time_col != "time_step":
        features = features.rename(columns={time_col: "time_step"})
    elif not time_col:
        raise ValueError(
            "No time-step column found. A chronological split cannot be built safely. "
            "Inspect the dataset schema and adapt TIME_CANDIDATES if needed."
        )

    # Canonicalise labels while preserving unknown/unlabelled rows.
    classes["class"] = classes["class"].astype(str).str.strip().str.lower()
    label_map = {
        "1": "illicit", "illicit": "illicit", "illicit (class-1)": "illicit",
        "2": "licit", "licit": "licit", "licit (class-2)": "licit",
        "3": "unknown", "unknown": "unknown", "unlabelled": "unknown",
        "unlabeled": "unknown", "nan": "unknown", "": "unknown",
    }
    classes["label_name"] = classes["class"].map(label_map).fillna("unknown")
    merged = features.merge(classes[["txId", "label_name"]], on="txId", how="left")
    merged["label_name"] = merged["label_name"].fillna("unknown")

    # Common official Elliptic++ edge format has two columns (source, destination).
    if len(edges.columns) < 2:
        raise ValueError(f"Expected at least 2 edge-list columns, got {list(edges.columns)}")
    edges = edges.iloc[:, :2].copy()
    edges.columns = ["source_tx", "target_tx"]
    edges = edges.dropna()
    edges["source_tx"] = edges["source_tx"].astype(str)
    edges["target_tx"] = edges["target_tx"].astype(str)
    merged["txId"] = merged["txId"].astype(str)
    return merged, edges, {"feature_file": str(d / "txs_features.csv"),
                            "class_file": str(d / "txs_classes.csv"),
                            "edge_file": str(d / "txs_edgelist.csv")}
