"""Per-SKU detail endpoint — aggregates all pipeline stages for one material."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException

from src.api.deps import (
    get_classification, get_forecast, get_monthly_demand,
    get_policy, get_rl_policy, get_stock_tracker,
)
from src.api.schemas import MonthlyDemandPoint, SKUDetail

router = APIRouter(prefix="/sku", tags=["SKU Detail"])


def _fmt_date(val) -> str | None:
    if pd.isna(val):
        return None
    try:
        return pd.Timestamp(val).strftime("%Y-%m-%d")
    except Exception:
        return str(val)


@router.get("/{material_9}", response_model=SKUDetail)
def get_sku(material_9: str) -> SKUDetail:
    clf = get_classification()
    fct = get_forecast()
    stk = get_stock_tracker()
    pol = get_policy()
    rl  = get_rl_policy()
    mth = get_monthly_demand()

    def _first(df: pd.DataFrame) -> dict:
        rows = df[df["material_9"] == material_9]
        return rows.iloc[0].to_dict() if len(rows) else {}

    c = _first(clf)
    f = _first(fct)
    s = _first(stk)
    p = _first(pol)
    r = _first(rl)

    if not c and not f and not p:
        raise HTTPException(status_code=404, detail=f"SKU '{material_9}' not found")

    # Monthly demand history
    monthly: list[MonthlyDemandPoint] = []
    if "material_9" in mth.columns and "year_month_str" in mth.columns:
        sku_mth = (
            mth[mth["material_9"] == material_9]
            .groupby("year_month_str")
            .agg(
                issue_qty=("issue_qty", "sum"),
                issue_value_lkr=("issue_value_lkr", "sum"),
                return_qty=("return_qty", "sum"),
                net_demand=("net_demand", "sum"),
            )
            .reset_index()
            .sort_values("year_month_str")
        )
        monthly = [
            MonthlyDemandPoint(
                year_month_str=str(row["year_month_str"]),
                issue_qty=float(row.get("issue_qty", 0.0)),
                issue_value_lkr=float(row.get("issue_value_lkr", 0.0)),
                return_qty=float(row.get("return_qty", 0.0)),
                net_demand=float(row.get("net_demand", 0.0)),
            )
            for _, row in sku_mth.iterrows()
        ]

    def _s(key: str, *dicts, default="") -> str:
        for d in dicts:
            v = d.get(key)
            if v is not None and str(v) not in ("", "nan", "NaT"):
                return str(v)
        return str(default)

    def _f(key: str, *dicts, default=0.0) -> float:
        for d in dicts:
            v = d.get(key)
            if v is not None:
                try:
                    fv = float(v)
                    if not pd.isna(fv):
                        return fv
                except (TypeError, ValueError):
                    pass
        return float(default)

    def _b(key: str, *dicts, default=False) -> bool:
        for d in dicts:
            v = d.get(key)
            if v is not None:
                return bool(v)
        return bool(default)

    return SKUDetail(
        material_9=material_9,
        description=_s("description", c, p, f),
        abc=_s("abc", c, p, f),
        xyz=_s("xyz", c, p, f),
        fsn=_s("fsn", c, p, f),
        abc_xyz_fsn=_s("abc_xyz_fsn", c, p, f),
        policy_tier=_s("policy_tier", c, p, f),
        demand_category=_s("demand_category", c) or None,
        demand_segment=_s("demand_segment", c) or None,
        in_ssop=_b("in_ssop", c) if "in_ssop" in c else None,
        avg_monthly_demand=_f("avg_monthly_demand", c, f, p),
        demand_std_monthly=_f("demand_std_monthly", f, p),
        p_zero=_f("p_zero", c),
        cv=_f("cv", c, p),
        active_months=int(_f("active_months", c, f)),
        total_issue_qty=_f("total_issue_qty", c),
        total_issue_value_lkr=_f("total_issue_value_lkr", c, f),
        # Stock
        stock_on_hand=_f("stock_on_hand", s, p),
        stock_value_lkr=_f("stock_value_lkr", s, p),
        stock_status=_s("stock_status", s, p),
        coverage_months=_f("coverage_months", s, p),
        days_of_stock=_f("days_of_stock", s, p),
        total_receipts=_f("total_receipts", s),
        total_issues=_f("total_issues", s),
        total_returns=_f("total_returns", s),
        last_movement_date=_fmt_date(s.get("last_movement_date")),
        # Forecast
        method=_s("method", f, p),
        forecast_m1=_f("forecast_m1", f, p),
        forecast_m2=_f("forecast_m2", f, p),
        forecast_m3=_f("forecast_m3", f, p),
        forecast_lt=_f("forecast_lt", f, p),
        # Policy
        service_level=_f("service_level", p),
        z_score=_f("z_score", p),
        safety_stock=_f("safety_stock", p),
        ss_method=_s("ss_method", p),
        rol=_f("rol", p),
        roq=_f("roq", p),
        net_requirement=_f("net_requirement", p),
        order_urgency=_s("order_urgency", p, default="none"),
        unit_value_lkr=_f("unit_value_lkr", p, r),
        sanity_flag=_b("sanity_flag", p),
        sanity_note=_s("sanity_note", p),
        # RL
        rl_multiplier=float(r["rl_multiplier"]) if r.get("rl_multiplier") is not None else None,
        rl_recommended_qty=float(r["rl_recommended_qty"]) if r.get("rl_recommended_qty") is not None else None,
        rl_flag=bool(r["rl_flag"]) if r.get("rl_flag") is not None else None,
        monthly_demand=monthly,
    )
