"""Overview and KPI endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.deps import (
    get_classification, get_policy, get_rl_policy,
    get_stock_tracker, pipeline_status, pipeline_freshness,
)
from src.api.schemas import KpiResponse, OverviewResponse, PipelineStatus, StockStatusBreakdown

router = APIRouter(prefix="/overview", tags=["Overview"])


@router.get("/pipeline", response_model=PipelineStatus)
def get_pipeline_status() -> PipelineStatus:
    return PipelineStatus(**pipeline_status())


@router.get("/pipeline/freshness")
def get_pipeline_freshness() -> dict[str, str | None]:
    """Returns ISO-format last-run timestamp per stage artifact, or null if not yet run."""
    return pipeline_freshness()


@router.get("/kpis", response_model=OverviewResponse)
def get_overview() -> OverviewResponse:
    clf = get_classification()
    stk = get_stock_tracker()
    pol = get_policy()
    rl  = get_rl_policy()

    total_skus  = len(clf)
    active_skus = int((clf["avg_monthly_demand"] > 0).sum()) if "avg_monthly_demand" in clf.columns else 0

    status_counts: dict[str, int] = {}
    if "stock_status" in stk.columns:
        status_counts = {k: int(v) for k, v in stk["stock_status"].value_counts().items()}

    stockout = int(status_counts.get("stockout", 0))
    critical = int(status_counts.get("critical", 0))
    excess   = int(status_counts.get("excess",   0))

    urgency_counts: dict[str, int] = {}
    immediate = soon = planned = 0
    total_order_val = 0.0
    if "order_urgency" in pol.columns:
        urgency_counts = {k: int(v) for k, v in pol["order_urgency"].value_counts().items()}
        immediate = urgency_counts.get("immediate", 0)
        soon      = urgency_counts.get("soon",      0)
        planned   = urgency_counts.get("planned",   0)
    if "net_requirement" in pol.columns and "unit_value_lkr" in pol.columns:
        order_rows = pol[pol["order_urgency"].isin(["immediate", "soon", "planned"])]
        total_order_val = float((order_rows["net_requirement"] * order_rows["unit_value_lkr"]).sum())

    total_stock_val  = float(stk["stock_value_lkr"].sum()) if "stock_value_lkr" in stk.columns else 0.0
    excess_stock_val = float(
        stk.loc[stk["stock_status"] == "excess", "stock_value_lkr"].sum()
    ) if "stock_value_lkr" in stk.columns and "stock_status" in stk.columns else 0.0

    # Average coverage — exclude non-movers (coverage sentinel = 999)
    avg_coverage = 0.0
    if "coverage_months" in stk.columns and "avg_monthly_demand" in stk.columns:
        active_stk = stk[(stk["avg_monthly_demand"] > 0) & (stk["coverage_months"] < 999)]
        avg_coverage = float(active_stk["coverage_months"].mean()) if len(active_stk) else 0.0

    sanity_count = int(pol["sanity_flag"].sum()) if "sanity_flag" in pol.columns else 0

    # RL order reduction — avg pct reduction for active SKUs with mult < 1.0
    rl_reduction = 0.0
    if "rl_multiplier" in rl.columns and "avg_monthly_demand" in rl.columns:
        active_rl = rl[(rl["avg_monthly_demand"] > 0) & rl["rl_multiplier"].notna()]
        if len(active_rl):
            rl_reduction = float((1.0 - active_rl["rl_multiplier"]).clip(lower=0).mean() * 100.0)

    abc_counts  = {k: int(v) for k, v in clf["abc"].value_counts().items()} if "abc" in clf.columns else {}
    tier_counts = {k: int(v) for k, v in pol["policy_tier"].value_counts().items()} if "policy_tier" in pol.columns else {}
    ss_method_counts = {k: int(v) for k, v in pol["ss_method"].value_counts().items()} if "ss_method" in pol.columns else {}

    return OverviewResponse(
        kpis=KpiResponse(
            total_skus=total_skus,
            active_skus=active_skus,
            stockout_skus=stockout,
            critical_skus=critical,
            excess_skus=excess,
            immediate_orders=immediate,
            soon_orders=soon,
            planned_orders=planned,
            total_order_value_lkr=round(total_order_val, 0),
            total_stock_value_lkr=round(total_stock_val, 0),
            excess_stock_value_lkr=round(excess_stock_val, 0),
            avg_coverage_months=round(avg_coverage, 2),
            sanity_flag_count=sanity_count,
            rl_avg_order_reduction_pct=round(rl_reduction, 2),
        ),
        stock_status=StockStatusBreakdown(
            **{k: int(v) for k, v in status_counts.items() if k in StockStatusBreakdown.model_fields}
        ),
        abc_counts=abc_counts,
        tier_counts=tier_counts,
        urgency_counts=urgency_counts,
        ss_method_counts=ss_method_counts,
    )
