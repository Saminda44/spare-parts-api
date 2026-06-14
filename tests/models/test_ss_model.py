"""Unit tests for the ML safety stock quantile model."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.inventory_policy._ss_model import (
    QUANTILE_MAP,
    _MIN_ACTIVE_ML,
    build_quantile_dataset,
    compute_ml_safety_stock,
    predict_demand_at_risk,
    train_quantile_models,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _months(start: str, n: int) -> list[str]:
    base = pd.Timestamp(start + "-01")
    return [(base + pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(n)]


def _make_demand(sku: str, n_months: int, qty: float, start: str = "2022-01") -> pd.DataFrame:
    months = _months(start, n_months)
    return pd.DataFrame({
        "material_9":    [sku] * n_months,
        "year_month_str": months,
        "issue_qty":     [qty] * n_months,
        "net_demand":    [qty] * n_months,
        "issue_value_lkr": [qty * 500.0] * n_months,
        "return_qty":    [0.0] * n_months,
        "description":   ["TEST"] * n_months,
    })


def _make_classified(skus: list[str], active_months: int = 12) -> pd.DataFrame:
    return pd.DataFrame({
        "material_9":            skus,
        "description":           ["TEST"] * len(skus),
        "abc":                   ["B"] * len(skus),
        "xyz":                   ["X"] * len(skus),
        "fsn":                   ["F"] * len(skus),
        "abc_xyz_fsn":           ["BXF"] * len(skus),
        "policy_tier":           ["managed"] * len(skus),
        "avg_monthly_demand":    [5.0] * len(skus),
        "cv":                    [0.3] * len(skus),
        "p_zero":                [0.1] * len(skus),
        "active_months":         [active_months] * len(skus),
        "total_issue_value_lkr": [30_000.0] * len(skus),
    })


def _make_policy_row(sku: str, active_months: int, demand: float, policy_tier: str = "managed") -> dict:
    return {
        "material_9":         sku,
        "description":        "TEST",
        "abc":                "B",
        "xyz":                "X",
        "fsn":                "F",
        "abc_xyz_fsn":        "BXF",
        "policy_tier":        policy_tier,
        "active_months":      active_months,
        "avg_monthly_demand": demand,
        "total_issue_value_lkr": demand * active_months * 500.0,
        "forecast_lt":        demand * 3,
        "forecast_m1":        demand,
        "forecast_m2":        demand,
        "forecast_m3":        demand,
        "demand_std_monthly": demand * 0.3,
        "demand_std_lt":      demand * 0.3 * (3 ** 0.5),
        "stock_on_hand":      demand * 4,
        "coverage_months":    4.0,
        "stock_status":       "ok",
        "unit_value_lkr":     500.0,
        "service_level":      0.975,
        "z_score":            1.960,
        "safety_stock":       demand * 0.3 * (3 ** 0.5) * 1.960,
        "cv":                 0.3,
    }


# ---------------------------------------------------------------------------
# QUANTILE_MAP
# ---------------------------------------------------------------------------

class TestQuantileMap:
    def test_all_tiers_present(self) -> None:
        for tier in ("critical", "managed", "watch", "rationalise"):
            assert tier in QUANTILE_MAP

    def test_quantiles_ordered(self) -> None:
        assert QUANTILE_MAP["critical"] > QUANTILE_MAP["managed"]
        assert QUANTILE_MAP["managed"] > QUANTILE_MAP["watch"]
        assert QUANTILE_MAP["watch"]   > QUANTILE_MAP["rationalise"]

    def test_all_quantiles_in_range(self) -> None:
        for q in QUANTILE_MAP.values():
            assert 0.5 < q < 1.0


# ---------------------------------------------------------------------------
# build_quantile_dataset
# ---------------------------------------------------------------------------

class TestBuildQuantileDataset:
    def _make_multi_sku_data(self, n_skus: int = 5, n_months: int = 18) -> tuple[pd.DataFrame, pd.DataFrame]:
        skus = [f"SKU{i}" for i in range(n_skus)]
        demand = pd.concat([
            _make_demand(sku, n_months, qty=float(i + 1) * 3.0)
            for i, sku in enumerate(skus)
        ], ignore_index=True)
        classified = _make_classified(skus, active_months=n_months)
        return demand, classified

    def test_returns_tuple_of_X_y(self) -> None:
        demand, classified = self._make_multi_sku_data()
        X, y = build_quantile_dataset(demand, classified, [f"SKU{i}" for i in range(5)])
        assert isinstance(X, pd.DataFrame)
        assert isinstance(y, pd.Series)

    def test_X_and_y_same_length(self) -> None:
        demand, classified = self._make_multi_sku_data()
        X, y = build_quantile_dataset(demand, classified, [f"SKU{i}" for i in range(5)])
        assert len(X) == len(y)

    def test_last_3_rows_dropped_per_sku(self) -> None:
        # 5 SKUs × 18 months = 90 total, last 3 per SKU dropped → 75 rows
        demand, classified = self._make_multi_sku_data(n_skus=5, n_months=18)
        X, y = build_quantile_dataset(demand, classified, [f"SKU{i}" for i in range(5)])
        assert len(X) == 5 * (18 - 3)

    def test_y_is_non_negative(self) -> None:
        demand, classified = self._make_multi_sku_data()
        _, y = build_quantile_dataset(demand, classified, [f"SKU{i}" for i in range(5)])
        assert (y >= 0.0).all()

    def test_feature_columns_present(self) -> None:
        demand, classified = self._make_multi_sku_data()
        X, _ = build_quantile_dataset(demand, classified, [f"SKU{i}" for i in range(5)])
        assert "lag_1" in X.columns
        assert "roll_mean_3" in X.columns
        assert "month" in X.columns


# ---------------------------------------------------------------------------
# train_quantile_models
# ---------------------------------------------------------------------------

class TestTrainQuantileModels:
    def _make_training_data(self, n: int = 200) -> tuple[pd.DataFrame, pd.Series]:
        rng = np.random.default_rng(42)
        X = pd.DataFrame({
            "lag_1":       rng.poisson(5, n).astype(float),
            "lag_2":       rng.poisson(5, n).astype(float),
            "lag_3":       rng.poisson(5, n).astype(float),
            "roll_mean_3": rng.uniform(3, 8, n),
            "roll_std_3":  rng.uniform(0, 3, n),
            "month":       rng.integers(1, 13, n).astype(float),
            "abc_code":    rng.integers(0, 3, n).astype(float),
            "cv":          rng.uniform(0.1, 0.8, n),
            "p_zero":      rng.uniform(0, 0.5, n),
        })
        y = pd.Series(rng.poisson(15, n).astype(float))
        return X, y

    def test_returns_dict_of_models(self) -> None:
        X, y = self._make_training_data()
        models = train_quantile_models(X, y)
        assert isinstance(models, dict)
        assert len(models) > 0

    def test_all_quantiles_have_model(self) -> None:
        X, y = self._make_training_data()
        models = train_quantile_models(X, y)
        for q in set(QUANTILE_MAP.values()):
            assert q in models

    def test_models_are_fitted(self) -> None:
        X, y = self._make_training_data()
        models = train_quantile_models(X, y)
        for model in models.values():
            preds = model.predict(X)
            assert len(preds) == len(X)

    def test_higher_quantile_gives_higher_prediction(self) -> None:
        """q=0.99 predictions should generally exceed q=0.90 predictions."""
        X, y = self._make_training_data(n=500)
        models = train_quantile_models(X, y)
        preds_high = models[0.990].predict(X)
        preds_low  = models[0.900].predict(X)
        # On average, the higher quantile model gives higher predictions
        assert preds_high.mean() > preds_low.mean()


# ---------------------------------------------------------------------------
# compute_ml_safety_stock (integration)
# ---------------------------------------------------------------------------

class TestComputeMlSafetyStock:
    def _make_scenario(
        self,
        n_skus: int = 10,
        n_months: int = 18,
        active_months_val: int = 18,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Return (policy_df, monthly_demand, classified) for integration tests."""
        skus = [f"SKU{i}" for i in range(n_skus)]
        demand = pd.concat([
            _make_demand(sku, n_months, qty=float(i + 2) * 2.0)
            for i, sku in enumerate(skus)
        ], ignore_index=True)
        classified = _make_classified(skus, active_months=active_months_val)
        policy_rows = [
            _make_policy_row(sku, active_months_val, float(i + 2) * 2.0)
            for i, sku in enumerate(skus)
        ]
        policy_df = pd.DataFrame(policy_rows)
        return policy_df, demand, classified

    def test_ss_method_column_added(self) -> None:
        policy_df, demand, classified = self._make_scenario()
        result = compute_ml_safety_stock(policy_df, demand, classified)
        assert "ss_method" in result.columns

    def test_active_skus_get_ml_method(self) -> None:
        policy_df, demand, classified = self._make_scenario(
            n_months=18, active_months_val=18
        )
        result = compute_ml_safety_stock(policy_df, demand, classified)
        ml_rows = result[result["ss_method"] == "ML-Quantile"]
        assert len(ml_rows) > 0

    def test_sparse_skus_keep_classical(self) -> None:
        """SKUs with active_months < _MIN_ACTIVE_ML should remain Classical."""
        skus = [f"SPARSE{i}" for i in range(5)]
        demand = pd.concat([
            _make_demand(sku, 3, qty=5.0)  # only 3 months — below threshold
            for sku in skus
        ], ignore_index=True)
        classified = _make_classified(skus, active_months=3)
        policy_rows = [_make_policy_row(sku, 3, 5.0) for sku in skus]
        policy_df   = pd.DataFrame(policy_rows)
        result = compute_ml_safety_stock(policy_df, demand, classified)
        # All sparse — should either be Classical or unchanged (ML model not applied)
        assert (result["ss_method"] == "Classical").all()

    def test_safety_stock_non_negative(self) -> None:
        policy_df, demand, classified = self._make_scenario()
        result = compute_ml_safety_stock(policy_df, demand, classified)
        assert (result["safety_stock"] >= 0.0).all()

    def test_output_has_same_row_count(self) -> None:
        policy_df, demand, classified = self._make_scenario()
        result = compute_ml_safety_stock(policy_df, demand, classified)
        assert len(result) == len(policy_df)
