"""NHITS deep-learning demand forecasting model (via neuralforecast).

Applied only to Tier-4 SKUs: A/B-class, X/Y demand, ≥ 12 active months.
NHITS (Neural Hierarchical Interpolation for Time Series) decomposes the
forecast into multi-scale components and handles the daily/weekly/seasonal
patterns better than basic RNNs on medium-length series (12–41 months).

Requires: neuralforecast (installed via `uv add neuralforecast`).
If unavailable at runtime the module degrades gracefully: `available()` returns
False and callers fall back to LightGBM.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from loguru import logger

from src.models.demand_forecast._feature_eng import holdout_split, mae

_INPUT_SIZE  = 12   # months of lookback for NHITS
_MAX_STEPS   = 200  # training iterations
_BATCH_SIZE  = 32


def available() -> bool:
    """Return True if neuralforecast is importable."""
    try:
        import neuralforecast  # noqa: F401
        return True
    except ImportError:
        return False


def _make_nf_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Convert (unique_id, ds, y) panel to neuralforecast format."""
    nf = panel[["unique_id", "ds", "y"]].copy()
    nf["unique_id"] = nf["unique_id"].astype(str)
    nf["ds"]        = pd.to_datetime(nf["ds"])
    return nf.sort_values(["unique_id", "ds"]).reset_index(drop=True)


def _make_future_df(panel: pd.DataFrame, h: int) -> pd.DataFrame:
    """Build the futr_df required by neuralforecast predict()."""
    last_ds = panel.groupby("unique_id")["ds"].max().reset_index()
    rows: list[dict] = []
    for _, row in last_ds.iterrows():
        for step in range(1, h + 1):
            rows.append({
                "unique_id": str(row["unique_id"]),
                "ds":        row["ds"] + pd.DateOffset(months=step),
            })
    return pd.DataFrame(rows)


def forecast_nhits(
    panel: pd.DataFrame,
    h: int = 3,
) -> pd.DataFrame:
    """Fit NHITS and generate h-step ahead forecasts.

    Args:
        panel: (unique_id, ds, y) training panel.
        h: forecast horizon in months.

    Returns:
        DataFrame [unique_id, forecast_m1 … forecast_mh, forecast_lt, method="NHITS"].
    """
    from neuralforecast import NeuralForecast
    from neuralforecast.models import NHITS

    nf_panel = _make_nf_panel(panel)

    # Drop SKUs with fewer observations than input_size (NHITS minimum)
    obs_counts = nf_panel.groupby("unique_id")["y"].count()
    valid_uids = obs_counts[obs_counts >= _INPUT_SIZE].index
    nf_panel = nf_panel[nf_panel["unique_id"].isin(valid_uids)]

    if nf_panel.empty:
        logger.warning("NHITS: no SKUs have enough observations; skipping")
        return pd.DataFrame(columns=["unique_id", "forecast_m1", "forecast_m2", "forecast_m3", "forecast_lt", "method"])

    logger.debug(f"NHITS training on {nf_panel['unique_id'].nunique()} SKUs")

    model = NHITS(
        h=h,
        input_size=_INPUT_SIZE,
        max_steps=_MAX_STEPS,
        batch_size=_BATCH_SIZE,
        enable_progress_bar=False,
        enable_model_summary=False,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nf = NeuralForecast(models=[model], freq="MS")
        nf.fit(df=nf_panel)
        pred = nf.predict()

    pred_col = [c for c in pred.columns if c not in ("unique_id", "ds")][0]
    pred[pred_col] = pred[pred_col].clip(lower=0.0)

    pred = pred.sort_values(["unique_id", "ds"])
    pred["horizon"] = pred.groupby("unique_id").cumcount() + 1
    wide = pred.pivot(index="unique_id", columns="horizon", values=pred_col).reset_index()
    wide.columns = ["unique_id"] + [f"forecast_m{i}" for i in range(1, h + 1)]
    wide["forecast_lt"] = wide[[f"forecast_m{i}" for i in range(1, h + 1)]].sum(axis=1)
    wide["method"] = "NHITS"
    return wide


def evaluate_nhits(
    panel: pd.DataFrame,
    h: int = 3,
) -> dict[str, float]:
    """Return per-SKU holdout MAE for NHITS (train on all-but-last-h months)."""
    train_panel, test_panel = holdout_split(panel, h=h)

    # Only SKUs with ≥ input_size observations in training
    valid_uids = train_panel.groupby("unique_id").size()
    valid_uids = valid_uids[valid_uids >= _INPUT_SIZE].index
    train_panel = train_panel[train_panel["unique_id"].isin(valid_uids)]
    test_panel  = test_panel[test_panel["unique_id"].isin(valid_uids)]

    if train_panel.empty:
        return {}

    try:
        forecasts = forecast_nhits(train_panel, h=h)
    except Exception as exc:
        logger.warning(f"NHITS evaluation failed: {exc!r}")
        return {}

    mae_map: dict[str, float] = {}
    for uid, grp in test_panel.groupby("unique_id"):
        uid_str = str(uid)
        if uid_str not in forecasts["unique_id"].values:
            continue
        frow    = forecasts[forecasts["unique_id"] == uid_str].iloc[0]
        actuals = grp.sort_values("ds")["y"].tolist()
        preds   = [frow.get(f"forecast_m{i+1}", 0.0) for i in range(len(actuals))]
        mae_map[uid_str] = mae(pd.Series(actuals), pd.Series(preds))

    return mae_map
