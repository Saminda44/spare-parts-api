"""Lag / rolling feature engineering shared by LightGBM and evaluation helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

_LAGS = [1, 2, 3, 6, 12]
_ROLL_WINDOWS = [3, 6, 12]

_FEATURE_COLS: list[str] = (
    [f"lag_{l}" for l in _LAGS]
    + [f"roll_mean_{w}" for w in _ROLL_WINDOWS]
    + [f"roll_std_{w}" for w in _ROLL_WINDOWS]
    + ["month", "quarter", "abc_code", "cv", "p_zero"]
)


def make_features(
    panel: pd.DataFrame,
    classified: pd.DataFrame,
) -> pd.DataFrame:
    """Add lag / rolling / calendar / SKU-level features to a (unique_id, ds, y) panel.

    Business meaning: lag features capture recent demand momentum; rolling stats
    capture the typical demand level and variability; calendar features capture
    seasonal patterns (e.g. end-of-year parts orders); SKU-level features let the
    global model distinguish high-value from low-value SKUs without needing
    separate per-SKU models.

    Args:
        panel: DataFrame with columns [unique_id, ds, y], sorted by (unique_id, ds).
        classified: abc_xyz_fsn DataFrame for SKU-level covariates.

    Returns:
        panel with feature columns appended; rows with all-NaN lags are kept
        (they get 0-filled) so the panel shape is unchanged.
    """
    df = panel.sort_values(["unique_id", "ds"]).copy()

    grp = df.groupby("unique_id")["y"]

    for lag in _LAGS:
        df[f"lag_{lag}"] = grp.shift(lag)

    for w in _ROLL_WINDOWS:
        shifted = grp.shift(1)
        df[f"roll_mean_{w}"] = shifted.transform(
            lambda x: x.rolling(w, min_periods=1).mean()
        )
        df[f"roll_std_{w}"] = shifted.transform(
            lambda x: x.rolling(w, min_periods=1).std().fillna(0.0)
        )

    df["month"]   = df["ds"].dt.month
    df["quarter"] = df["ds"].dt.quarter

    # ABC code as ordinal (A=0, B=1, C=2)
    abc_map = classified[["material_9", "abc", "cv", "p_zero"]].copy()
    abc_map["abc_code"] = abc_map["abc"].map({"A": 0, "B": 1, "C": 2}).fillna(2)
    df = df.merge(
        abc_map[["material_9", "abc_code", "cv", "p_zero"]].rename(
            columns={"material_9": "unique_id"}
        ),
        on="unique_id",
        how="left",
    )
    df["abc_code"] = df["abc_code"].fillna(2)
    df["cv"]       = df["cv"].fillna(0.0)
    df["p_zero"]   = df["p_zero"].fillna(1.0)

    # Fill remaining NaNs (early lag rows) with 0
    for col in _FEATURE_COLS:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)

    return df


def holdout_split(
    panel: pd.DataFrame, h: int = 3
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split panel into train / test by holding out last h months per SKU.

    SKUs with ≤ h total rows have no test split (all training).
    """
    panel = panel.copy().sort_values(["unique_id", "ds"]).reset_index(drop=True)
    # Reverse cumulative count: 0 = last row, 1 = second-to-last, etc.
    panel["_rev"] = panel.groupby("unique_id").cumcount(ascending=False)
    grp_size = panel.groupby("unique_id")["unique_id"].transform("count")
    is_test = (panel["_rev"] < h) & (grp_size > h)
    train = panel[~is_test].drop(columns=["_rev"]).reset_index(drop=True)
    test  = panel[is_test].drop(columns=["_rev"]).reset_index(drop=True)
    return train, test


def mae(actual: pd.Series, predicted: pd.Series) -> float:
    """Mean absolute error, ignoring NaN pairs."""
    mask = actual.notna() & predicted.notna()
    if mask.sum() == 0:
        return float("inf")
    return float(np.abs(actual[mask].values - predicted[mask].values).mean())
