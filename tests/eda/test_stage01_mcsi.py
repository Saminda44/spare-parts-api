"""Unit tests for Stage 1 MCSI EDA functions."""

import pandas as pd
import pytest

from src.eda.stage01_msci import (
    classify_vin_status,
    enrich_with_status,
    hierarchy_summary,
    model_mix,
    monthly_sales_trend,
    returns_detail,
    summary_kpis,
)


def _make_mcsi(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal MCSI DataFrame for testing."""
    defaults = {
        "VIN": "V001",
        "SlsVolQty": 1,
        "Billing Date": pd.Timestamp("2025-05-01"),
        "Year_Month_str": "2025-05",
        "Model": "Ray ZR",
        "Province": "Western",
        "RM": "RM1",
        "ASE": "ASE1",
        "Dealer": "Dealer A",
        "Dealer Code": "D001",
        "Net Sales": 500_000.0,
        "Status": "Sold",
    }
    records = [{**defaults, **r} for r in rows]
    return pd.DataFrame(records)


class TestClassifyVinStatus:
    def test_sold_vin(self) -> None:
        df = _make_mcsi([{"VIN": "V001", "SlsVolQty": 1}])
        status = classify_vin_status(df)
        assert status.loc[status["VIN"] == "V001", "Status"].values[0] == "Sold"

    def test_returned_vin(self) -> None:
        # VIN appears twice: sold then returned (net = 0)
        df = _make_mcsi([
            {"VIN": "V001", "SlsVolQty": 1},
            {"VIN": "V001", "SlsVolQty": -1},
        ])
        # Business rule: sum == 0 → Returned
        status = classify_vin_status(df)
        assert status.loc[status["VIN"] == "V001", "Status"].values[0] == "Returned"

    def test_pure_zero_vin(self) -> None:
        df = _make_mcsi([{"VIN": "V002", "SlsVolQty": 0}])
        status = classify_vin_status(df)
        assert status.loc[status["VIN"] == "V002", "Status"].values[0] == "Returned"

    def test_multiple_vins(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "SlsVolQty": 1},
            {"VIN": "V002", "SlsVolQty": 0},
            {"VIN": "V003", "SlsVolQty": 1},
        ])
        status = classify_vin_status(df)
        assert len(status) == 3
        sold = status[status["Status"] == "Sold"]
        returned = status[status["Status"] == "Returned"]
        assert len(sold) == 2
        assert len(returned) == 1


class TestMonthlyTrend:
    def test_counts_unique_vins_per_month(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "Year_Month_str": "2025-05"},
            {"VIN": "V002", "Year_Month_str": "2025-05"},
            {"VIN": "V003", "Year_Month_str": "2025-06"},
        ])
        result = monthly_sales_trend(df)
        may = result[result["Month"] == "2025-05"]
        assert may["Units_Sold"].values[0] == 2

    def test_sorted_ascending(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "Year_Month_str": "2025-06"},
            {"VIN": "V002", "Year_Month_str": "2025-05"},
        ])
        result = monthly_sales_trend(df)
        assert list(result["Month"]) == ["2025-05", "2025-06"]


class TestHierarchySummary:
    def test_province_totals(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "Province": "Western"},
            {"VIN": "V002", "Province": "Western"},
            {"VIN": "V003", "Province": "Central"},
        ])
        result = hierarchy_summary(df, ["Province"], "Province")
        western = result[result["Province"] == "Western"]
        assert western["Units_Sold"].values[0] == 2

    def test_sorted_descending(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "Province": "Central"},
            {"VIN": "V002", "Province": "Western"},
            {"VIN": "V003", "Province": "Western"},
        ])
        result = hierarchy_summary(df, ["Province"], "Province")
        assert result.iloc[0]["Province"] == "Western"


class TestModelMix:
    def test_share_sums_to_100(self) -> None:
        df = _make_mcsi([
            {"VIN": "V001", "Model": "Ray ZR"},
            {"VIN": "V002", "Model": "MT 15"},
            {"VIN": "V003", "Model": "Ray ZR"},
        ])
        result = model_mix(df)
        assert abs(result["Share_%"].sum() - 100.0) < 0.1


class TestSummaryKpis:
    def test_return_rate_calculation(self) -> None:
        sold = _make_mcsi([{"VIN": "V001"}, {"VIN": "V002"}])
        returned = _make_mcsi([{"VIN": "V003", "Status": "Returned"}])
        full = pd.concat([sold, returned])
        full["Status"] = ["Sold", "Sold", "Returned"]
        kpis = summary_kpis(sold, returned, full)
        assert kpis["Total Units Sold"] == 2
        assert kpis["Total Returns"] == 1
        assert abs(kpis["Return Rate %"] - 33.33) < 0.1
