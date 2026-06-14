"""Unit tests for Stage 13 shipment report functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.reports.stage13_shipment_report import (
    build_excluded_list,
    build_order_list,
    order_by_abc,
    order_by_tier,
    summary_kpis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy_row(
    sku: str,
    net_req: float,
    roq: float,
    urgency: str,
    stock: float = 5.0,
    demand: float = 5.0,
    abc: str = "B",
    tier: str = "managed",
    status: str = "low",
    unit_val: float = 500.0,
    sanity: bool = False,
    ss_method: str = "Classical",
) -> dict:
    return {
        "material_9":             sku,
        "description":            f"PART {sku}",
        "abc":                    abc,
        "xyz":                    "X",
        "fsn":                    "F",
        "abc_xyz_fsn":            f"{abc}XF",
        "policy_tier":            tier,
        "order_urgency":          urgency,
        "stock_status":           status,
        "stock_on_hand":          stock,
        "coverage_months":        stock / max(demand, 1e-9),
        "avg_monthly_demand":     demand,
        "net_requirement":        net_req,
        "roq":                    roq,
        "forecast_m1":            demand,
        "forecast_m2":            demand,
        "forecast_m3":            demand,
        "forecast_lt":            demand * 3,
        "safety_stock":           demand * 0.5,
        "demand_std_monthly":     demand * 0.2,
        "demand_std_lt":          demand * 0.2 * 1.73,
        "unit_value_lkr":         unit_val,
        "rol":                    demand * 3 + demand * 0.5,
        "sanity_flag":            sanity,
        "sanity_note":            "ROL > 3× lead-time demand" if sanity else "",
        "ss_method":              ss_method,
        "method":                 "AutoETS",
        "active_months":          6,
        "total_issue_value_lkr":  demand * 6 * unit_val,
    }


def _make_policy(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# build_order_list
# ---------------------------------------------------------------------------

class TestBuildOrderList:
    def test_only_non_zero_net_req_included(self) -> None:
        policy = _make_policy([
            _policy_row("ORDER1", net_req=20.0, roq=30.0, urgency="immediate"),
            _policy_row("SKIP1",  net_req=0.0,  roq=30.0, urgency="none"),
        ])
        result = build_order_list(policy)
        assert set(result["material_9"]) == {"ORDER1"}

    def test_recommended_qty_is_max_of_net_req_and_roq(self) -> None:
        policy = _make_policy([
            _policy_row("SKU1", net_req=10.0, roq=30.0, urgency="planned"),
            _policy_row("SKU2", net_req=50.0, roq=20.0, urgency="immediate"),
        ])
        result = build_order_list(policy)
        row1 = result[result["material_9"] == "SKU1"].iloc[0]
        row2 = result[result["material_9"] == "SKU2"].iloc[0]
        assert row1["recommended_qty"] == pytest.approx(30.0)  # roq > net_req
        assert row2["recommended_qty"] == pytest.approx(50.0)  # net_req > roq

    def test_order_value_correct(self) -> None:
        policy = _make_policy([
            _policy_row("SKU1", net_req=20.0, roq=20.0, urgency="planned", unit_val=1000.0),
        ])
        result = build_order_list(policy)
        assert result["order_value_lkr"].iloc[0] == pytest.approx(20_000.0)

    def test_projected_stock_computed(self) -> None:
        policy = _make_policy([
            _policy_row("SKU1", net_req=20.0, roq=30.0, urgency="planned", stock=5.0),
        ])
        result = build_order_list(policy)
        # projected = stock_on_hand + recommended_qty = 5 + 30 = 35
        assert result["projected_stock"].iloc[0] == pytest.approx(35.0)

    def test_projected_coverage_computed(self) -> None:
        policy = _make_policy([
            _policy_row("SKU1", net_req=20.0, roq=30.0, urgency="planned",
                        stock=5.0, demand=5.0),
        ])
        result = build_order_list(policy)
        # projected = 35 / 5 = 7.0 months
        assert result["projected_coverage_months"].iloc[0] == pytest.approx(7.0)

    def test_sorted_immediate_before_planned(self) -> None:
        policy = _make_policy([
            _policy_row("PLANNED",   net_req=10.0, roq=10.0, urgency="planned"),
            _policy_row("IMMEDIATE", net_req=10.0, roq=10.0, urgency="immediate"),
        ])
        result = build_order_list(policy)
        assert result.iloc[0]["material_9"] == "IMMEDIATE"

    def test_within_urgency_a_before_b(self) -> None:
        policy = _make_policy([
            _policy_row("B_SKU", net_req=10.0, roq=10.0, urgency="immediate", abc="B"),
            _policy_row("A_SKU", net_req=10.0, roq=10.0, urgency="immediate", abc="A"),
        ])
        result = build_order_list(policy)
        assert result.iloc[0]["material_9"] == "A_SKU"

    def test_recommended_qty_whole_units(self) -> None:
        policy = _make_policy([
            _policy_row("SKU1", net_req=10.7, roq=15.3, urgency="planned"),
        ])
        result = build_order_list(policy)
        assert result["recommended_qty"].iloc[0] == result["recommended_qty"].iloc[0].round(0)


# ---------------------------------------------------------------------------
# build_excluded_list
# ---------------------------------------------------------------------------

class TestBuildExcludedList:
    def test_only_zero_net_req_excluded(self) -> None:
        policy = _make_policy([
            _policy_row("IN",  net_req=10.0, roq=10.0, urgency="immediate"),
            _policy_row("OUT", net_req=0.0,  roq=10.0, urgency="none", status="excess"),
        ])
        result = build_excluded_list(policy)
        assert set(result["material_9"]) == {"OUT"}

    def test_exclusion_reason_for_non_mover(self) -> None:
        policy = _make_policy([
            _policy_row("GHOST", net_req=0.0, roq=0.0, urgency="none",
                        demand=0.0, status="stockout"),
        ])
        result = build_excluded_list(policy)
        assert "Non-moving" in result["exclusion_reason"].iloc[0]

    def test_exclusion_reason_for_excess(self) -> None:
        policy = _make_policy([
            _policy_row("BIG", net_req=0.0, roq=0.0, urgency="none",
                        stock=100.0, demand=5.0, status="excess"),
        ])
        result = build_excluded_list(policy)
        assert "Excess" in result["exclusion_reason"].iloc[0]


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def _make_scenario(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        policy = _make_policy([
            _policy_row("A1",  net_req=20.0, roq=30.0, urgency="immediate", abc="A"),
            _policy_row("B1",  net_req=15.0, roq=20.0, urgency="soon",      abc="B"),
            _policy_row("OK",  net_req=0.0,  roq=10.0, urgency="none",      status="ok"),
        ])
        order = build_order_list(policy)
        return order, policy

    def test_required_keys_present(self) -> None:
        order, policy = self._make_scenario()
        kpis = summary_kpis(order, policy)
        for key in (
            "Report Date",
            "SKUs to Order (this cycle)",
            "Total Recommended Qty (units)",
            "Total Order Value Est. (LKR)",
        ):
            assert key in kpis

    def test_sku_counts_correct(self) -> None:
        order, policy = self._make_scenario()
        kpis = summary_kpis(order, policy)
        assert kpis["SKUs to Order (this cycle)"] == 2

    def test_total_value_correct(self) -> None:
        order, policy = self._make_scenario()
        kpis = summary_kpis(order, policy)
        expected = order["order_value_lkr"].sum()
        assert kpis["Total Order Value Est. (LKR)"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# order_by_tier / order_by_abc
# ---------------------------------------------------------------------------

class TestOrderAggregations:
    def _make_order(self) -> pd.DataFrame:
        policy = _make_policy([
            _policy_row("A1", net_req=20.0, roq=30.0, urgency="immediate",
                        abc="A", tier="critical"),
            _policy_row("B1", net_req=10.0, roq=15.0, urgency="planned",
                        abc="B", tier="managed"),
            _policy_row("B2", net_req=5.0,  roq=8.0,  urgency="planned",
                        abc="B", tier="managed"),
        ])
        return build_order_list(policy)

    def test_tier_agg_has_correct_counts(self) -> None:
        order = self._make_order()
        agg   = order_by_tier(order)
        managed_row = agg[agg["policy_tier"] == "managed"]
        assert managed_row["sku_count"].iloc[0] == 2

    def test_abc_agg_covers_all_classes(self) -> None:
        order = self._make_order()
        agg   = order_by_abc(order)
        assert set(agg["abc"]) >= {"A", "B"}

    def test_critical_sorted_first(self) -> None:
        order = self._make_order()
        agg   = order_by_tier(order)
        assert agg.iloc[0]["policy_tier"] == "critical"
