"""Stock tracker / inventory status endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.api.deps import get_policy, get_stock_tracker, load_module_parquet
from src.api.schemas import (
    AtRiskRow,
    CoverageHistogramBucket,
    ExcessRow,
    InventoryResponse,
    InventoryRow,
    M3InvPositionResponse,
    M3InvPositionRow,
    M4PlanningResponse,
    M4PlanningRow,
    M4SafetyStockResponse,
    M4SafetyStockRow,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


def _fmt_date(val) -> str | None:
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
    page  = df.iloc[offset : offset + limit]

    full = get_stock_tracker()
    total_val  = float(full["stock_value_lkr"].sum()) if "stock_value_lkr" in full.columns else 0.0
    excess_val = float(
        full.loc[full["stock_status"] == "excess", "stock_value_lkr"].sum()
    ) if "stock_value_lkr" in full.columns and "stock_status" in full.columns else 0.0

    return InventoryResponse(
        total=total,
        rows=[_to_row(r) for _, r in page.iterrows()],
        status_counts={k: int(v) for k, v in full["stock_status"].value_counts().items()} if "stock_status" in full.columns else {},
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


@router.get("/position", response_model=M3InvPositionResponse)
def get_inventory_position(
    part_no: str | None = Query(None, description="Filter by part number"),
    limit: int = Query(500, le=50000),
    offset: int = Query(0, ge=0),
) -> M3InvPositionResponse:
    """Module 3 — inventory position from m3_inventory_position.parquet.

    Business meaning: net inventory position per SKU = stock_qty + pipeline_qty
    - backorder_qty. Drives Module 4 reorder calculations.
    """
    df = load_module_parquet("m3_inventory_position.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 3 --save")

    if part_no:
        df = df[df["part_no"] == part_no]

    total_stock = float(df["stock_qty"].sum()) if "stock_qty" in df.columns else 0.0
    total_pipeline = float(df["pipeline_qty"].sum()) if "pipeline_qty" in df.columns else 0.0
    total_net = float(df["net_position"].sum()) if "net_position" in df.columns else 0.0

    total = len(df)
    page = df.iloc[offset: offset + limit]

    rows = [
        M3InvPositionRow(
            part_no=str(r["part_no"]),
            stock_qty=float(r.get("stock_qty", 0) or 0),
            pipeline_qty=float(r.get("pipeline_qty", 0) or 0),
            backorder_qty=float(r.get("backorder_qty", 0) or 0),
            net_position=float(r.get("net_position", 0) or 0),
        )
        for _, r in page.iterrows()
    ]
    return M3InvPositionResponse(
        total=total, offset=offset, limit=limit, rows=rows,
        total_stock_qty=round(total_stock, 2),
        total_pipeline_qty=round(total_pipeline, 2),
        total_net_position=round(total_net, 2),
    )


@router.get("/planning", response_model=M4PlanningResponse)
def get_planning_table(
    part_no: str | None = Query(None, description="Filter by part number"),
    demand_class: str | None = Query(None, description="Filter by demand class"),
    signal_to_reorder: bool | None = Query(None, description="Filter by reorder signal"),
    limit: int = Query(500, le=50000),
    offset: int = Query(0, ge=0),
) -> M4PlanningResponse:
    """Module 4 — planning table from m4_planning_table.parquet.

    Business meaning: complete inventory planning table with ROL, safety stock,
    reorder signal, and urgency score per SKU (Module 4 output).
    """
    df = load_module_parquet("m4_planning_table.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 4 --save")

    if part_no:
        df = df[df["part_no"] == part_no]
    if demand_class and "demand_class" in df.columns:
        df = df[df["demand_class"] == demand_class]
    if signal_to_reorder is not None and "signal_to_reorder" in df.columns:
        df = df[df["signal_to_reorder"].astype(bool) == signal_to_reorder]

    signal_count = int(df["signal_to_reorder"].astype(bool).sum()) if "signal_to_reorder" in df.columns else 0
    dc_counts: dict[str, int] = (
        {k: int(v) for k, v in df["demand_class"].value_counts().items()}
        if "demand_class" in df.columns else {}
    )

    total = len(df)
    page = df.iloc[offset: offset + limit]

    rows = [
        M4PlanningRow(
            part_no=str(r["part_no"]),
            demand_class=str(r.get("demand_class", "")),
            mean_monthly_demand=float(r.get("mean_monthly_demand", 0) or 0),
            lead_time_demand=float(r.get("lead_time_demand", 0) or 0),
            review_demand=float(r.get("review_demand", 0) or 0),
            horizon_demand=float(r.get("horizon_demand", 0) or 0),
            sigma_demand=float(r.get("sigma_demand", 0) or 0),
            service_level=float(r.get("service_level", 0) or 0),
            z_score=float(r.get("z_score", 0) or 0),
            ss_method=str(r.get("ss_method", "")),
            safety_stock=float(r.get("safety_stock", 0) or 0),
            rol=float(r.get("rol", 0) or 0),
            stock_qty=float(r.get("stock_qty", 0) or 0),
            pipeline_qty=float(r.get("pipeline_qty", 0) or 0),
            backorder_qty=float(r.get("backorder_qty", 0) or 0),
            net_position=float(r.get("net_position", 0) or 0),
            signal_to_reorder=bool(r.get("signal_to_reorder", False)),
            urgency_score=float(r.get("urgency_score", 0) or 0),
        )
        for _, r in page.iterrows()
    ]
    return M4PlanningResponse(
        total=total, offset=offset, limit=limit,
        rows=rows, signal_count=signal_count, demand_class_counts=dc_counts,
    )


@router.get("/safety-stock", response_model=M4SafetyStockResponse)
def get_safety_stock(
    part_no: str | None = Query(None, description="Filter by part number"),
    demand_class: str | None = Query(None, description="Filter by demand class"),
    limit: int = Query(500, le=50000),
    offset: int = Query(0, ge=0),
) -> M4SafetyStockResponse:
    """Module 4 — safety stock table from m4_safety_stock.parquet.

    Business meaning: per-SKU safety stock requirements derived from demand
    variability and target service levels set by ABC classification.
    """
    df = load_module_parquet("m4_safety_stock.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 4 --save")

    if part_no:
        df = df[df["part_no"] == part_no]
    if demand_class and "demand_class" in df.columns:
        df = df[df["demand_class"] == demand_class]

    dc_counts: dict[str, int] = (
        {k: int(v) for k, v in df["demand_class"].value_counts().items()}
        if "demand_class" in df.columns else {}
    )

    total = len(df)
    page = df.iloc[offset: offset + limit]

    rows = [
        M4SafetyStockRow(
            part_no=str(r["part_no"]),
            safety_stock=float(r.get("safety_stock", 0) or 0),
            service_level=float(r.get("service_level", 0) or 0),
            z_score=float(r.get("z_score", 0) or 0),
            sigma_demand=float(r.get("sigma_demand", 0) or 0),
            demand_class=str(r.get("demand_class", "")),
            ss_method=str(r.get("ss_method", "")),
        )
        for _, r in page.iterrows()
    ]
    return M4SafetyStockResponse(
        total=total, offset=offset, limit=limit, rows=rows, demand_class_counts=dc_counts,
    )


@router.get("/coverage-histogram", response_model=list[CoverageHistogramBucket])
def coverage_histogram(bins: int = Query(40, ge=5, le=100)) -> list[CoverageHistogramBucket]:
    import numpy as np

    df = get_stock_tracker()
    if df.empty or "coverage_months" not in df.columns:
        return []

    active = df[
        (df["avg_monthly_demand"] > 0) &
        (df["coverage_months"] < 24)
    ]["coverage_months"].dropna()

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
