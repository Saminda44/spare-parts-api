"""Unit tests for Stage 10 demand forecast functions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.demand_forecast.stage10_demand_forecast import (
    _zero_forecast,
    build_panel,
    compute_demand_stats,
    compute_forecasts,
    method_summary,
    summary_kpis,
    top_forecast_skus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_demand(rows: list[dict]) -> pd.DataFrame:
    defaults: dict = {
        "material_9":       "1GC-E4450-00",
        "year_month_str":   "2025-01",
        "issue_qty":        10.0,
        "issue_value_lkr":  5000.0,
        "return_qty":       0.0,
        "net_demand":       10.0,
        "description":      "TEST PART",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _make_classified(rows: list[dict]) -> pd.DataFrame:
    defaults: dict = {
        "material_9":             "1GC-E4450-00",
        "description":            "TEST PART",
        "active_months":          5,
        "xyz":                    "X",
        "abc":                    "B",
        "fsn":                    "F",
        "abc_xyz_fsn":            "BXF",
        "policy_tier":            "managed",
        "total_issue_value_lkr":  25000.0,
        "avg_monthly_demand":     2.0,
        "cv":                     0.3,
        "p_zero":                 0.5,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _months(start: str, n: int) -> list[str]:
    """Generate n consecutive year_month_str values starting from start."""
    base = pd.Timestamp(start + "-01")
    return [(base + pd.DateOffset(months=i)).strftime("%Y-%m") for i in range(n)]


def _make_active_sku(
    sku: str, n_months: int, qty: float, xyz: str = "X"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (demand, classified) with n_months of consistent data for sku."""
    months = _months("2022-01", n_months)
    demand = _make_demand([
        {"material_9": sku, "year_month_str": m, "issue_qty": qty} for m in months
    ])
    classified = _make_classified([{
        "material_9": sku,
        "active_months": n_months,
        "xyz": xyz,
    }])
    return demand, classified


# ---------------------------------------------------------------------------
# build_panel
# ---------------------------------------------------------------------------

class TestBuildPanel:
    def test_all_months_included(self) -> None:
        """Panel should have one row per (SKU, month) for ALL months in demand."""
        months = _months("2025-01", 4)
        rows = [
            {"material_9": "A", "year_month_str": m, "issue_qty": float(i + 1)}
            for i, m in enumerate(months)
        ]
        demand = _make_demand(rows)
        panel = build_panel(demand, ["A"])
        assert len(panel) == 4

    def test_missing_months_filled_with_zero(self) -> None:
        """Months present for other SKUs but absent for this SKU should be zero-filled."""
        demand = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 10.0},
            {"material_9": "A", "year_month_str": "2025-03", "issue_qty": 5.0},
            {"material_9": "B", "year_month_str": "2025-02", "issue_qty": 8.0},
        ])
        panel = build_panel(demand, ["A"])
        # Month 2025-02 is in the full month set (from B) but absent for A → should be 0
        row = panel[panel["ds"] == pd.Timestamp("2025-02-01")]
        assert row["y"].iloc[0] == pytest.approx(0.0)

    def test_correct_y_values(self) -> None:
        demand = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 15.0},
        ])
        panel = build_panel(demand, ["A"])
        assert panel.loc[panel["ds"] == pd.Timestamp("2025-01-01"), "y"].iloc[0] == pytest.approx(15.0)

    def test_sorted_by_uid_and_ds(self) -> None:
        months = _months("2025-01", 3)
        demand = _make_demand([
            {"material_9": "A", "year_month_str": m, "issue_qty": 1.0} for m in months
        ] + [
            {"material_9": "B", "year_month_str": m, "issue_qty": 2.0} for m in months
        ])
        panel = build_panel(demand, ["A", "B"])
        assert list(panel["unique_id"].iloc[:3]) == ["A", "A", "A"]
        assert list(panel["ds"].iloc[:3]) == [pd.Timestamp(f"2025-0{i+1}-01") for i in range(3)]

    def test_uid_filter(self) -> None:
        """Only requested UIDs should appear in the panel."""
        demand = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 5.0},
            {"material_9": "B", "year_month_str": "2025-01", "issue_qty": 8.0},
        ])
        panel = build_panel(demand, ["A"])
        assert set(panel["unique_id"]) == {"A"}


# ---------------------------------------------------------------------------
# _zero_forecast
# ---------------------------------------------------------------------------

class TestZeroForecast:
    def test_all_forecasts_zero(self) -> None:
        result = _zero_forecast(["A", "B", "C"])
        assert (result[["forecast_m1", "forecast_m2", "forecast_m3", "forecast_lt"]] == 0.0).all().all()

    def test_method_label(self) -> None:
        result = _zero_forecast(["X"])
        assert result["method"].iloc[0] == "Zero"

    def test_one_row_per_uid(self) -> None:
        result = _zero_forecast(["A", "B", "C"])
        assert len(result) == 3
        assert set(result["unique_id"]) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# compute_demand_stats
# ---------------------------------------------------------------------------

class TestComputeDemandStats:
    def test_mean_monthly_correct(self) -> None:
        months = _months("2025-01", 4)
        demand = _make_demand([
            {"year_month_str": m, "issue_qty": 10.0} for m in months
        ])
        stats = compute_demand_stats(demand, 4)
        assert stats.loc[stats["material_9"] == "1GC-E4450-00", "demand_mean_monthly"].iloc[0] == pytest.approx(10.0)

    def test_std_zero_for_constant_demand(self) -> None:
        months = _months("2025-01", 3)
        demand = _make_demand([
            {"year_month_str": m, "issue_qty": 5.0} for m in months
        ])
        stats = compute_demand_stats(demand, 3)
        assert stats.loc[stats["material_9"] == "1GC-E4450-00", "demand_std_monthly"].iloc[0] == pytest.approx(0.0)

    def test_std_lt_is_sqrt3_times_std_monthly(self) -> None:
        months = _months("2025-01", 5)
        demand = _make_demand([
            {"year_month_str": months[i], "issue_qty": float(i * 3)} for i in range(5)
        ])
        stats = compute_demand_stats(demand, 5)
        row = stats.loc[stats["material_9"] == "1GC-E4450-00"].iloc[0]
        assert row["demand_std_lt"] == pytest.approx(row["demand_std_monthly"] * (3 ** 0.5))

    def test_single_observation_std_is_zero(self) -> None:
        demand = _make_demand([{"issue_qty": 10.0}])
        stats = compute_demand_stats(demand, 1)
        assert stats.loc[stats["material_9"] == "1GC-E4450-00", "demand_std_monthly"].iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_forecasts (integration — uses statsforecast internally)
# ---------------------------------------------------------------------------

class TestComputeForecasts:
    def test_non_mover_gets_zero_forecast(self) -> None:
        demand = _make_demand([{"material_9": "GHOST", "issue_qty": 0.0}])
        classified = _make_classified([{
            "material_9": "GHOST",
            "active_months": 0,
            "xyz": "Z",
        }])
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "GHOST"].iloc[0]
        assert row["forecast_lt"] == pytest.approx(0.0)
        assert row["method"] == "Zero"

    def test_sparse_sku_uses_historic_average(self) -> None:
        demand, classified = _make_active_sku("SPARSE", n_months=2, qty=10.0)
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "SPARSE"].iloc[0]
        assert row["method"] == "HistoricAverage"

    def test_z_class_uses_croston(self) -> None:
        demand, classified = _make_active_sku("LUMPY", n_months=5, qty=8.0, xyz="Z")
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "LUMPY"].iloc[0]
        assert row["method"] == "Croston"

    def test_x_class_uses_tier3_model(self) -> None:
        """X/Y-class SKUs should use a Tier 3 model (AutoETS or LightGBM)."""
        demand, classified = _make_active_sku("STEADY", n_months=5, qty=10.0, xyz="X")
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "STEADY"].iloc[0]
        assert row["method"] in {"AutoETS", "LightGBM"}

    def test_forecast_lt_equals_sum_of_monthly(self) -> None:
        demand, classified = _make_active_sku("SKU1", n_months=6, qty=5.0, xyz="X")
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "SKU1"].iloc[0]
        assert row["forecast_lt"] == pytest.approx(
            row["forecast_m1"] + row["forecast_m2"] + row["forecast_m3"]
        )

    def test_forecast_non_negative(self) -> None:
        demand, classified = _make_active_sku("POS", n_months=6, qty=3.0, xyz="Y")
        result = compute_forecasts(demand, classified)
        row = result[result["material_9"] == "POS"].iloc[0]
        assert row["forecast_m1"] >= 0.0
        assert row["forecast_lt"] >= 0.0

    def test_demand_std_lt_attached(self) -> None:
        demand, classified = _make_active_sku("SKU2", n_months=5, qty=5.0)
        result = compute_forecasts(demand, classified)
        assert "demand_std_lt" in result.columns
        assert "demand_std_monthly" in result.columns

    def test_one_row_per_sku(self) -> None:
        months = _months("2022-01", 5)
        demand = pd.concat([
            _make_demand([{"material_9": "A", "year_month_str": m, "issue_qty": 5.0} for m in months]),
            _make_demand([{"material_9": "B", "year_month_str": m, "issue_qty": 0.0} for m in months]),
        ])
        classified = pd.concat([
            _make_classified([{"material_9": "A", "active_months": 5, "xyz": "X"}]),
            _make_classified([{"material_9": "B", "active_months": 0, "xyz": "Z"}]),
        ]).reset_index(drop=True)
        result = compute_forecasts(demand, classified)
        assert len(result) == 2
        assert set(result["material_9"]) == {"A", "B"}


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def test_total_sku_count(self) -> None:
        demand, classified = _make_active_sku("SKU_T", n_months=5, qty=5.0)
        result = compute_forecasts(demand, classified)
        kpis = summary_kpis(result)
        assert kpis["Total SKUs"] == 1

    def test_zero_forecast_skus_counted(self) -> None:
        demand = _make_demand([{"material_9": "NM", "issue_qty": 0.0}])
        classified = _make_classified([{"material_9": "NM", "active_months": 0, "xyz": "Z"}])
        result = compute_forecasts(demand, classified)
        kpis = summary_kpis(result)
        assert kpis["Zero Forecast (Non-moving) SKUs"] == 1


# ---------------------------------------------------------------------------
# top_forecast_skus
# ---------------------------------------------------------------------------

class TestTopForecastSkus:
    def test_top_n_limit(self) -> None:
        rows = []
        for i in range(5):
            rows.append({
                "material_9": f"M{i}", "forecast_lt": float(i + 1),
                "method": "AutoETS", "forecast_m1": 1.0, "forecast_m2": 1.0,
                "forecast_m3": float(i - 1), "demand_std_lt": 0.5,
                "active_months": 5, "abc_xyz_fsn": "BXF", "policy_tier": "managed",
                "description": "TEST",
            })
        df = pd.DataFrame(rows)
        top = top_forecast_skus(df, top_n=3)
        assert len(top) == 3

    def test_sorted_by_forecast_lt_desc(self) -> None:
        df = pd.DataFrame([
            {"material_9": "LOW",  "forecast_lt": 1.0,  "method": "Zero",     "forecast_m1": 0.0, "forecast_m2": 0.0, "forecast_m3": 1.0, "demand_std_lt": 0.0, "active_months": 0, "abc_xyz_fsn": "CZN", "policy_tier": "rationalise", "description": ""},
            {"material_9": "HIGH", "forecast_lt": 100.0,"method": "AutoETS",  "forecast_m1": 33.0,"forecast_m2": 33.0,"forecast_m3": 34.0,"demand_std_lt": 5.0, "active_months": 10,"abc_xyz_fsn": "AXF", "policy_tier": "critical",    "description": ""},
        ])
        top = top_forecast_skus(df, top_n=10)
        assert top.iloc[0]["material_9"] == "HIGH"
