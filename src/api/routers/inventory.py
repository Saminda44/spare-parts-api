"""Stock tracker / inventory status endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from src.api.deps import get_policy, get_stock_location_analysis, get_stock_tracker
from src.api.schemas import (
    AtRiskRow,
    CoverageHistogramBucket,
    ExcessRow,
    InventoryResponse,
    InventoryRow,
    LocationRow,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


def _fmt_date(val: object) -> str | None:
    if pd.isna(val):
        return None
    try:
        return pd.Timestamp(val).strftime("%Y-%m-%d")
    except Exception:
        return str(val)


def _to_row(r: pd.Series) -> InventoryRow:
    return InventoryRow(
        material_9=str(r.get("material_9", "")),
        description=str(r.get("description", "")),
        abc=str(r.get("abc", "")),
        xyz=str(r.get("xyz", "")),
        fsn=str(r.get("fsn", "")),
        policy_tier=str(r.get("policy_tier", "")),
        stock_on_hand=float(r.get("stock_on_hand", 0.0)),
        stock_value_lkr=float(r.get("stock_value_lkr", 0.0)),
        coverage_months=float(r.get("coverage_months", 0.0)),
        days_of_stock=float(r.get("days_of_stock", 0.0)),
        stock_status=str(r.get("stock_status", "")),
        avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
        forecast_lt=float(r.get("forecast_lt", 0.0)),
        method=str(r.get("method", "")),
        total_receipts=float(r.get("total_receipts", 0.0)),
        total_issues=float(r.get("total_issues", 0.0)),
        total_returns=float(r.get("total_returns", 0.0)),
        last_movement_date=_fmt_date(r.get("last_movement_date")),
    )


@router.get("", response_model=InventoryResponse)
def list_inventory(
    status: str | None = Query(None, description="stockout / critical / low / ok / excess"),
    abc: str | None = Query(None),
    tier: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> InventoryResponse:
    df = get_stock_tracker().copy()

    if status and "stock_status" in df.columns:
        df = df[df["stock_status"] == status]
    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]

    total = len(df)
    page = df.iloc[offset : offset + limit]

    full = get_stock_tracker()
    total_val = float(full["stock_value_lkr"].sum()) if "stock_value_lkr" in full.columns else 0.0
    excess_val = (
        float(full.loc[full["stock_status"] == "excess", "stock_value_lkr"].sum())
        if "stock_value_lkr" in full.columns and "stock_status" in full.columns
        else 0.0
    )

    return InventoryResponse(
        total=total,
        rows=[_to_row(r) for _, r in page.iterrows()],
        status_counts={k: int(v) for k, v in full["stock_status"].value_counts().items()}
        if "stock_status" in full.columns
        else {},
        total_value_lkr=round(total_val, 0),
        excess_value_lkr=round(excess_val, 0),
    )


@router.get("/at-risk", response_model=list[AtRiskRow])
def at_risk(
    limit: int = Query(50, le=500),
) -> list[AtRiskRow]:
    """Top SKUs at immediate stockout/critical risk — sorted by net_requirement desc."""
    pol = get_policy()
    if pol.empty:
        return []

    risk = pol[pol["order_urgency"].isin(["immediate", "soon"])].copy()
    risk = risk.sort_values("net_requirement", ascending=False).head(limit)

    return [
        AtRiskRow(
            material_9=str(r.get("material_9", "")),
            description=str(r.get("description", "")),
            abc=str(r.get("abc", "")),
            policy_tier=str(r.get("policy_tier", "")),
            stock_on_hand=float(r.get("stock_on_hand", 0.0)),
            stock_status=str(r.get("stock_status", "")),
            coverage_months=float(r.get("coverage_months", 0.0)),
            net_requirement=float(r.get("net_requirement", 0.0)),
            order_urgency=str(r.get("order_urgency", "")),
            unit_value_lkr=float(r.get("unit_value_lkr", 0.0)),
        )
        for _, r in risk.iterrows()
    ]


@router.get("/excess", response_model=list[ExcessRow])
def excess_stock(
    limit: int = Query(100, le=1000),
) -> list[ExcessRow]:
    """SKUs with excess stock (>6 months coverage) — sorted by stock_value_lkr desc."""
    stk = get_stock_tracker()
    if stk.empty:
        return []

    excess = stk[stk["stock_status"] == "excess"].copy()
    excess = excess.sort_values("stock_value_lkr", ascending=False).head(limit)

    return [
        ExcessRow(
            material_9=str(r.get("material_9", "")),
            description=str(r.get("description", "")),
            abc=str(r.get("abc", "")),
            policy_tier=str(r.get("policy_tier", "")),
            stock_on_hand=float(r.get("stock_on_hand", 0.0)),
            stock_value_lkr=float(r.get("stock_value_lkr", 0.0)),
            coverage_months=float(r.get("coverage_months", 0.0)),
            avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
        )
        for _, r in excess.iterrows()
    ]


@router.get("/coverage-histogram", response_model=list[CoverageHistogramBucket])
def coverage_histogram(bins: int = Query(40, ge=5, le=100)) -> list[CoverageHistogramBucket]:
    import numpy as np

    df = get_stock_tracker()
    if df.empty or "coverage_months" not in df.columns:
        return []

    active = df[(df["avg_monthly_demand"] > 0) & (df["coverage_months"] < 24)][
        "coverage_months"
    ].dropna()

    if active.empty:
        return []

    counts, edges = np.histogram(active, bins=bins)
    return [
        CoverageHistogramBucket(
            bin_start=round(float(edges[i]), 2),
            bin_end=round(float(edges[i + 1]), 2),
            count=int(counts[i]),
        )
        for i in range(len(counts))
    ]


@router.get("/stock-by-location", response_model=list[LocationRow])
def stock_by_location() -> list[LocationRow]:
    """Stock qty, value and SKU count by SAP storage location description.

    Covers all location types in current stock.xlsx — including those excluded
    from the active inventory calculation (Damage, GR-unavailable etc.). The
    is_excluded field marks which locations are filtered out.
    """
    df = get_stock_location_analysis()
    if df.empty:
        return []
    return [
        LocationRow(
            description=str(r.get("description", "")),
            qty=float(r.get("qty", 0.0)),
            value_lkr=float(r.get("value_lkr", 0.0)),
            sku_count=int(r.get("sku_count", 0)),
            is_excluded=bool(r.get("is_excluded", False)),
        )
        for _, r in df.iterrows()
    ]
