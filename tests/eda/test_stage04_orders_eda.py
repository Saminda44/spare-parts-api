"""Unit tests for Stage 4 Orders EDA functions."""

import numpy as np
import pandas as pd
import pytest

from src.eda.stage04_orders_eda import (
    fill_rate_by_dealer,
    monthly_trend,
    returns_analysis,
    short_ship_by_material,
    summary_kpis,
    top_materials_by_value,
)


def _make_orders(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "Sales Document":          "40000001",
        "doc_type":                "PO",
        "Sold-to Party":           "D001",
        "Sold-To Party Name":      "Test Dealer",
        "Material":                "MAT001",
        "Material Description":    "Test Part",
        "Order Quantity (Item)":   10,
        "Confirmed Quantity (Item)": 10,
        "Net Value (Item)":        1000.0,
        "lead_time_days":          1,
        "lost_qty":                0,
        "fill_rate":               1.0,
        "Year_Month_str":          "2024-01",
        "Order Reason Description": "Parts Dealer Sales",
        "Reason for Rejection":    None,
    }
    records = [{**defaults, **r} for r in rows]
    return pd.DataFrame(records)


class TestSummaryKpis:
    def test_po_and_return_counts(self) -> None:
        df = _make_orders([
            {"doc_type": "PO"},
            {"doc_type": "PO"},
            {"doc_type": "Return"},
        ])
        rejected = _make_orders([{"Confirmed Quantity (Item)": 0}])
        kpis = summary_kpis(df, rejected)
        assert kpis["PO Lines"] == 2
        assert kpis["Return Lines"] == 1
        assert kpis["Fully Rejected Lines"] == 1

    def test_fill_rate_perfect(self) -> None:
        df = _make_orders([{"Order Quantity (Item)": 100, "Confirmed Quantity (Item)": 100}])
        kpis = summary_kpis(df, pd.DataFrame())
        assert kpis["Overall Fill Rate %"] == pytest.approx(100.0)

    def test_fill_rate_partial(self) -> None:
        df = _make_orders([{"Order Quantity (Item)": 100, "Confirmed Quantity (Item)": 75}])
        kpis = summary_kpis(df, pd.DataFrame())
        assert kpis["Overall Fill Rate %"] == pytest.approx(75.0)


class TestMonthlyTrend:
    def test_po_lines_counted_per_month(self) -> None:
        df = _make_orders([
            {"Year_Month_str": "2024-01"},
            {"Year_Month_str": "2024-01"},
            {"Year_Month_str": "2024-02"},
        ])
        result = monthly_trend(df)
        jan = result[result["Month"] == "2024-01"]
        assert jan["po_lines"].values[0] == 2

    def test_returns_not_in_po_lines(self) -> None:
        df = _make_orders([
            {"doc_type": "PO",     "Year_Month_str": "2024-01"},
            {"doc_type": "Return", "Year_Month_str": "2024-01"},
        ])
        result = monthly_trend(df)
        jan = result[result["Month"] == "2024-01"]
        assert jan["po_lines"].values[0] == 1

    def test_sorted_ascending(self) -> None:
        df = _make_orders([
            {"Year_Month_str": "2024-03"},
            {"Year_Month_str": "2024-01"},
        ])
        result = monthly_trend(df)
        assert list(result["Month"]) == ["2024-01", "2024-03"]


class TestShortShipByMaterial:
    def test_only_short_shipped_included(self) -> None:
        df = _make_orders([
            {"Material": "A", "lost_qty": -5,  "Order Quantity (Item)": 10, "Confirmed Quantity (Item)": 5},
            {"Material": "B", "lost_qty":  0,  "Order Quantity (Item)": 10, "Confirmed Quantity (Item)": 10},
        ])
        result = short_ship_by_material(df)
        assert "A" in result["Material"].values
        assert "B" not in result["Material"].values

    def test_returns_excluded(self) -> None:
        df = _make_orders([
            {"doc_type": "Return", "Material": "A", "lost_qty": -5,
             "Order Quantity (Item)": 10, "Confirmed Quantity (Item)": 5},
        ])
        result = short_ship_by_material(df)
        assert len(result) == 0


class TestFillRateByDealer:
    def test_fill_rate_100_when_fully_filled(self) -> None:
        df = _make_orders([
            {"Sold-to Party": "D001", "Order Quantity (Item)": 10, "Confirmed Quantity (Item)": 10},
        ])
        result = fill_rate_by_dealer(df, pd.DataFrame())
        assert result.loc[result["Dealer Code"] == "D001", "fill_rate_%"].values[0] == pytest.approx(100.0)

    def test_sorted_by_value_desc(self) -> None:
        df = _make_orders([
            {"Sold-to Party": "D001", "Net Value (Item)": 500},
            {"Sold-to Party": "D002", "Net Value (Item)": 2000},
        ])
        result = fill_rate_by_dealer(df, pd.DataFrame())
        assert result.iloc[0]["Dealer Code"] == "D002"


class TestReturnsAnalysis:
    def test_only_return_docs(self) -> None:
        df = _make_orders([
            {"doc_type": "Return", "Order Reason Description": "Damage"},
            {"doc_type": "PO"},
        ])
        result = returns_analysis(df)
        assert len(result) == 1

    def test_sorted_by_value_desc(self) -> None:
        df = _make_orders([
            {"doc_type": "Return", "Net Value (Item)": 100, "Order Reason Description": "X"},
            {"doc_type": "Return", "Net Value (Item)": 500, "Order Reason Description": "Y"},
        ])
        result = returns_analysis(df)
        assert result.iloc[0]["return_value"] == 500


class TestTopMaterials:
    def test_correct_count(self) -> None:
        df = _make_orders([{"Material": f"M{i:03d}", "Material Description": f"Part {i}"} for i in range(10)])
        result = top_materials_by_value(df, top_n=5)
        assert len(result) == 5

    def test_value_share_sums_to_100_within_top_n(self) -> None:
        df = _make_orders([{"Material": "A", "Net Value (Item)": 600},
                           {"Material": "B", "Net Value (Item)": 400}])
        result = top_materials_by_value(df, top_n=10)
        assert result["value_share_%"].sum() == pytest.approx(100.0, abs=0.1)
