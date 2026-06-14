"""Unit tests for Stage 7 Stock Movement functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.eda.stage07_stock_movement import (
    _classify_movement,
    _to_9digit,
    build_monthly_demand,
    monthly_trend,
    slow_movers,
    summary_kpis,
    top_movers,
)


def _make_movements(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "material_12":    "1GC-E4450-00-00",
        "material_9":     "1GC-E4450-00",
        "description":    "AIR CLEANER ELEMENT",
        "movement_type":  601,
        "movement_class": "issue",
        "posting_date":   pd.Timestamp("2025-01-15"),
        "year_month_str": "2025-01",
        "qty":            -10.0,
        "value_lkr":      -5000.0,
        "customer":       "10001",
        "plant":          "W1B4",
        "compatible_models": "FZ; R15",
        "catalog_models":    "FZ & FZS",
    }
    records = [{**defaults, **r} for r in rows]
    df = pd.DataFrame(records)
    df["posting_date"] = pd.to_datetime(df["posting_date"])
    return df


# ---------------------------------------------------------------------------
# _to_9digit
# ---------------------------------------------------------------------------

class TestTo9Digit:
    def test_12digit_stripped(self) -> None:
        assert _to_9digit("1GC-E4450-00-00") == "1GC-E4450-00"

    def test_9digit_unchanged(self) -> None:
        assert _to_9digit("1GC-E4450-00") == "1GC-E4450-00"

    def test_non_standard_unchanged(self) -> None:
        assert _to_9digit("MISC") == "MISC"

    def test_empty_string_unchanged(self) -> None:
        assert _to_9digit("") == ""


# ---------------------------------------------------------------------------
# _classify_movement
# ---------------------------------------------------------------------------

class TestClassifyMovement:
    def test_issue_601(self) -> None:
        assert _classify_movement(601) == "issue"

    def test_receipt_101(self) -> None:
        assert _classify_movement(101) == "receipt"

    def test_return_653(self) -> None:
        assert _classify_movement(653) == "return"

    def test_scrap_551(self) -> None:
        assert _classify_movement(551) == "scrap"

    def test_unknown_returns_other(self) -> None:
        assert _classify_movement(999) == "other"


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def test_issue_lines_counted(self) -> None:
        df = _make_movements([
            {"movement_class": "issue"},
            {"movement_class": "issue"},
            {"movement_class": "receipt"},
        ])
        kpis = summary_kpis(df)
        assert kpis["Total Issue Lines (MT 601)"] == 2

    def test_total_issue_qty_absolute(self) -> None:
        df = _make_movements([
            {"movement_class": "issue", "qty": -15.0},
            {"movement_class": "issue", "qty": -5.0},
        ])
        kpis = summary_kpis(df)
        assert kpis["Total Issue Qty"] == pytest.approx(20.0)

    def test_return_rate_calculation(self) -> None:
        df = _make_movements([
            {"movement_class": "issue",   "qty": -100.0},
            {"movement_class": "return",  "qty":   10.0},
        ])
        kpis = summary_kpis(df)
        assert kpis["Return Rate %"] == pytest.approx(10.0)

    def test_unique_sku_count(self) -> None:
        df = _make_movements([
            {"material_9": "A"},
            {"material_9": "A"},
            {"material_9": "B"},
        ])
        kpis = summary_kpis(df)
        assert kpis["Unique SKUs"] == 2


# ---------------------------------------------------------------------------
# build_monthly_demand
# ---------------------------------------------------------------------------

class TestBuildMonthlyDemand:
    def test_issue_qty_aggregated(self) -> None:
        df = _make_movements([
            {"material_9": "A", "year_month_str": "2025-01",
             "movement_class": "issue", "qty": -10.0, "value_lkr": -5000.0},
            {"material_9": "A", "year_month_str": "2025-01",
             "movement_class": "issue", "qty": -5.0,  "value_lkr": -2500.0},
        ])
        result = build_monthly_demand(df)
        row = result[(result["material_9"] == "A") & (result["year_month_str"] == "2025-01")]
        assert row["issue_qty"].values[0] == pytest.approx(15.0)

    def test_net_demand_subtracts_returns(self) -> None:
        df = _make_movements([
            {"material_9": "A", "year_month_str": "2025-01",
             "movement_class": "issue",  "qty": -20.0, "value_lkr": -10000.0},
            {"material_9": "A", "year_month_str": "2025-01",
             "movement_class": "return", "qty":   3.0, "value_lkr":   1500.0},
        ])
        result = build_monthly_demand(df)
        row = result[result["material_9"] == "A"]
        assert row["net_demand"].values[0] == pytest.approx(17.0)

    def test_separate_months_separate_rows(self) -> None:
        df = _make_movements([
            {"year_month_str": "2025-01", "movement_class": "issue", "qty": -5.0,  "value_lkr": -2500.0},
            {"year_month_str": "2025-02", "movement_class": "issue", "qty": -8.0,  "value_lkr": -4000.0},
        ])
        result = build_monthly_demand(df)
        assert len(result[result["material_9"] == "1GC-E4450-00"]) == 2

    def test_zero_filled_for_missing_movement_class(self) -> None:
        """A month with only receipts should have zero issue_qty."""
        df = _make_movements([
            {"year_month_str": "2025-01", "movement_class": "receipt", "qty": 50.0, "value_lkr": 25000.0},
        ])
        result = build_monthly_demand(df)
        assert result["issue_qty"].iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# top_movers
# ---------------------------------------------------------------------------

class TestTopMovers:
    def test_sorted_by_value_desc(self) -> None:
        df = _make_movements([
            {"material_9": "HIGH", "movement_class": "issue", "qty": -1.0,  "value_lkr": -99000.0},
            {"material_9": "LOW",  "movement_class": "issue", "qty": -100.0,"value_lkr": -100.0},
        ])
        result = top_movers(df, top_n=10)
        assert result.iloc[0]["material_9"] == "HIGH"

    def test_top_n_limit(self) -> None:
        df = _make_movements([
            {"material_9": f"M{i}", "movement_class": "issue",
             "qty": -float(i), "value_lkr": -float(i * 1000)}
            for i in range(1, 11)
        ])
        result = top_movers(df, top_n=5)
        assert len(result) == 5

    def test_only_issues_included(self) -> None:
        df = _make_movements([
            {"material_9": "A", "movement_class": "receipt", "qty": 100.0, "value_lkr": 50000.0},
            {"material_9": "B", "movement_class": "issue",   "qty": -5.0,  "value_lkr": -2500.0},
        ])
        result = top_movers(df, top_n=10)
        assert "A" not in result["material_9"].values
        assert "B" in result["material_9"].values


# ---------------------------------------------------------------------------
# slow_movers
# ---------------------------------------------------------------------------

class TestSlowMovers:
    def test_recently_issued_not_slow(self) -> None:
        df = _make_movements([
            {"material_9": "ACTIVE", "movement_class": "issue",
             "posting_date": pd.Timestamp("2025-12-01"), "qty": -5.0, "value_lkr": -1000.0},
        ])
        result = slow_movers(df, min_months_silent=6)
        assert "ACTIVE" not in result["material_9"].values

    def test_old_issue_classified_slow(self) -> None:
        df = _make_movements([
            {"material_9": "SLOW",   "movement_class": "issue",
             "posting_date": pd.Timestamp("2023-01-01"), "qty": -5.0, "value_lkr": -1000.0},
            {"material_9": "ACTIVE", "movement_class": "issue",
             "posting_date": pd.Timestamp("2025-12-01"), "qty": -5.0, "value_lkr": -1000.0},
        ])
        result = slow_movers(df, min_months_silent=12)
        assert "SLOW" in result["material_9"].values
        assert "ACTIVE" not in result["material_9"].values

    def test_never_issued_is_slow(self) -> None:
        df = _make_movements([
            {"material_9": "GHOST", "movement_class": "receipt",
             "posting_date": pd.Timestamp("2025-01-01"), "qty": 10.0, "value_lkr": 5000.0},
        ])
        result = slow_movers(df, min_months_silent=6)
        assert "GHOST" in result["material_9"].values


# ---------------------------------------------------------------------------
# monthly_trend
# ---------------------------------------------------------------------------

class TestMonthlyTrend:
    def test_months_sorted_ascending(self) -> None:
        df = _make_movements([
            {"year_month_str": "2025-03", "movement_class": "issue", "qty": -5.0, "value_lkr": -1000.0},
            {"year_month_str": "2025-01", "movement_class": "issue", "qty": -5.0, "value_lkr": -1000.0},
        ])
        result = monthly_trend(df)
        assert result["Month"].iloc[0] == "2025-01"

    def test_issue_qty_aggregated(self) -> None:
        df = _make_movements([
            {"year_month_str": "2025-01", "movement_class": "issue", "qty": -10.0, "value_lkr": -5000.0},
            {"year_month_str": "2025-01", "movement_class": "issue", "qty": -20.0, "value_lkr": -10000.0},
        ])
        result = monthly_trend(df)
        jan = result[result["Month"] == "2025-01"]
        assert jan["issue_qty"].values[0] == pytest.approx(30.0)

    def test_return_rate_computed(self) -> None:
        df = _make_movements([
            {"year_month_str": "2025-01", "movement_class": "issue",  "qty": -100.0, "value_lkr": -50000.0},
            {"year_month_str": "2025-01", "movement_class": "return", "qty":   10.0, "value_lkr":   5000.0},
        ])
        result = monthly_trend(df)
        jan = result[result["Month"] == "2025-01"]
        assert jan["return_rate_%"].values[0] == pytest.approx(10.0)
