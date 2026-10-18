
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    average_precision_score,
    roc_auc_score,
)
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from .dataset import load_transaction_data
from .features import add_graph_features

LABEL_TO_INT = {"licit": 0, "illicit": 1}


def chronological_split(df: pd.DataFrame):
    """Split by ordered time steps: 70% train, 15% validation, 15% test."""
    steps = sorted(
        pd.to_numeric(df["time_step"], errors="coerce")
        .dropna()
        .unique()
    )

    if len(steps) < 3:
        raise ValueError(
            f"Need >=3 distinct time steps; observed {len(steps)}."
        )

    n = len(steps)
    train_cut = max(1, int(n * 0.70))
    val_cut = max(train_cut + 1, int(n * 0.85))
    val_cut = min(val_cut, n - 1)

    train_steps = set(steps[:train_cut])
    val_steps = set(steps[train_cut:val_cut])
    test_steps = set(steps[val_cut:])

    train = df[df["time_step"].isin(train_steps)].copy()
    val = df[df["time_step"].isin(val_steps)].copy()
    test = df[df["time_step"].isin(test_steps)].copy()

    split_info = {
        "train_steps": sorted(train_steps),
        "validation_steps": sorted(val_steps),
        "test_steps": sorted(test_steps),
    }

    return train, val, test, split_info


def _make_estimator(
    scale_pos_weight=1.0,
    max_depth=5,
    learning_rate=0.04,
    n_estimators=600,
    min_child_weight=3,
):
    """Create an XGBoost model, with a sklearn fallback."""
    try:
        from xgboost import XGBClassifier

        model = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            min_child_weight=min_child_weight,
            subsample=0.85,
            colsample_bytree=0.85,
            scale_pos_weight=scale_pos_weight,
            objective="binary:logistic",
            eval_metric="aucpr",
            tree_method="hist",
            reg_alpha=0.1,
            reg_lambda=2.0,
            random_state=42,
            n_jobs=-1,
        )
        return model, "XGBoost"

    except ImportError:
        model = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", HistGradientBoostingClassifier(
                max_iter=n_estimators,
                learning_rate=learning_rate,
                max_leaf_nodes=31,
                l2_regularization=2.0,
                random_state=42,
            )),
        ])
        return model, "HistGradientBoosting"


def _fit_estimator(model, name, X, y, scale_pos_weight):
    """Fit with class-imbalance weights when supported."""
    if name == "XGBoost":
        model.fit(X, y)
    else:
        weights = np.where(
            np.asarray(y) == 1,
            scale_pos_weight,
            1.0,
        )
        model.fit(X, y, model__sample_weight=weights)


def _metrics(y_true, y_pred, y_prob):
    """Calculate classification metrics for the supplied evaluation split."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    y_prob = np.asarray(y_prob)

    result = {
        "n": int(len(y_true)),
        "accuracy": float(np.mean(y_true == y_pred)),
        "precision_illicit": float(
            precision_score(y_true, y_pred, zero_division=0)
        ),
        "recall_illicit": float(
            recall_score(y_true, y_pred, zero_division=0)
        ),
        "f1_illicit": float(
            f1_score(y_true, y_pred, zero_division=0)
        ),
        "confusion_matrix_labels_0_licit_1_illicit":
            confusion_matrix(
                y_true, y_pred, labels=[0, 1]
            ).tolist(),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=[0, 1],
            target_names=["licit", "illicit"],
            output_dict=True,
            zero_division=0,
        ),
    }

    if len(np.unique(y_true)) == 2:
        result["pr_auc_average_precision"] = float(
            average_precision_score(y_true, y_prob)
        )
        result["roc_auc"] = float(
            roc_auc_score(y_true, y_prob)
        )
    else:
        result["pr_auc_average_precision"] = None
        result["roc_auc"] = None

    return result


def run_training(data_dir: str | Path, output_dir: str | Path):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    merged, edges, sources = load_transaction_data(data_dir)

    merged["time_step"] = pd.to_numeric(
        merged["time_step"], errors="coerce"
    )
    merged = merged.dropna(subset=["time_step"]).copy()

    # NOTE: Audit add_graph_features for temporal leakage before
    # interpreting these results as a production performance estimate.
    merged = add_graph_features(merged, edges)

    counts = merged["label_name"].value_counts(
        dropna=False
    ).to_dict()

    labelled = merged[
        merged["label_name"].isin(LABEL_TO_INT)
    ].copy()

    labelled["y"] = (
        labelled["label_name"].map(LABEL_TO_INT).astype(int)
    )

    train, val, test, split_info = chronological_split(labelled)

    if train.empty or val.empty or test.empty:
        raise ValueError(
            "A chronological split is empty. Inspect label coverage."
        )

    if train["y"].nunique() < 2:
        raise ValueError(
            "Training split contains only one class."
        )

    if val["y"].nunique() < 2:
        raise ValueError(
            "Validation split must contain both classes to tune F1."
        )

    # Never use IDs, labels, or the time-step column as model features.
    blocked = {
        "txId", "label_name", "class", "label",
        "target", "time_step", "y",
    }

    feature_cols = [
        col for col in merged.columns
        if col not in blocked
        and pd.api.types.is_numeric_dtype(merged[col])
]

    if not feature_cols:
        raise ValueError("No numeric features available.")

    def prepare_features(frame):
        return (
            frame[feature_cols]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0)
        )

    X_train = prepare_features(train)
    X_val = prepare_features(val)
    X_test = prepare_features(test)

    y_train = train["y"].to_numpy()
    y_val = val["y"].to_numpy()
    y_test = test["y"].to_numpy()

    negatives = int(np.sum(y_train == 0))
    positives = int(np.sum(y_train == 1))

    if positives == 0:
        raise ValueError("No illicit examples in the training split.")

    imbalance_ratio = negatives / positives

    # Candidate configurations are selected on validation data only.
    # Class weighting may improve recall but can reduce precision.
    candidate_configs = [
        {
            "scale_pos_weight": 1.0,
            "max_depth": 4,
            "learning_rate": 0.04,
            "n_estimators": 600,
            "min_child_weight": 3,
        },
        {
            "scale_pos_weight": 2.0,
            "max_depth": 4,
            "learning_rate": 0.04,
            "n_estimators": 600,
            "min_child_weight": 3,
        },
        {
            "scale_pos_weight": 3.0,
            "max_depth": 5,
            "learning_rate": 0.04,
            "n_estimators": 700,
            "min_child_weight": 3,
        },
        {
            "scale_pos_weight": min(imbalance_ratio, 10.0),
            "max_depth": 4,
            "learning_rate": 0.04,
            "n_estimators": 600,
            "min_child_weight": 5,
        },
        {
            "scale_pos_weight": min(imbalance_ratio, 10.0),
            "max_depth": 6,
            "learning_rate": 0.03,
            "n_estimators": 800,
            "min_child_weight": 5,
        },
    ]

    # Use a fixed, validation-only threshold grid.
    thresholds = np.arange(0.05, 0.951, 0.01)

    best_f1 = -1.0
    best_threshold = 0.5
    best_model = None
    best_name = None
    best_config = None
    best_val_precision = 0.0
    best_val_recall = 0.0

    for config in candidate_configs:
        model, name = _make_estimator(**config)

        _fit_estimator(
            model,
            name,
            X_train,
            y_train,
            config["scale_pos_weight"],
        )

        val_prob = model.predict_proba(X_val)[:, 1]

        for threshold in thresholds:
            val_pred = (val_prob >= threshold).astype(int)

            score = f1_score(
                y_val, val_pred, zero_division=0
            )

            # Maximize F1; break ties in favor of higher precision.
            precision = precision_score(
                y_val, val_pred, zero_division=0
            )
            recall = recall_score(
                y_val, val_pred, zero_division=0
            )

            is_better = score > best_f1 + 1e-12
            is_tie_better = (
                abs(score - best_f1) <= 1e-12
                and precision > best_val_precision
            )

            if is_better or is_tie_better:
                best_f1 = float(score)
                best_threshold = float(threshold)
                best_model = model
                best_name = name
                best_config = config.copy()
                best_val_precision = float(precision)
                best_val_recall = float(recall)

    if best_model is None:
        raise RuntimeError("No candidate model was selected.")

    # Evaluate on the held-out test period after selection is complete.
    test_prob = best_model.predict_proba(X_test)[:, 1]
    test_pred = (test_prob >= best_threshold).astype(int)

    metrics = _metrics(y_test, test_pred, test_prob)

    metrics.update({
        "estimator": best_name,
        "selected_model_config": best_config,
        "threshold_selected_on_validation": best_threshold,
        "validation_f1_illicit": best_f1,
        "validation_precision_illicit": best_val_precision,
        "validation_recall_illicit": best_val_recall,
        "training_class_imbalance_ratio": float(imbalance_ratio),
        "label_counts_in_loaded_data": {
            str(k): int(v) for k, v in counts.items()
        },
        "split": split_info,
        "n_features": len(feature_cols),
        "feature_columns": feature_cols,
        "data_sources": sources,
        "warning": (
            "Elliptic++ is Bitcoin data. Results do not establish "
            "Ethereum/DeFi performance or real-world attribution. "
            "Graph features must be audited for temporal leakage."
        ),
    })

    joblib.dump(
        {
            "model": best_model,
            "feature_columns": feature_cols,
            "threshold": best_threshold,
            "estimator_name": best_name,
        },
        output / "model.joblib",
    )

    predictions = pd.DataFrame({
        "txId": test["txId"].astype(str).values,
        "time_step": test["time_step"].values,
        "true_label": test["label_name"].values,
        "illicit_score": test_prob,
        "predicted_label": np.where(
            test_pred == 1,
            "illicit_pattern",
            "licit_pattern",
        ),
    })

    predictions.to_csv(
        output / "predictions.csv", index=False
    )

    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str),
        encoding="utf-8",
    )

    (output / "feature_columns.json").write_text(
        json.dumps(feature_cols, indent=2),
        encoding="utf-8",
    )

    model_obj = best_model
    if (
        hasattr(best_model, "named_steps")
        and "model" in best_model.named_steps
    ):
        model_obj = best_model.named_steps["model"]

    if hasattr(model_obj, "feature_importances_"):
        importance = model_obj.feature_importances_
        pd.DataFrame({
            "feature": feature_cols,
            "importance": importance,
        }).sort_values(
            "importance", ascending=False
        ).to_csv(
            output / "feature_importance.csv", index=False
        )

    elif hasattr(model_obj, "coef_"):
        pd.DataFrame({
            "feature": feature_cols,
            "importance": np.abs(model_obj.coef_[0]),
        }).sort_values(
            "importance", ascending=False
        ).to_csv(
            output / "feature_importance.csv", index=False
        )

    print(
        f"Selected configuration: {best_config}\n"
        f"Validation F1: {best_f1:.4f}\n"
        f"Validation threshold: {best_threshold:.2f}\n"
        f"Test accuracy: {metrics['accuracy']:.4f}\n"
        f"Test illicit precision: {metrics['precision_illicit']:.4f}\n"
        f"Test illicit recall: {metrics['recall_illicit']:.4f}\n"
        f"Test illicit F1: {metrics['f1_illicit']:.4f}"
    )

    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Train ASTRA's Elliptic++ transaction classifier."
    )
    parser.add_argument(
        "--data-dir", default="data/ellipticpp"
    )
    parser.add_argument(
        "--output-dir", default="artifacts"
    )
    args = parser.parse_args()

    metrics = run_training(
        args.data_dir,
        args.output_dir,
    )
    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
