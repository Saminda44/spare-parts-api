"""Unit tests for Stage 11 stock tracker functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.inventory_policy.stage11_stock_tracker import (
    classify_stock_status,
    compute_coverage,
    compute_stock_position,
    excess_report,
    risk_report,
    summary_kpis,
    top_stockout_risk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_movements(rows: list[dict]) -> pd.DataFrame:
    defaults: dict = {
        "material_9":     "1GC-E4450-00",
        "movement_class": "issue",
        "posting_date":   pd.Timestamp("2025-01-15"),
        "qty":            -10.0,
        "value_lkr":      5000.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _make_classified(rows: list[dict]) -> pd.DataFrame:
    defaults: dict = {
        "material_9":            "1GC-E4450-00",
        "description":           "TEST PART",
        "abc":                   "B",
        "xyz":                   "X",
        "fsn":                   "F",
        "abc_xyz_fsn":           "BXF",
        "policy_tier":           "managed",
        "avg_monthly_demand":    5.0,
        "cv":                    0.3,
        "active_months":         6,
        "total_issue_value_lkr": 30000.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _make_forecast(rows: list[dict]) -> pd.DataFrame:
    defaults: dict = {
        "material_9":       "1GC-E4450-00",
        "forecast_m1":      5.0,
        "forecast_m2":      5.0,
        "forecast_m3":      5.0,
        "forecast_lt":      15.0,
        "demand_std_monthly": 1.0,
        "demand_std_lt":    1.73,
        "method":           "AutoETS",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _make_stock_row(
    sku: str = "SKU1",
    stock_on_hand: float = 10.0,
    avg_monthly_demand: float = 2.0,
    forecast_lt: float = 6.0,
    abc: str = "B",
    active_months: int = 6,
) -> dict:
    return {
        "material_9":         sku,
        "stock_on_hand":      stock_on_hand,
        "stock_value_lkr":    stock_on_hand * 100.0,
        "last_movement_date": pd.Timestamp("2025-03-01"),
        "total_receipts":     50.0,
        "total_issues":       40.0,
        "total_returns":      0.0,
        "avg_monthly_demand": avg_monthly_demand,
        "forecast_lt":        forecast_lt,
        "forecast_m1":        2.0,
        "forecast_m2":        2.0,
        "forecast_m3":        2.0,
        "demand_std_monthly": 0.5,
        "demand_std_lt":      0.87,
        "method":             "AutoETS",
        "description":        "TEST",
        "abc":                abc,
        "xyz":                "X",
        "fsn":                "F",
        "abc_xyz_fsn":        f"{abc}XF",
        "policy_tier":        "managed",
        "cv":                 0.3,
        "active_months":      active_months,
        "total_issue_value_lkr": 20000.0,
        "coverage_months":    stock_on_hand / max(avg_monthly_demand, 1e-9),
        "days_of_stock":      int(stock_on_hand / max(avg_monthly_demand, 1e-9) * 30),
    }


# ---------------------------------------------------------------------------
# compute_stock_position
# ---------------------------------------------------------------------------

class TestComputeStockPosition:
    def test_net_receipt_minus_issue(self) -> None:
        """receipt qty (+50) minus issue qty (-30) should give stock_on_hand = 20."""
        mv = _make_movements([
            {"movement_class": "receipt", "qty": 50.0},
            {"movement_class": "issue",   "qty": -30.0},
        ])
        result = compute_stock_position(mv)
        assert result.loc[result["material_9"] == "1GC-E4450-00", "stock_on_hand"].iloc[0] == pytest.approx(20.0)

    def test_transfers_excluded(self) -> None:
        """Transfer movements should not affect the stock position."""
        mv = _make_movements([
            {"movement_class": "receipt",  "qty": 100.0},
            {"movement_class": "transfer", "qty": -50.0},  # internal — excluded
        ])
        result = compute_stock_position(mv)
        # Only the receipt counts
        assert result.loc[result["material_9"] == "1GC-E4450-00", "stock_on_hand"].iloc[0] == pytest.approx(100.0)

    def test_negative_stock_clipped_to_zero(self) -> None:
        """Net negative stock (opening balance missing) must be clipped to 0."""
        mv = _make_movements([
            {"movement_class": "issue", "qty": -100.0},
        ])
        result = compute_stock_position(mv)
        assert result.loc[result["material_9"] == "1GC-E4450-00", "stock_on_hand"].iloc[0] == pytest.approx(0.0)

    def test_customer_returns_add_to_stock(self) -> None:
        """Customer returns (movement_class='return') increase stock on hand."""
        mv = _make_movements([
            {"movement_class": "issue",  "qty": -10.0},
            {"movement_class": "return", "qty": 3.0},
        ])
        # Net = -10 + 3 = -7 → clipped to 0 (we only have outflow data here)
        # With a receipt to ensure positive balance:
        mv2 = _make_movements([
            {"movement_class": "receipt", "qty": 20.0},
            {"movement_class": "issue",   "qty": -10.0},
            {"movement_class": "return",  "qty": 3.0},
        ])
        result = compute_stock_position(mv2)
        assert result.loc[result["material_9"] == "1GC-E4450-00", "stock_on_hand"].iloc[0] == pytest.approx(13.0)

    def test_one_row_per_sku(self) -> None:
        """Each SKU should produce exactly one output row."""
        mv = _make_movements([
            {"material_9": "A", "movement_class": "receipt", "qty": 10.0},
            {"material_9": "A", "movement_class": "issue",   "qty": -3.0},
            {"material_9": "B", "movement_class": "receipt", "qty": 5.0},
        ])
        result = compute_stock_position(mv)
        assert len(result) == 2
        assert set(result["material_9"]) == {"A", "B"}

    def test_total_receipts_and_issues_tracked(self) -> None:
        """total_receipts and total_issues must be absolute sums."""
        mv = _make_movements([
            {"movement_class": "receipt", "qty": 50.0},
            {"movement_class": "receipt", "qty": 20.0},
            {"movement_class": "issue",   "qty": -15.0},
        ])
        result = compute_stock_position(mv)
        assert result.loc[result["material_9"] == "1GC-E4450-00", "total_receipts"].iloc[0] == pytest.approx(70.0)
        assert result.loc[result["material_9"] == "1GC-E4450-00", "total_issues"].iloc[0] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# compute_coverage
# ---------------------------------------------------------------------------

class TestComputeCoverage:
    def _base_stock(self, stock: float, demand: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "material_9":         "SKU1",
            "stock_on_hand":      stock,
            "stock_value_lkr":    stock * 100.0,
            "last_movement_date": pd.Timestamp("2025-03-01"),
            "total_receipts":     100.0,
            "total_issues":       80.0,
            "total_returns":      0.0,
        }])

    def test_coverage_months_correct(self) -> None:
        stock = self._base_stock(15.0, 5.0)
        classified = _make_classified([{"material_9": "SKU1", "avg_monthly_demand": 5.0}])
        forecast   = _make_forecast([{"material_9": "SKU1", "forecast_lt": 15.0}])
        result = compute_coverage(stock, classified, forecast)
        assert result.loc[result["material_9"] == "SKU1", "coverage_months"].iloc[0] == pytest.approx(3.0)

    def test_zero_demand_with_stock_gives_999(self) -> None:
        stock = self._base_stock(10.0, 0.0)
        classified = _make_classified([{"material_9": "SKU1", "avg_monthly_demand": 0.0}])
        forecast   = _make_forecast([{"material_9": "SKU1", "forecast_lt": 0.0}])
        result = compute_coverage(stock, classified, forecast)
        assert result.loc[result["material_9"] == "SKU1", "coverage_months"].iloc[0] == pytest.approx(999.0)

    def test_zero_demand_zero_stock_gives_zero_coverage(self) -> None:
        stock = self._base_stock(0.0, 0.0)
        classified = _make_classified([{"material_9": "SKU1", "avg_monthly_demand": 0.0}])
        forecast   = _make_forecast([{"material_9": "SKU1", "forecast_lt": 0.0}])
        result = compute_coverage(stock, classified, forecast)
        assert result.loc[result["material_9"] == "SKU1", "coverage_months"].iloc[0] == pytest.approx(0.0)

    def test_forecast_columns_attached(self) -> None:
        stock = self._base_stock(10.0, 5.0)
        classified = _make_classified([{"material_9": "SKU1"}])
        forecast   = _make_forecast([{"material_9": "SKU1", "forecast_lt": 12.0}])
        result = compute_coverage(stock, classified, forecast)
        assert "forecast_lt" in result.columns
        assert "demand_std_lt" in result.columns


# ---------------------------------------------------------------------------
# classify_stock_status
# ---------------------------------------------------------------------------

class TestClassifyStockStatus:
    def _df(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_stockout_when_zero_stock(self) -> None:
        df = self._df([{"stock_on_hand": 0.0, "coverage_months": 0.0, "avg_monthly_demand": 5.0}])
        result = classify_stock_status(df)
        assert result["stock_status"].iloc[0] == "stockout"

    def test_critical_below_one_month(self) -> None:
        df = self._df([{"stock_on_hand": 2.0, "coverage_months": 0.4, "avg_monthly_demand": 5.0}])
        result = classify_stock_status(df)
        assert result["stock_status"].iloc[0] == "critical"

    def test_low_below_lead_time(self) -> None:
        df = self._df([{"stock_on_hand": 8.0, "coverage_months": 1.6, "avg_monthly_demand": 5.0}])
        result = classify_stock_status(df)
        assert result["stock_status"].iloc[0] == "low"

    def test_ok_within_range(self) -> None:
        df = self._df([{"stock_on_hand": 20.0, "coverage_months": 4.0, "avg_monthly_demand": 5.0}])
        result = classify_stock_status(df)
        assert result["stock_status"].iloc[0] == "ok"

    def test_excess_above_six_months(self) -> None:
        df = self._df([{"stock_on_hand": 50.0, "coverage_months": 10.0, "avg_monthly_demand": 5.0}])
        result = classify_stock_status(df)
        assert result["stock_status"].iloc[0] == "excess"

    def test_all_statuses_possible(self) -> None:
        df = self._df([
            {"stock_on_hand": 0.0,  "coverage_months": 0.0,  "avg_monthly_demand": 5.0},
            {"stock_on_hand": 2.0,  "coverage_months": 0.4,  "avg_monthly_demand": 5.0},
            {"stock_on_hand": 8.0,  "coverage_months": 1.6,  "avg_monthly_demand": 5.0},
            {"stock_on_hand": 20.0, "coverage_months": 4.0,  "avg_monthly_demand": 5.0},
            {"stock_on_hand": 50.0, "coverage_months": 10.0, "avg_monthly_demand": 5.0},
        ])
        result = classify_stock_status(df)
        assert set(result["stock_status"]) == {"stockout", "critical", "low", "ok", "excess"}


# ---------------------------------------------------------------------------
# risk_report
# ---------------------------------------------------------------------------

class TestRiskReport:
    def test_filters_at_risk_only(self) -> None:
        rows = [
            _make_stock_row("LOW",    stock_on_hand=5.0,  avg_monthly_demand=5.0),   # low
            _make_stock_row("OK",     stock_on_hand=20.0, avg_monthly_demand=5.0),   # ok
            _make_stock_row("EXCESS", stock_on_hand=60.0, avg_monthly_demand=5.0),   # excess
        ]
        rows[0]["stock_status"] = "low"
        rows[1]["stock_status"] = "ok"
        rows[2]["stock_status"] = "excess"
        df = pd.DataFrame(rows)
        result = risk_report(df)
        assert set(result["material_9"]) == {"LOW"}

    def test_excludes_zero_demand_skus(self) -> None:
        row = _make_stock_row("GHOST", stock_on_hand=0.0, avg_monthly_demand=0.0)
        row["stock_status"] = "stockout"
        df = pd.DataFrame([row])
        result = risk_report(df)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# excess_report
# ---------------------------------------------------------------------------

class TestExcessReport:
    def test_returns_only_excess_with_stock(self) -> None:
        rows = [
            _make_stock_row("EX1", stock_on_hand=60.0, avg_monthly_demand=5.0),
            _make_stock_row("EX2", stock_on_hand=0.0,  avg_monthly_demand=5.0),
            _make_stock_row("OK",  stock_on_hand=15.0, avg_monthly_demand=5.0),
        ]
        rows[0]["stock_status"] = "excess"
        rows[1]["stock_status"] = "excess"   # no stock — should be excluded
        rows[2]["stock_status"] = "ok"
        df = pd.DataFrame(rows)
        result = excess_report(df)
        assert set(result["material_9"]) == {"EX1"}

    def test_sorted_by_coverage_desc(self) -> None:
        rows = [
            _make_stock_row("EX1", stock_on_hand=70.0, avg_monthly_demand=5.0),
            _make_stock_row("EX2", stock_on_hand=50.0, avg_monthly_demand=5.0),
        ]
        for r in rows:
            r["stock_status"] = "excess"
        df = pd.DataFrame(rows)
        result = excess_report(df)
        assert result.iloc[0]["material_9"] == "EX1"


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def test_required_keys_present(self) -> None:
        row = _make_stock_row("SKU1")
        row["stock_status"] = "ok"
        df = pd.DataFrame([row])
        kpis = summary_kpis(df)
        for key in (
            "Total SKUs",
            "SKUs with Stock",
            "Stockout SKUs",
            "Total Stock Value (LKR)",
            "At-risk SKUs (< lead time)",
        ):
            assert key in kpis, f"Missing KPI: {key}"

    def test_total_sku_count(self) -> None:
        rows = [_make_stock_row(f"S{i}") for i in range(5)]
        for r in rows:
            r["stock_status"] = "ok"
        df = pd.DataFrame(rows)
        assert summary_kpis(df)["Total SKUs"] == 5

    def test_stockout_count(self) -> None:
        rows = [
            _make_stock_row("OUT1", stock_on_hand=0.0),
            _make_stock_row("OUT2", stock_on_hand=0.0),
            _make_stock_row("OK1",  stock_on_hand=10.0),
        ]
        rows[0]["stock_status"] = "stockout"
        rows[1]["stock_status"] = "stockout"
        rows[2]["stock_status"] = "ok"
        df = pd.DataFrame(rows)
        assert summary_kpis(df)["Stockout SKUs"] == 2


# ---------------------------------------------------------------------------
# top_stockout_risk
# ---------------------------------------------------------------------------

class TestTopStockoutRisk:
    def test_top_n_limit(self) -> None:
        rows = []
        for i in range(10):
            r = _make_stock_row(f"S{i}", stock_on_hand=float(i), avg_monthly_demand=5.0)
            r["stock_status"] = "low"
            rows.append(r)
        df = pd.DataFrame(rows)
        result = top_stockout_risk(df, top_n=3)
        assert len(result) == 3

    def test_excludes_non_at_risk(self) -> None:
        rows = [
            _make_stock_row("OK",  stock_on_hand=20.0, avg_monthly_demand=5.0),
            _make_stock_row("LOW", stock_on_hand=5.0,  avg_monthly_demand=5.0),
        ]
        rows[0]["stock_status"] = "ok"
        rows[1]["stock_status"] = "low"
        df = pd.DataFrame(rows)
        result = top_stockout_risk(df, top_n=10)
        assert set(result["material_9"]) == {"LOW"}
