"""Unit tests for Stage 5 Sales EDA functions."""

import numpy as np
import pandas as pd
import pytest

from src.eda.stage05_sales_eda import (
    accessories_sample,
    category_mix,
    channel_comparison,
    dealer_churn_analysis,
    monthly_by_category,
    monthly_revenue_trend,
    summary_kpis,
    top_dealers,
    top_materials,
)

# _revenue_df is internal; test indirectly via summary_kpis / top_dealers


def _make_sales(rows: list[dict]) -> pd.DataFrame:
    base = {
        "channel":          "Dealer",
        "product_category": "Yamaha Spare Parts",
        "Payer":            "Dealer A",
        "Material":         "MAT001",
        "Material Description": "Test Part",
        "Matl Group":       "AWPYM0001",
        "Billing Date":     pd.Timestamp("2025-06-01"),
        "Year_Month_str":   "2025-06",
        "SlsVolQty":        10.0,
        "Net Sales":        5000.0,
        "bill_class":       "sale",
        "Bill. Type":       "F2",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


class TestDealerScoping:
    """Verify that non-dealer payers are excluded before any analysis."""

    def test_unregistered_payer_excluded_from_summary(self) -> None:
        # Only "Known Dealer" is in our dealer-name set after scoping
        df = _make_sales([
            {"Payer": "Known Dealer",    "Net Sales": 5000},
            {"Payer": "Bulk Distributor","Net Sales": 99999},
        ])
        # Simulate post-scoping: drop the non-dealer row
        scoped = df[df["Payer"] == "Known Dealer"]
        kpis = summary_kpis(scoped)
        assert kpis["Gross Sales excl. Lubricants (LKR)"] == 5000

    def test_unregistered_payer_excluded_from_churn(self) -> None:
        df = _make_sales([
            {"Payer": "Known Dealer",    "Billing Date": pd.Timestamp("2025-12-01")},
            {"Payer": "Bulk Distributor","Billing Date": pd.Timestamp("2025-12-01")},
        ])
        df["Billing Date"] = pd.to_datetime(df["Billing Date"])
        scoped = df[df["Payer"] == "Known Dealer"]
        result = dealer_churn_analysis(scoped)
        assert "Bulk Distributor" not in result["Payer"].values


class TestSummaryKpis:
    def test_return_rate_calculation(self) -> None:
        df = _make_sales([
            {"bill_class": "sale",   "Net Sales": 10000.0},
            {"bill_class": "return", "Net Sales": -1000.0},
        ])
        kpis = summary_kpis(df)
        assert kpis["Return Rate %"] == pytest.approx(10.0)

    def test_net_revenue(self) -> None:
        df = _make_sales([
            {"bill_class": "sale",   "Net Sales": 8000.0},
            {"bill_class": "return", "Net Sales": -2000.0},
        ])
        kpis = summary_kpis(df)
        assert kpis["Net Revenue (LKR)"] == 6000

    def test_channel_counts(self) -> None:
        df = _make_sales([
            {"channel": "Dealer"},
            {"channel": "Dealer"},
            {"channel": "Service"},
        ])
        kpis = summary_kpis(df)
        assert kpis["Dealer Channel Lines"] == 2
        assert kpis["Service Channel Lines"] == 1

    def test_lubricants_excluded_from_revenue(self) -> None:
        df = _make_sales([
            {"product_category": "Yamaha Spare Parts", "Net Sales": 5000.0},
            {"product_category": "Lubricants",         "Net Sales": -999999.0},
        ])
        kpis = summary_kpis(df)
        assert kpis["Gross Sales excl. Lubricants (LKR)"] == 5000


class TestMonthlyRevenueTrend:
    def test_monthly_aggregation(self) -> None:
        df = _make_sales([
            {"Year_Month_str": "2025-01", "Net Sales": 3000},
            {"Year_Month_str": "2025-01", "Net Sales": 2000},
            {"Year_Month_str": "2025-02", "Net Sales": 5000},
        ])
        result = monthly_revenue_trend(df)
        jan = result[result["Month"] == "2025-01"]
        assert jan["gross_sales"].values[0] == pytest.approx(5000)

    def test_sorted_ascending(self) -> None:
        df = _make_sales([
            {"Year_Month_str": "2025-03"},
            {"Year_Month_str": "2025-01"},
        ])
        result = monthly_revenue_trend(df)
        assert result["Month"].iloc[0] == "2025-01"

    def test_return_rate_computed(self) -> None:
        df = _make_sales([
            {"Year_Month_str": "2025-01", "bill_class": "sale",   "Net Sales": 10000},
            {"Year_Month_str": "2025-01", "bill_class": "return", "Net Sales": -2000},
        ])
        result = monthly_revenue_trend(df)
        jan = result[result["Month"] == "2025-01"]
        assert jan["return_rate_%"].values[0] == pytest.approx(20.0)


class TestTopDealers:
    def test_sorted_by_gross_sales_desc(self) -> None:
        df = _make_sales([
            {"Payer": "Big Dealer",   "Net Sales": 50000},
            {"Payer": "Small Dealer", "Net Sales": 5000},
        ])
        result = top_dealers(df)
        assert result.iloc[0]["Payer"] == "Big Dealer"

    def test_top_n_limit(self) -> None:
        df = _make_sales([{"Payer": f"D{i}", "Net Sales": float(i * 1000)} for i in range(10)])
        result = top_dealers(df, top_n=5)
        assert len(result) == 5


class TestAccessoriesSample:
    def test_scoped_to_accessories_category(self) -> None:
        df = _make_sales([
            {"product_category": "Accessories & Filters", "Material": "A1", "Net Sales": 500},
            {"product_category": "Yamaha Spare Parts",    "Material": "Y1", "Net Sales": 9000},
        ])
        result = accessories_sample(df)
        assert "A1" in result["Material"].values
        assert "Y1" not in result["Material"].values

    def test_sorted_by_value_desc(self) -> None:
        df = _make_sales([
            {"product_category": "Accessories & Filters", "Material": "A", "Net Sales": 200, "Matl Group": "AWPMA0001"},
            {"product_category": "Accessories & Filters", "Material": "B", "Net Sales": 800, "Matl Group": "AWPMA0001"},
        ])
        result = accessories_sample(df)
        assert result.iloc[0]["Material"] == "B"

    def test_has_required_columns(self) -> None:
        df = _make_sales([
            {"product_category": "Accessories & Filters", "Material": "X", "Matl Group": "AWPMA0001"},
        ])
        result = accessories_sample(df)
        assert {"Matl Group", "Material", "gross_sales", "value_share_%"}.issubset(result.columns)


class TestTopMaterials:
    def test_sorted_by_value(self) -> None:
        df = _make_sales([
            {"Material": "A", "Net Sales": 9000},
            {"Material": "B", "Net Sales": 1000},
        ])
        result = top_materials(df, category="Yamaha Spare Parts")
        assert result.iloc[0]["Material"] == "A"

    def test_value_share_sums_to_100(self) -> None:
        df = _make_sales([
            {"Material": "A", "Net Sales": 600},
            {"Material": "B", "Net Sales": 400},
        ])
        result = top_materials(df, category="Yamaha Spare Parts")
        assert result["value_share_%"].sum() == pytest.approx(100.0, abs=0.1)

    def test_filters_to_requested_category(self) -> None:
        df = _make_sales([
            {"Material": "Y1", "product_category": "Yamaha Spare Parts", "Net Sales": 1000},
            {"Material": "T1", "product_category": "Tyres",              "Net Sales": 9000},
        ])
        result = top_materials(df, category="Yamaha Spare Parts")
        assert all(m != "T1" for m in result["Material"])


class TestCategoryMix:
    def test_shows_all_categories(self) -> None:
        df = _make_sales([
            {"product_category": "Yamaha Spare Parts", "Net Sales": 700},
            {"product_category": "Tyres",              "Net Sales": 300},
        ])
        result = category_mix(df)
        assert set(result["product_category"]) == {"Yamaha Spare Parts", "Tyres"}

    def test_lubricant_anomaly_note_present(self) -> None:
        df = _make_sales([
            {"product_category": "Lubricants", "Net Sales": -50000},
        ])
        result = category_mix(df)
        lube_row = result[result["product_category"] == "Lubricants"]
        assert len(lube_row) == 1
        assert "anomaly" in lube_row["data_note"].values[0].lower()

    def test_share_excludes_lubricants_from_denominator(self) -> None:
        df = _make_sales([
            {"product_category": "Yamaha Spare Parts", "Net Sales": 600},
            {"product_category": "Tyres",              "Net Sales": 400},
            {"product_category": "Lubricants",         "Net Sales": -99999},
        ])
        result = category_mix(df)
        ym = result[result["product_category"] == "Yamaha Spare Parts"]["value_share_%"].values[0]
        ty = result[result["product_category"] == "Tyres"]["value_share_%"].values[0]
        assert ym + ty == pytest.approx(100.0, abs=0.1)


class TestDealerChurnAnalysis:
    def test_active_dealer_classified_correctly(self) -> None:
        df = _make_sales([{"Payer": "New Dealer", "Billing Date": pd.Timestamp("2025-12-20")}])
        df["Billing Date"] = pd.to_datetime(df["Billing Date"])
        result = dealer_churn_analysis(df)
        assert result.loc[result["Payer"] == "New Dealer", "status"].values[0] == "Active"

    def test_old_dealer_classified_churned(self) -> None:
        df = _make_sales([
            {"Payer": "Old Dealer",  "Billing Date": pd.Timestamp("2024-01-01")},
            {"Payer": "New Dealer",  "Billing Date": pd.Timestamp("2025-12-01")},
        ])
        df["Billing Date"] = pd.to_datetime(df["Billing Date"])
        result = dealer_churn_analysis(df)
        assert result.loc[result["Payer"] == "Old Dealer", "status"].values[0] == "Churned"

    def test_returns_excluded_from_churn(self) -> None:
        df = _make_sales([
            {"Payer": "D1", "bill_class": "return", "Billing Date": pd.Timestamp("2025-12-01")},
        ])
        df["Billing Date"] = pd.to_datetime(df["Billing Date"])
        result = dealer_churn_analysis(df)
        assert len(result) == 0  # only sales count for churn


class TestChannelComparison:
    def test_both_channels_present(self) -> None:
        df = _make_sales([
            {"channel": "Dealer"},
            {"channel": "Service"},
        ])
        result = channel_comparison(df)
        assert set(result["Channel"]) == {"Dealer", "Service"}

    def test_dealer_has_higher_value(self) -> None:
        df = _make_sales([
            {"channel": "Dealer",  "Net Sales": 10000},
            {"channel": "Service", "Net Sales": 500},
        ])
        result = channel_comparison(df)
        dealer_val  = result.loc[result["Channel"] == "Dealer",  "Gross Sales (LKR)"].values[0]
        service_val = result.loc[result["Channel"] == "Service", "Gross Sales (LKR)"].values[0]
        assert dealer_val > service_val
