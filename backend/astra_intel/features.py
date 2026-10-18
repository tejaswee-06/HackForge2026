from __future__ import annotations
import pandas as pd
import numpy as np


def add_graph_features(frame: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Add simple graph-derived degree features without using labels."""
    out = frame.copy()
    ids = set(out["txId"].astype(str))
    e = edges[edges["source_tx"].isin(ids) & edges["target_tx"].isin(ids)]
    indeg = e.groupby("target_tx").size()
    outdeg = e.groupby("source_tx").size()
    out["graph_in_degree"] = out["txId"].astype(str).map(indeg).fillna(0).astype("float32")
    out["graph_out_degree"] = out["txId"].astype(str).map(outdeg).fillna(0).astype("float32")
    out["graph_degree_total"] = out["graph_in_degree"] + out["graph_out_degree"]
    return out


def get_feature_columns(frame: pd.DataFrame) -> list[str]:
    blocked = {"txId", "label_name", "class", "label", "target", "time_step"}
    cols = []
    for c in frame.columns:
        if c in blocked:
            continue
        if pd.api.types.is_numeric_dtype(frame[c]):
            cols.append(c)
    if not cols:
        raise ValueError("No numeric model features were detected.")
    return cols


def make_model_matrix(frame: pd.DataFrame, columns: list[str] | None = None):
    cols = columns or get_feature_columns(frame)
    X = frame[cols].apply(pd.to_numeric, errors="coerce")
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return X, cols
