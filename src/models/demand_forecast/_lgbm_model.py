"""LightGBM global demand forecasting model.

A single LightGBM model is trained on ALL active SKUs simultaneously (global model).
This lets it learn cross-SKU patterns (seasonality, trend) that per-SKU classical
models cannot detect when individual series are short.

Prediction is recursive: predict t+1, append to history, predict t+2, …, t+h.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from src.models.demand_forecast._feature_eng import (
    _FEATURE_COLS,
    _LAGS,
    holdout_split,
    mae,
    make_features,
)

_LGBM_PARAMS: dict = {
    "objective":         "regression_l1",   # MAE objective
    "metric":            "mae",
    "num_leaves":        63,
    "learning_rate":     0.05,
    "feature_fraction":  0.8,
    "bagging_fraction":  0.8,
    "bagging_freq":      5,
    "min_child_samples": 10,
    "verbose":           -1,
    "n_jobs":            -1,
}
_N_ROUNDS = 300
_EARLY_STOP = 30


def _train(X: pd.DataFrame, y: pd.Series) -> object:
    import lightgbm as lgb

    mask = y.notna() & (X.notna().all(axis=1))
    ds = lgb.Dataset(X[mask], label=y[mask])
    return lgb.train(_LGBM_PARAMS, ds, num_boost_round=_N_ROUNDS)


def _predict_one_step(model: object, feature_row: pd.DataFrame) -> float:
    pred = model.predict(feature_row)[0]
    return float(max(0.0, pred))


def _build_next_row(
    uid: str,
    history: list[float],
    future_months: list[pd.Timestamp],
    step: int,
    abc_code: float,
    cv: float,
    p_zero: float,
) -> pd.DataFrame:
    """Construct a single feature row for recursive prediction."""
    ts = future_months[step]
    n = len(history)
    row: dict[str, float] = {}

    for lag in _LAGS:
        idx = n - lag
        row[f"lag_{lag}"] = history[idx] if idx >= 0 else 0.0

    for w in [3, 6, 12]:
        window = history[max(0, n - w):]
        row[f"roll_mean_{w}"] = float(np.mean(window)) if window else 0.0
        row[f"roll_std_{w}"]  = float(np.std(window, ddof=0)) if len(window) > 1 else 0.0

    row["month"]    = float(ts.month)
    row["quarter"]  = float(ts.quarter)
    row["abc_code"] = abc_code
    row["cv"]       = cv
    row["p_zero"]   = p_zero

    return pd.DataFrame([row], columns=_FEATURE_COLS)


def forecast_lgbm(
    panel: pd.DataFrame,
    classified: pd.DataFrame,
    h: int = 3,
) -> pd.DataFrame:
    """Train a LightGBM global model and return h-step ahead forecasts.

    Args:
        panel: (unique_id, ds, y) panel for the SKU group (train only).
        classified: abc_xyz_fsn DataFrame for SKU-level covariates.
        h: forecast horizon in months.

    Returns:
        DataFrame [unique_id, forecast_m1 … forecast_mh, forecast_lt, method="LightGBM"].
    """
    import lightgbm as lgb  # noqa: F401 — confirms import

    feat = make_features(panel, classified)
    X = feat[_FEATURE_COLS]
    y = feat["y"]

    logger.debug(f"LightGBM training on {len(panel['unique_id'].unique()):,} SKUs, {len(feat):,} rows")
    model = _train(X, y)

    # Build metadata map per SKU for recursive prediction
    meta = (
        classified[["material_9", "abc", "cv", "p_zero"]]
        .copy()
        .set_index("material_9")
    )
    meta["abc_code"] = meta["abc"].map({"A": 0, "B": 1, "C": 2}).fillna(2)

    last_ds = panel.groupby("unique_id")["ds"].max()
    records: list[dict] = []

    for uid, grp in panel.groupby("unique_id"):
        grp  = grp.sort_values("ds")
        hist = grp["y"].tolist()
        last = last_ds[uid]
        future = [last + pd.DateOffset(months=i + 1) for i in range(h)]

        uid_meta = meta.loc[uid] if uid in meta.index else pd.Series({"abc_code": 2, "cv": 0.0, "p_zero": 1.0})
        abc_code = float(uid_meta["abc_code"])
        cv_      = float(uid_meta["cv"])
        pz_      = float(uid_meta["p_zero"])

        preds: list[float] = []
        for step in range(h):
            row  = _build_next_row(uid, hist + preds, future, step, abc_code, cv_, pz_)
            pred = _predict_one_step(model, row)
            preds.append(pred)

        row_d = {"unique_id": uid}
        for i, p in enumerate(preds, 1):
            row_d[f"forecast_m{i}"] = p
        row_d["forecast_lt"] = sum(preds)
        row_d["method"]      = "LightGBM"
        records.append(row_d)

    return pd.DataFrame(records)


def evaluate_lgbm(
    panel: pd.DataFrame,
    classified: pd.DataFrame,
    h: int = 3,
) -> dict[str, float]:
    """Return per-SKU holdout MAE for the LightGBM model.

    Trains on panel minus last h months, predicts last h months.
    Only SKUs with > h rows in their train split are included.
    """
    train_panel, test_panel = holdout_split(panel, h=h)

    # Only keep SKUs that have training data after the split
    valid_uids = train_panel.groupby("unique_id").size()
    valid_uids = valid_uids[valid_uids > 0].index
    train_panel = train_panel[train_panel["unique_id"].isin(valid_uids)]
    test_panel  = test_panel[test_panel["unique_id"].isin(valid_uids)]

    if train_panel.empty or test_panel.empty:
        return {}

    forecasts = forecast_lgbm(train_panel, classified, h=h)

    mae_map: dict[str, float] = {}
    for uid, grp in test_panel.groupby("unique_id"):
        if uid not in forecasts["unique_id"].values:
            continue
        frow = forecasts[forecasts["unique_id"] == uid].iloc[0]
        actuals = grp.sort_values("ds")["y"].tolist()
        preds   = [frow.get(f"forecast_m{i+1}", 0.0) for i in range(len(actuals))]
        mae_map[str(uid)] = mae(pd.Series(actuals), pd.Series(preds))

    return mae_map
