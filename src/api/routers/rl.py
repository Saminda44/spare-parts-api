"""RL policy endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from src.api.deps import get_rl_policy
from src.api.schemas import RLResponse, RLRow, RLSummary

router = APIRouter(prefix="/rl", tags=["RL Policy"])


def _to_row(r: pd.Series) -> RLRow:
    return RLRow(
        material_9=str(r.get("material_9", "")),
        description=str(r.get("description", "")),
        abc=str(r.get("abc", "")),
        xyz=str(r.get("xyz", "")),
        fsn=str(r.get("fsn", "")),
        policy_tier=str(r.get("policy_tier", "")),
        stock_status=str(r.get("stock_status", "")),
        coverage_months=float(r.get("coverage_months", 0.0)),
        avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
        unit_value_lkr=float(r.get("unit_value_lkr", 0.0)),
        rl_multiplier=float(r.get("rl_multiplier", 1.0)),
        rl_recommended_qty=float(r.get("rl_recommended_qty", 0.0)),
        rule_based_roq=float(r.get("rule_based_roq", r.get("roq", 0.0))),
        rl_flag=bool(r.get("rl_flag", False)),
    )


@router.get("", response_model=RLResponse)
def list_rl(
    flagged: bool | None = Query(None, description="Return only RL-flagged SKUs (>±50% deviation)"),
    abc: str | None = Query(None),
    tier: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> RLResponse:
    df = get_rl_policy().copy()

    if flagged is not None and "rl_flag" in df.columns:
        df = df[df["rl_flag"] == flagged]
    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]

    total = len(df)
    page  = df.iloc[offset : offset + limit]

    full   = get_rl_policy()
    active = full[full["avg_monthly_demand"] > 0].copy() if "avg_monthly_demand" in full.columns else full

    avg_mult = 1.0
    reduce   = increase = unchanged = 0
    avg_reduction = 0.0
    if "rl_multiplier" in active.columns and len(active):
        avg_mult    = round(float(active["rl_multiplier"].mean()), 4)
        reduce      = int((active["rl_multiplier"] < 1.0).sum())
        increase    = int((active["rl_multiplier"] > 1.0).sum())
        unchanged   = int((active["rl_multiplier"] == 1.0).sum())
        avg_reduction = round(float((1.0 - active["rl_multiplier"]).clip(lower=0).mean() * 100.0), 2)

    summary = RLSummary(
        scored_skus=int(full["rl_multiplier"].notna().sum()) if "rl_multiplier" in full.columns else 0,
        flagged_skus=int(full["rl_flag"].sum()) if "rl_flag" in full.columns else 0,
        avg_multiplier=avg_mult,
        avg_order_reduction_pct=avg_reduction,
        skus_reduce_order=reduce,
        skus_increase_order=increase,
        skus_unchanged=unchanged,
    )

    return RLResponse(
        summary=summary,
        rows=[_to_row(r) for _, r in page.iterrows()],
    )
