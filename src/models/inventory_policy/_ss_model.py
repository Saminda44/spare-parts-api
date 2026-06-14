"""ML safety stock model: LightGBM quantile regression for demand-at-risk.

Classical safety stock (z × σ_lt) assumes demand is normally distributed — a
poor fit for spare parts which are typically intermittent, zero-inflated, and
heavy-tailed.  This module replaces the normality assumption with empirical
quantile regression:

  demand_at_risk_q = LightGBM.predict(features, quantile=q)
  safety_stock_ML  = max(0, demand_at_risk_q − point_forecast_lt)

Training data: for every (SKU, month t) in the movement history, the target is
the sum of demand over the next 3 months (one full lead time).  Features at
time t are the same lag / rolling / calendar / SKU-level covariates used by
Stage 10's LightGBM forecast model.

Tiered complexity (mirrors Stage 10 philosophy):
  Tier 0 – non-movers (active_months == 0):          SS = 0
  Tier 1 – sparse (active_months < _MIN_ACTIVE_ML):  SS = classical z × σ_lt
  Tier 2+ – active (active_months ≥ _MIN_ACTIVE_ML): SS = max(0, quantile − forecast_lt)

One LightGBM model is trained per quantile level (0.90 / 0.95 / 0.975 / 0.99).
The correct model is selected per SKU based on its policy_tier.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from src.models.demand_forecast._feature_eng import _FEATURE_COLS, make_features

# Minimum active months for a SKU to be included in ML safety stock
_MIN_ACTIVE_ML: int = 6

# Minimum number of ML-eligible SKUs for the global model to be meaningful
_MIN_SKUS_ML: int = 5

# Minimum number of training windows (SKU × month combinations) for fitting
_MIN_TRAINING_ROWS: int = 50

# Quantile for each policy tier
QUANTILE_MAP: dict[str, float] = {
    "critical":    0.990,
    "managed":     0.975,
    "watch":       0.950,
    "rationalise": 0.900,
}

_LGBM_PARAMS_BASE: dict = {
    "n_estimators":    300,
    "learning_rate":   0.05,
    "num_leaves":      31,
    "min_child_samples": 5,
    "subsample":       0.8,
    "colsample_bytree": 0.8,
    "random_state":    42,
    "verbose":         -1,
}

# Calibration tolerance: fraction of samples allowed to exceed the quantile
_CALIBRATION_WARN_SLACK: float = 0.05


# ---------------------------------------------------------------------------
# Training data construction
# ---------------------------------------------------------------------------

def build_quantile_dataset(
    monthly_demand: pd.DataFrame,
    classified: pd.DataFrame,
    ml_skus: list[str],
) -> tuple[pd.DataFrame, pd.Series]:
    """Build (X, y) training data for quantile regression.

    For each (SKU, month t) in the history, the target y is the total demand
    over months t+1, t+2, t+3 (one import lead time ahead).  The last 3 rows
    per SKU have no complete target and are dropped.

    Args:
        monthly_demand: monthly_demand.parquet as produced by Stage 7.
        classified: abc_xyz_fsn.parquet from Stage 9.
        ml_skus: list of SKU codes eligible for ML (active_months ≥ threshold).

    Returns:
        X: feature DataFrame (index reset).
        y: target Series — 3-month forward demand (index aligned with X).
    """
    sub = monthly_demand[monthly_demand["material_9"].isin(ml_skus)].copy()

    # Build (unique_id, ds, y) panel
    panel = (
        sub[["material_9", "year_month_str", "issue_qty"]]
        .rename(columns={"material_9": "unique_id", "year_month_str": "ds", "issue_qty": "y"})
        .copy()
    )
    panel["ds"] = pd.to_datetime(panel["ds"] + "-01")
    panel = panel.sort_values(["unique_id", "ds"]).reset_index(drop=True)

    # 3-month forward rolling sum per SKU (shift by -1, -2, -3)
    grp = panel.groupby("unique_id")["y"]
    panel["y_fwd1"] = grp.shift(-1)
    panel["y_fwd2"] = grp.shift(-2)
    panel["y_fwd3"] = grp.shift(-3)
    panel["y_target"] = panel["y_fwd1"].fillna(0) + panel["y_fwd2"].fillna(0) + panel["y_fwd3"].fillna(0)

    # Drop the last 3 rows per SKU (incomplete forward window)
    rev_count = panel.groupby("unique_id").cumcount(ascending=False)
    panel = panel[rev_count >= 3].copy()

    # Add feature columns
    panel_feat = make_features(panel, classified)

    feat_cols = [c for c in _FEATURE_COLS if c in panel_feat.columns]
    X = panel_feat[feat_cols].copy()
    y = panel_feat["y_target"].copy()

    logger.debug(
        f"Quantile dataset: {len(X):,} rows from {panel_feat['unique_id'].nunique():,} SKUs | "
        f"features: {len(feat_cols)}"
    )
    return X.reset_index(drop=True), y.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def train_quantile_models(
    X: pd.DataFrame,
    y: pd.Series,
) -> dict[float, object]:
    """Train one LightGBM quantile model per quantile level.

    Business meaning: separate models for each quantile because LGBM's quantile
    loss is non-differentiable across quantiles — joint multi-quantile training
    can violate the monotonicity constraint.  Four separate models are a cheap
    and robust solution for our four service-level targets.

    Args:
        X: feature matrix (from build_quantile_dataset).
        y: 3-month forward demand target.

    Returns:
        dict mapping float quantile → fitted LGBMRegressor.
    """
    from lightgbm import LGBMRegressor

    models: dict[float, object] = {}
    for quantile in sorted(set(QUANTILE_MAP.values())):
        params = {**_LGBM_PARAMS_BASE, "objective": "quantile", "alpha": quantile}
        model  = LGBMRegressor(**params)
        model.fit(X, y)
        # Coverage check: fraction of training samples where pred < actual
        preds    = model.predict(X)
        coverage = float((y.values <= preds).mean())
        logger.debug(
            f"  Quantile q={quantile:.3f}: train coverage={coverage:.3f} "
            f"(target ≈ {quantile:.3f})"
        )
        if abs(coverage - quantile) > _CALIBRATION_WARN_SLACK:
            logger.warning(
                f"  q={quantile:.3f} model training coverage ({coverage:.3f}) "
                f"deviates from target by >{_CALIBRATION_WARN_SLACK:.2f} — check data quality"
            )
        models[quantile] = model

    logger.info(f"Quantile models trained: {list(models.keys())}")
    return models


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def predict_demand_at_risk(
    models: dict[float, object],
    policy_df: pd.DataFrame,
    monthly_demand: pd.DataFrame,
    classified: pd.DataFrame,
    ml_skus: list[str],
) -> pd.Series:
    """Predict demand-at-risk at each SKU's service level quantile.

    Uses the most recent available feature vector for each SKU (last month in
    history) to project forward-looking demand-at-risk.

    Args:
        models: trained quantile models from train_quantile_models().
        policy_df: current policy DataFrame with policy_tier column.
        monthly_demand: monthly_demand.parquet (Stage 7).
        classified: abc_xyz_fsn.parquet (Stage 9).
        ml_skus: SKUs eligible for ML.

    Returns:
        Series indexed by material_9 with predicted demand-at-risk (q-th quantile
        of 3-month forward demand).
    """
    sub = monthly_demand[monthly_demand["material_9"].isin(ml_skus)].copy()
    panel = (
        sub[["material_9", "year_month_str", "issue_qty"]]
        .rename(columns={"material_9": "unique_id", "year_month_str": "ds", "issue_qty": "y"})
        .copy()
    )
    panel["ds"] = pd.to_datetime(panel["ds"] + "-01")
    panel = panel.sort_values(["unique_id", "ds"]).reset_index(drop=True)

    panel_feat = make_features(panel, classified)
    feat_cols  = [c for c in _FEATURE_COLS if c in panel_feat.columns]

    # Take the LAST row per SKU (most recent feature vector for forecasting)
    latest = panel_feat.sort_values("ds").groupby("unique_id").tail(1).copy()

    # Build tier → quantile lookup per SKU
    tier_map = (
        policy_df[policy_df["material_9"].isin(ml_skus)]
        .set_index("material_9")["policy_tier"]
        .fillna("rationalise")
        .map(QUANTILE_MAP)
        .to_dict()
    )

    X_latest  = latest[feat_cols].fillna(0.0)
    uid_list  = latest["unique_id"].values
    results: dict[str, float] = {}

    for quantile, model in models.items():
        preds = model.predict(X_latest)
        mask  = np.array([tier_map.get(uid, 0.90) == quantile for uid in uid_list])
        for uid, pred in zip(uid_list[mask], preds[mask]):
            results[str(uid)] = float(max(0.0, pred))

    return pd.Series(results, name="demand_at_risk")


# ---------------------------------------------------------------------------
# Safety stock computation (tiered)
# ---------------------------------------------------------------------------

def compute_ml_safety_stock(
    policy_df: pd.DataFrame,
    monthly_demand: pd.DataFrame,
    classified: pd.DataFrame,
) -> pd.DataFrame:
    """Replace z × σ safety stock with ML quantile estimates for active SKUs.

    Tiers:
      Non-movers (active_months == 0): SS = 0
      Sparse (active_months < _MIN_ACTIVE_ML): SS unchanged (classical z × σ already set)
      Active (active_months ≥ _MIN_ACTIVE_ML): SS = max(0, quantile_pred − forecast_lt)

    The method column is set to 'ML-Quantile' for SKUs where ML was applied, and
    'Classical' for those falling back to the z × σ formula.

    Args:
        policy_df: DataFrame after compute_safety_stock() has set the classical SS.
        monthly_demand: monthly_demand.parquet.
        classified: abc_xyz_fsn.parquet.

    Returns:
        policy_df with safety_stock and ss_method columns updated.
    """
    df = policy_df.copy()
    df["ss_method"] = "Classical"

    # Identify ML-eligible SKUs
    ml_mask = (df["active_months"] >= _MIN_ACTIVE_ML) & (df["avg_monthly_demand"] > 0.0)
    ml_skus  = df.loc[ml_mask, "material_9"].tolist()

    if len(ml_skus) < _MIN_SKUS_ML:
        logger.warning(
            f"Too few ML-eligible SKUs ({len(ml_skus)}) — keeping classical safety stock for all"
        )
        return df

    logger.info(f"ML safety stock: {len(ml_skus):,} eligible SKUs (active_months ≥ {_MIN_ACTIVE_ML})")

    # Build training data and train
    X_train, y_train = build_quantile_dataset(monthly_demand, classified, ml_skus)

    if len(X_train) < _MIN_TRAINING_ROWS:
        logger.warning("Too few training windows — keeping classical safety stock")
        return df

    logger.info(f"Training quantile models on {len(X_train):,} windows ...")
    models = train_quantile_models(X_train, y_train)

    # Predict demand-at-risk for the latest feature vector
    dar = predict_demand_at_risk(models, df, monthly_demand, classified, ml_skus)

    # Merge back and recompute safety stock
    ml_idx = df["material_9"].isin(ml_skus) & df["material_9"].isin(dar.index)
    df.loc[ml_idx, "safety_stock"] = (
        (dar.reindex(df.loc[ml_idx, "material_9"].values).values
         - df.loc[ml_idx, "forecast_lt"].values)
        .clip(min=0.0)
    )
    df.loc[ml_idx, "ss_method"] = "ML-Quantile"

    n_ml = int(ml_idx.sum())
    logger.info(
        f"ML safety stock applied to {n_ml:,} SKUs | "
        f"Classical retained for {len(df) - n_ml:,} SKUs"
    )
    return df
