"""Unit tests for Stage 8 Spare Parts EDA functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.eda.stage08_spare_parts_eda import (
    _categorise_demand,
    _compute_cv,
    _last_issue_date,
    compute_sku_features,
    demand_pattern_distribution,
    intermittent_skus,
    summary_kpis,
    top_skus_by_value,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_demand(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal monthly_demand DataFrame from partial row dicts."""
    defaults: dict = {
        "material_9":       "1GC-E4450-00",
        "description":      "AIR CLEANER ELEMENT",
        "year_month_str":   "2025-01",
        "issue_qty":        10.0,
        "issue_value_lkr":  5000.0,
        "return_qty":       0.0,
        "return_value_lkr": 0.0,
        "receipt_qty":      0.0,
        "net_demand":       10.0,
    }
    records = [{**defaults, **r} for r in rows]
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# _compute_cv
# ---------------------------------------------------------------------------

class TestComputeCV:
    def test_single_nonzero_returns_zero(self) -> None:
        df = _make_demand([{"issue_qty": 10.0}])
        cv = _compute_cv(df)
        assert cv["1GC-E4450-00"] == pytest.approx(0.0)

    def test_constant_demand_zero_cv(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 10.0},
            {"year_month_str": "2025-02", "issue_qty": 10.0},
        ])
        cv = _compute_cv(df)
        assert cv["1GC-E4450-00"] == pytest.approx(0.0)

    def test_variable_demand_positive_cv(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 2.0},
            {"year_month_str": "2025-02", "issue_qty": 8.0},
        ])
        cv = _compute_cv(df)
        # std([2,8]) / mean([2,8]) = 4.243/5 ≈ 0.849
        assert cv["1GC-E4450-00"] > 0.0

    def test_zero_months_excluded_from_cv(self) -> None:
        """CV should be computed on non-zero months only."""
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 0.0},
            {"year_month_str": "2025-02", "issue_qty": 10.0},
            {"year_month_str": "2025-03", "issue_qty": 10.0},
        ])
        cv = _compute_cv(df)
        # Only two non-zero months with equal values → CV = 0
        assert cv["1GC-E4450-00"] == pytest.approx(0.0)

    def test_multiple_skus_computed_independently(self) -> None:
        df = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 5.0},
            {"material_9": "A", "year_month_str": "2025-02", "issue_qty": 15.0},
            {"material_9": "B", "year_month_str": "2025-01", "issue_qty": 10.0},
            {"material_9": "B", "year_month_str": "2025-02", "issue_qty": 10.0},
        ])
        cv = _compute_cv(df)
        assert cv["A"] > 0.0
        assert cv["B"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _last_issue_date
# ---------------------------------------------------------------------------

class TestLastIssueDate:
    def test_returns_most_recent_month(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 5.0},
            {"year_month_str": "2025-06", "issue_qty": 3.0},
        ])
        last = _last_issue_date(df)
        assert last["1GC-E4450-00"] == pd.Timestamp("2025-06-01")

    def test_zero_months_not_counted(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 5.0},
            {"year_month_str": "2025-12", "issue_qty": 0.0},
        ])
        last = _last_issue_date(df)
        assert last["1GC-E4450-00"] == pd.Timestamp("2025-01-01")

    def test_never_issued_returns_empty(self) -> None:
        df = _make_demand([{"issue_qty": 0.0}])
        last = _last_issue_date(df)
        assert "1GC-E4450-00" not in last


# ---------------------------------------------------------------------------
# _categorise_demand
# ---------------------------------------------------------------------------

class TestCategoriseDemand:
    def _row(self, active: int, p_zero: float, cv: float) -> pd.Series:
        return pd.Series({"active_months": active, "p_zero": p_zero, "cv": cv})

    def test_non_moving(self) -> None:
        assert _categorise_demand(self._row(0, 1.0, 0.0)) == "non_moving"

    def test_p_zero_equals_one_is_non_moving(self) -> None:
        assert _categorise_demand(self._row(1, 1.0, 0.0)) == "non_moving"

    def test_smooth(self) -> None:
        # Low CV, low p_zero
        assert _categorise_demand(self._row(10, 0.3, 0.2)) == "smooth"

    def test_erratic(self) -> None:
        # High CV, low p_zero
        assert _categorise_demand(self._row(10, 0.3, 0.8)) == "erratic"

    def test_intermittent(self) -> None:
        # Low CV, high p_zero
        assert _categorise_demand(self._row(3, 0.8, 0.2)) == "intermittent"

    def test_lumpy(self) -> None:
        # High CV AND high p_zero
        assert _categorise_demand(self._row(3, 0.8, 0.9)) == "lumpy"


# ---------------------------------------------------------------------------
# compute_sku_features
# ---------------------------------------------------------------------------

class TestComputeSkuFeatures:
    def test_returns_one_row_per_sku(self) -> None:
        df = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 5.0, "issue_value_lkr": 1000.0},
            {"material_9": "A", "year_month_str": "2025-02", "issue_qty": 3.0, "issue_value_lkr": 600.0},
            {"material_9": "B", "year_month_str": "2025-01", "issue_qty": 0.0, "issue_value_lkr": 0.0},
        ])
        result = compute_sku_features(df)
        assert set(result["material_9"]) == {"A", "B"}
        assert len(result) == 2

    def test_total_issue_qty_summed(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 10.0, "issue_value_lkr": 5000.0},
            {"year_month_str": "2025-02", "issue_qty":  5.0, "issue_value_lkr": 2500.0},
        ])
        result = compute_sku_features(df)
        assert result.iloc[0]["total_issue_qty"] == pytest.approx(15.0)

    def test_active_months_counts_nonzero(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 10.0, "issue_value_lkr": 5000.0},
            {"year_month_str": "2025-02", "issue_qty":  0.0, "issue_value_lkr": 0.0},
            {"year_month_str": "2025-03", "issue_qty":  5.0, "issue_value_lkr": 2500.0},
        ])
        result = compute_sku_features(df)
        assert result.iloc[0]["active_months"] == 2

    def test_p_zero_fraction_correct(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 10.0, "issue_value_lkr": 5000.0},
            {"year_month_str": "2025-02", "issue_qty":  0.0, "issue_value_lkr": 0.0},
            {"year_month_str": "2025-03", "issue_qty":  0.0, "issue_value_lkr": 0.0},
            {"year_month_str": "2025-04", "issue_qty":  0.0, "issue_value_lkr": 0.0},
        ])
        result = compute_sku_features(df)
        # 3 zero months out of 4 total = 0.75
        assert result.iloc[0]["p_zero"] == pytest.approx(0.75)

    def test_avg_monthly_demand_over_total_months(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 20.0, "issue_value_lkr": 10000.0},
            {"year_month_str": "2025-02", "issue_qty":  0.0, "issue_value_lkr": 0.0},
        ])
        result = compute_sku_features(df)
        # 20 units over 2 months → 10 avg/month
        assert result.iloc[0]["avg_monthly_demand"] == pytest.approx(10.0)

    def test_last_issue_date_populated(self) -> None:
        df = _make_demand([
            {"year_month_str": "2024-06", "issue_qty": 1.0, "issue_value_lkr": 100.0},
            {"year_month_str": "2025-01", "issue_qty": 5.0, "issue_value_lkr": 500.0},
        ])
        result = compute_sku_features(df)
        assert result.iloc[0]["last_issue_date"] == pd.Timestamp("2025-01-01")

    def test_demand_category_assigned(self) -> None:
        df = _make_demand([
            {"year_month_str": "2025-01", "issue_qty": 10.0, "issue_value_lkr": 5000.0},
        ])
        result = compute_sku_features(df)
        assert result.iloc[0]["demand_category"] in {
            "smooth", "erratic", "intermittent", "lumpy", "non_moving"
        }

    def test_never_issued_sku_is_non_moving(self) -> None:
        df = _make_demand([
            {"material_9": "GHOST", "issue_qty": 0.0, "issue_value_lkr": 0.0,
             "return_qty": 0.0, "net_demand": 0.0},
        ])
        result = compute_sku_features(df)
        row = result[result["material_9"] == "GHOST"].iloc[0]
        assert row["demand_category"] == "non_moving"
        assert row["active_months"] == 0

    def test_sorted_by_value_desc(self) -> None:
        df = _make_demand([
            {"material_9": "LOW",  "issue_qty": 1.0, "issue_value_lkr": 100.0,
             "return_qty": 0.0, "net_demand": 1.0},
            {"material_9": "HIGH", "issue_qty": 100.0, "issue_value_lkr": 50000.0,
             "return_qty": 0.0, "net_demand": 100.0},
        ])
        result = compute_sku_features(df)
        assert result.iloc[0]["material_9"] == "HIGH"


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def test_active_sku_count(self) -> None:
        df = _make_demand([
            {"material_9": "A", "issue_qty": 5.0, "issue_value_lkr": 1000.0,
             "return_qty": 0.0, "net_demand": 5.0},
            {"material_9": "B", "issue_qty": 0.0, "issue_value_lkr": 0.0,
             "return_qty": 0.0, "net_demand": 0.0},
        ])
        features = compute_sku_features(df)
        kpis = summary_kpis(features)
        assert kpis["SKUs with ≥1 Issue"] == 1

    def test_non_moving_count(self) -> None:
        df = _make_demand([
            {"material_9": "A", "issue_qty": 0.0, "issue_value_lkr": 0.0,
             "return_qty": 0.0, "net_demand": 0.0},
        ])
        features = compute_sku_features(df)
        kpis = summary_kpis(features)
        assert kpis["Non-moving SKUs (p_zero=1)"] == 1

    def test_total_value_aggregated(self) -> None:
        df = _make_demand([
            {"material_9": "A", "year_month_str": "2025-01", "issue_qty": 5.0,
             "issue_value_lkr": 2500.0, "return_qty": 0.0, "net_demand": 5.0},
            {"material_9": "B", "year_month_str": "2025-01", "issue_qty": 3.0,
             "issue_value_lkr": 900.0, "return_qty": 0.0, "net_demand": 3.0},
        ])
        features = compute_sku_features(df)
        kpis = summary_kpis(features)
        assert kpis["Total Issue Value LKR"] == pytest.approx(3400.0)


# ---------------------------------------------------------------------------
# demand_pattern_distribution
# ---------------------------------------------------------------------------

class TestDemandPatternDistribution:
    def test_all_categories_present(self) -> None:
        df = _make_demand([
            {"material_9": "A", "issue_qty": 5.0, "issue_value_lkr": 1000.0,
             "return_qty": 0.0, "net_demand": 5.0},
        ])
        features = compute_sku_features(df)
        dist = demand_pattern_distribution(features)
        assert "demand_category" in dist.columns
        assert "sku_count" in dist.columns
        assert "share_%" in dist.columns

    def test_shares_sum_to_100(self) -> None:
        df = _make_demand([
            {"material_9": "A", "issue_qty": 5.0, "issue_value_lkr": 1000.0,
             "return_qty": 0.0, "net_demand": 5.0},
            {"material_9": "B", "issue_qty": 0.0, "issue_value_lkr": 0.0,
             "return_qty": 0.0, "net_demand": 0.0},
        ])
        features = compute_sku_features(df)
        dist = demand_pattern_distribution(features)
        assert dist["share_%"].sum() == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# top_skus_by_value
# ---------------------------------------------------------------------------

class TestTopSkusByValue:
    def test_top_n_limit(self) -> None:
        rows = [
            {"material_9": f"M{i}", "issue_qty": float(i), "issue_value_lkr": float(i * 1000),
             "return_qty": 0.0, "net_demand": float(i)}
            for i in range(1, 11)
        ]
        df = _make_demand(rows)
        features = compute_sku_features(df)
        top = top_skus_by_value(features, top_n=3)
        assert len(top) == 3

    def test_sorted_by_value_desc(self) -> None:
        df = _make_demand([
            {"material_9": "LOW",  "issue_qty": 1.0, "issue_value_lkr": 100.0,
             "return_qty": 0.0, "net_demand": 1.0},
            {"material_9": "HIGH", "issue_qty": 50.0, "issue_value_lkr": 50000.0,
             "return_qty": 0.0, "net_demand": 50.0},
        ])
        features = compute_sku_features(df)
        top = top_skus_by_value(features, top_n=10)
        assert top.iloc[0]["material_9"] == "HIGH"

    def test_cumulative_share_increases(self) -> None:
        rows = [
            {"material_9": f"M{i}", "issue_qty": float(i), "issue_value_lkr": float(i * 1000),
             "return_qty": 0.0, "net_demand": float(i)}
            for i in range(1, 6)
        ]
        df = _make_demand(rows)
        features = compute_sku_features(df)
        top = top_skus_by_value(features, top_n=5)
        shares = top["cumulative_value_share_%"].tolist()
        assert shares == sorted(shares)
        assert shares[-1] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# intermittent_skus
# ---------------------------------------------------------------------------

class TestIntermittentSkus:
    def test_never_issued_excluded(self) -> None:
        """Non-moving (p_zero=1) SKUs should not appear — function targets active intermittent."""
        df = _make_demand([
            {"material_9": "GHOST", "issue_qty": 0.0, "issue_value_lkr": 0.0,
             "return_qty": 0.0, "net_demand": 0.0},
        ])
        features = compute_sku_features(df)
        result = intermittent_skus(features)
        assert "GHOST" not in result["material_9"].values

    def test_high_p_zero_active_sku_included(self) -> None:
        """An SKU active in only 1 of 5 months (p_zero=0.8) should appear."""
        df = _make_demand([
            {"material_9": "RARE", "year_month_str": f"2025-0{i}",
             "issue_qty": 5.0 if i == 1 else 0.0,
             "issue_value_lkr": 1000.0 if i == 1 else 0.0,
             "return_qty": 0.0, "net_demand": 5.0 if i == 1 else 0.0}
            for i in range(1, 6)
        ])
        features = compute_sku_features(df)
        result = intermittent_skus(features)
        assert "RARE" in result["material_9"].values

    def test_smooth_sku_excluded(self) -> None:
        """A SKU active every month (p_zero=0) should not appear."""
        df = _make_demand([
            {"material_9": "STEADY", "year_month_str": f"2025-0{i}",
             "issue_qty": 10.0, "issue_value_lkr": 5000.0,
             "return_qty": 0.0, "net_demand": 10.0}
            for i in range(1, 6)
        ])
        features = compute_sku_features(df)
        result = intermittent_skus(features)
        assert "STEADY" not in result["material_9"].values
