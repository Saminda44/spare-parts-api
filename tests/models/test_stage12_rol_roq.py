"""Unit tests for Stage 12 ROL/ROQ/Buffer policy functions."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.models.inventory_policy.stage12_rol_roq import (
    _eoq_scalar,
    apply_sanity_checks,
    compute_net_requirement,
    compute_rol,
    compute_roq,
    compute_safety_stock,
    compute_unit_value,
    summary_kpis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_row(**overrides) -> dict:
    row = {
        "material_9":            "SKU1",
        "description":           "TEST PART",
        "abc":                   "B",
        "xyz":                   "X",
        "fsn":                   "F",
        "abc_xyz_fsn":           "BXF",
        "policy_tier":           "managed",
        "active_months":         6,
        "avg_monthly_demand":    10.0,
        "total_issue_value_lkr": 60_000.0,
        "forecast_lt":           30.0,
        "forecast_m1":           10.0,
        "forecast_m2":           10.0,
        "forecast_m3":           10.0,
        "demand_std_monthly":    2.0,
        "demand_std_lt":         3.46,
        "stock_on_hand":         20.0,
        "coverage_months":       2.0,
        "stock_status":          "low",
        "method":                "AutoETS",
        "cv":                    0.3,
        "stock_value_lkr":       2_000.0,
    }
    row.update(overrides)
    return row


def _df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------
# compute_unit_value
# ---------------------------------------------------------------------------

class TestComputeUnitValue:
    def test_unit_value_correct(self) -> None:
        # total_issue_value=60000, avg_monthly_demand=10, active_months=6 → qty=60 → 1000 LKR/unit
        df = _df(_base_row())
        result = compute_unit_value(df)
        assert result.loc[0, "unit_value_lkr"] == pytest.approx(1000.0)

    def test_zero_demand_clips_to_zero(self) -> None:
        df = _df(_base_row(avg_monthly_demand=0.0, total_issue_value_lkr=0.0, active_months=0))
        result = compute_unit_value(df)
        assert result.loc[0, "unit_value_lkr"] == pytest.approx(0.0)

    def test_no_negative_unit_value(self) -> None:
        df = _df(_base_row(total_issue_value_lkr=-100.0))
        result = compute_unit_value(df)
        assert result.loc[0, "unit_value_lkr"] >= 0.0


# ---------------------------------------------------------------------------
# compute_safety_stock
# ---------------------------------------------------------------------------

class TestComputeSafetyStock:
    def test_critical_z_score(self) -> None:
        df = _df(_base_row(policy_tier="critical", demand_std_lt=4.0))
        result = compute_safety_stock(df)
        # z ≈ 2.326 for 99%
        assert result.loc[0, "safety_stock"] == pytest.approx(2.326 * 4.0, rel=1e-3)

    def test_managed_z_score(self) -> None:
        df = _df(_base_row(policy_tier="managed", demand_std_lt=4.0))
        result = compute_safety_stock(df)
        assert result.loc[0, "safety_stock"] == pytest.approx(1.960 * 4.0, rel=1e-3)

    def test_watch_z_score(self) -> None:
        df = _df(_base_row(policy_tier="watch", demand_std_lt=4.0))
        result = compute_safety_stock(df)
        assert result.loc[0, "safety_stock"] == pytest.approx(1.645 * 4.0, rel=1e-3)

    def test_rationalise_z_score(self) -> None:
        df = _df(_base_row(policy_tier="rationalise", demand_std_lt=4.0))
        result = compute_safety_stock(df)
        assert result.loc[0, "safety_stock"] == pytest.approx(1.282 * 4.0, rel=1e-3)

    def test_safety_stock_non_negative(self) -> None:
        df = _df(_base_row(demand_std_lt=0.0))
        result = compute_safety_stock(df)
        assert result.loc[0, "safety_stock"] >= 0.0

    def test_service_level_column_added(self) -> None:
        df = _df(_base_row(policy_tier="critical"))
        result = compute_safety_stock(df)
        assert "service_level" in result.columns
        assert result.loc[0, "service_level"] == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# compute_rol
# ---------------------------------------------------------------------------

class TestComputeRol:
    def test_rol_equals_forecast_plus_ss(self) -> None:
        df = _df(_base_row(forecast_lt=30.0, demand_std_lt=4.0, policy_tier="managed"))
        df = compute_safety_stock(df)
        result = compute_rol(df)
        expected = 30.0 + 1.960 * 4.0
        assert result.loc[0, "rol"] == pytest.approx(expected, rel=1e-3)

    def test_rol_non_negative(self) -> None:
        df = _df(_base_row(forecast_lt=0.0, demand_std_lt=0.0))
        df = compute_safety_stock(df)
        result = compute_rol(df)
        assert result.loc[0, "rol"] >= 0.0

    def test_zero_demand_zero_rol(self) -> None:
        df = _df(_base_row(avg_monthly_demand=0.0, forecast_lt=0.0, demand_std_lt=0.0))
        df = compute_safety_stock(df)
        result = compute_rol(df)
        assert result.loc[0, "rol"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _eoq_scalar
# ---------------------------------------------------------------------------

class TestEoqScalar:
    def test_known_value(self) -> None:
        # D=120, S=5000, unit=1000, rate=0.20 → H=200 → EOQ=√(2×120×5000/200)=√6000≈77.46
        result = _eoq_scalar(d_annual=120.0, unit_value=1000.0)
        assert result == pytest.approx(math.sqrt(2 * 120 * 5000 / 200), rel=1e-3)

    def test_zero_demand_returns_zero(self) -> None:
        assert _eoq_scalar(d_annual=0.0, unit_value=1000.0) == pytest.approx(0.0)

    def test_zero_unit_value_returns_zero(self) -> None:
        assert _eoq_scalar(d_annual=120.0, unit_value=0.0) == pytest.approx(0.0)

    def test_positive_result(self) -> None:
        assert _eoq_scalar(d_annual=60.0, unit_value=500.0) > 0.0


# ---------------------------------------------------------------------------
# compute_roq
# ---------------------------------------------------------------------------

class TestComputeRoq:
    def _with_unit_value(self, **overrides) -> pd.DataFrame:
        row = _base_row(**overrides)
        df  = pd.DataFrame([row])
        df["unit_value_lkr"] = 1000.0  # pre-computed
        return df

    def test_critical_uses_eoq(self) -> None:
        df = self._with_unit_value(policy_tier="critical", avg_monthly_demand=10.0)
        result = compute_roq(df)
        # EOQ > 0 and ≥ 1
        assert result.loc[0, "roq"] >= 1.0

    def test_managed_uses_eoq(self) -> None:
        df = self._with_unit_value(policy_tier="managed", avg_monthly_demand=10.0)
        result = compute_roq(df)
        assert result.loc[0, "roq"] >= 1.0

    def test_watch_uses_forecast_lt(self) -> None:
        df = self._with_unit_value(policy_tier="watch", forecast_lt=25.0, avg_monthly_demand=8.0)
        result = compute_roq(df)
        assert result.loc[0, "roq"] == pytest.approx(25.0)

    def test_rationalise_uses_avg_demand(self) -> None:
        df = self._with_unit_value(policy_tier="rationalise", avg_monthly_demand=6.0, forecast_lt=18.0)
        result = compute_roq(df)
        assert result.loc[0, "roq"] == pytest.approx(6.0)

    def test_non_mover_gets_zero_roq(self) -> None:
        df = self._with_unit_value(avg_monthly_demand=0.0, forecast_lt=0.0)
        result = compute_roq(df)
        assert result.loc[0, "roq"] == pytest.approx(0.0)

    def test_roq_at_least_one_for_active(self) -> None:
        # Even with a tiny demand, ROQ ≥ 1
        df = self._with_unit_value(policy_tier="watch", avg_monthly_demand=0.1, forecast_lt=0.3)
        result = compute_roq(df)
        assert result.loc[0, "roq"] >= 1.0


# ---------------------------------------------------------------------------
# compute_net_requirement
# ---------------------------------------------------------------------------

class TestComputeNetRequirement:
    def _full_df(self, stock: float, rol: float, ss: float, status: str) -> pd.DataFrame:
        row = _base_row(stock_on_hand=stock, stock_status=status)
        df  = pd.DataFrame([row])
        df["rol"]          = rol
        df["safety_stock"] = ss
        return df

    def test_positive_net_req_when_stock_below_rol(self) -> None:
        df = self._full_df(stock=10.0, rol=38.0, ss=8.0, status="low")
        result = compute_net_requirement(df)
        assert result.loc[0, "net_requirement"] == pytest.approx(28.0)

    def test_zero_net_req_when_stock_above_rol(self) -> None:
        df = self._full_df(stock=50.0, rol=38.0, ss=8.0, status="ok")
        result = compute_net_requirement(df)
        assert result.loc[0, "net_requirement"] == pytest.approx(0.0)

    def test_urgency_immediate_for_stockout_with_demand(self) -> None:
        df = self._full_df(stock=0.0, rol=30.0, ss=5.0, status="stockout")
        result = compute_net_requirement(df)
        # net_requirement = 30 > 0, so urgency = immediate
        assert result.loc[0, "order_urgency"] == "immediate"

    def test_urgency_none_for_stockout_non_mover(self) -> None:
        # Zero demand non-mover: ROL=0, stock=0 → net_req=0 → no urgency
        df = self._full_df(stock=0.0, rol=0.0, ss=0.0, status="stockout")
        result = compute_net_requirement(df)
        assert result.loc[0, "order_urgency"] == "none"

    def test_urgency_soon_for_low(self) -> None:
        df = self._full_df(stock=5.0, rol=30.0, ss=5.0, status="low")
        result = compute_net_requirement(df)
        assert result.loc[0, "order_urgency"] == "soon"

    def test_urgency_none_when_no_order_needed(self) -> None:
        df = self._full_df(stock=60.0, rol=30.0, ss=5.0, status="excess")
        result = compute_net_requirement(df)
        assert result.loc[0, "order_urgency"] == "none"


# ---------------------------------------------------------------------------
# apply_sanity_checks
# ---------------------------------------------------------------------------

class TestApplySanityChecks:
    def _sanity_df(self, rol: float, roq: float, forecast_lt: float, avg_demand: float) -> pd.DataFrame:
        row = _base_row(forecast_lt=forecast_lt, avg_monthly_demand=avg_demand)
        df  = pd.DataFrame([row])
        df["rol"] = rol
        df["roq"] = roq
        return df

    def test_no_flag_for_normal_values(self) -> None:
        df = self._sanity_df(rol=35.0, roq=40.0, forecast_lt=30.0, avg_demand=10.0)
        result = apply_sanity_checks(df)
        assert not result.loc[0, "sanity_flag"]

    def test_flag_when_rol_exceeds_3x(self) -> None:
        # ROL = 100 > 3 × 30 = 90 → flag
        df = self._sanity_df(rol=100.0, roq=40.0, forecast_lt=30.0, avg_demand=10.0)
        result = apply_sanity_checks(df)
        assert result.loc[0, "sanity_flag"]
        assert "ROL" in result.loc[0, "sanity_note"]

    def test_flag_when_roq_exceeds_3x(self) -> None:
        df = self._sanity_df(rol=35.0, roq=100.0, forecast_lt=30.0, avg_demand=10.0)
        result = apply_sanity_checks(df)
        assert result.loc[0, "sanity_flag"]
        assert "ROQ" in result.loc[0, "sanity_note"]

    def test_no_flag_for_zero_demand_sku(self) -> None:
        # Non-movers — benchmark=0, condition `benchmark > 0` prevents false flags
        df = self._sanity_df(rol=0.0, roq=0.0, forecast_lt=0.0, avg_demand=0.0)
        result = apply_sanity_checks(df)
        assert not result.loc[0, "sanity_flag"]


# ---------------------------------------------------------------------------
# summary_kpis
# ---------------------------------------------------------------------------

class TestSummaryKpis:
    def _full_policy_df(self) -> pd.DataFrame:
        rows = [
            {**_base_row(material_9="SKU1", stock_on_hand=0.0,  stock_status="stockout"),
             "rol": 30.0, "roq": 40.0, "safety_stock": 7.0,
             "net_requirement": 30.0, "order_urgency": "immediate",
             "sanity_flag": False, "unit_value_lkr": 1000.0},
            {**_base_row(material_9="SKU2", stock_on_hand=50.0, stock_status="ok"),
             "rol": 30.0, "roq": 40.0, "safety_stock": 7.0,
             "net_requirement": 0.0, "order_urgency": "none",
             "sanity_flag": False, "unit_value_lkr": 500.0},
        ]
        return pd.DataFrame(rows)

    def test_required_keys_present(self) -> None:
        df = self._full_policy_df()
        kpis = summary_kpis(df)
        for key in (
            "Total SKUs",
            "SKUs Requiring Order (this cycle)",
            "Immediate Orders",
            "Total Net Requirement (units)",
            "Sanity-Flagged SKUs",
        ):
            assert key in kpis, f"Missing KPI: {key}"

    def test_total_sku_count(self) -> None:
        df = self._full_policy_df()
        assert summary_kpis(df)["Total SKUs"] == 2

    def test_immediate_orders_count(self) -> None:
        df = self._full_policy_df()
        assert summary_kpis(df)["Immediate Orders"] == 1

    def test_total_net_requirement(self) -> None:
        df = self._full_policy_df()
        assert summary_kpis(df)["Total Net Requirement (units)"] == pytest.approx(30.0)
