"""Demand forecast endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.api.deps import get_forecast, get_monthly_demand, load_module_parquet
from src.api.schemas import (
    ForecastResponse,
    ForecastRow,
    M2FusedDemandResponse,
    M2FusedDemandRow,
    M2OrdersForecastResponse,
    M2OrdersForecastRow,
    M2UIODemandModuleResponse,
    M2UIODemandModuleRow,
    MonthlyDemandPoint,
)

router = APIRouter(prefix="/forecast", tags=["Forecast"])


def _to_row(r: pd.Series) -> ForecastRow:
    return ForecastRow(
        material_9=str(r.get("material_9", "")),
        description=str(r.get("description", "")),
        abc=str(r.get("abc", "")),
        xyz=str(r.get("xyz", "")),
        fsn=str(r.get("fsn", "")),
        policy_tier=str(r.get("policy_tier", "")),
        method=str(r.get("method", "")),
        forecast_m1=float(r.get("forecast_m1", 0.0)),
        forecast_m2=float(r.get("forecast_m2", 0.0)),
        forecast_m3=float(r.get("forecast_m3", 0.0)),
        forecast_lt=float(r.get("forecast_lt", 0.0)),
        avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
        demand_std_monthly=float(r.get("demand_std_monthly", 0.0)),
        demand_std_lt=float(r.get("demand_std_lt", 0.0)),
        cv_hist=float(r.get("cv_hist", r.get("cv", 0.0))),
        total_issue_value_lkr=float(r.get("total_issue_value_lkr", 0.0)),
        active_months=int(r.get("active_months", 0)),
    )


@router.get("", response_model=ForecastResponse)
def list_forecast(
    method: str | None = Query(None, description="Filter by forecast method (Zero, HistoricAverage, Croston, AutoETS, LightGBM, NHITS)"),
    abc: str | None = Query(None),
    tier: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> ForecastResponse:
    df = get_forecast().copy()

    if method and "method" in df.columns:
        df = df[df["method"] == method]
    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]

    total = len(df)
    page  = df.iloc[offset : offset + limit]
    full  = get_forecast()

    return ForecastResponse(
        total=total,
        rows=[_to_row(r) for _, r in page.iterrows()],
        method_counts={k: int(v) for k, v in full["method"].value_counts().items()} if "method" in full.columns else {},
    )


@router.get("/fused-demand", response_model=M2FusedDemandResponse)
def list_fused_demand(
    part_no: str | None = Query(None, description="Filter by part number"),
    method: str | None = Query(None, description="Filter by fusion method"),
    demand_class: str | None = Query(None, description="Filter by demand class"),
    limit: int = Query(100, le=5000),
    offset: int = Query(0, ge=0),
) -> M2FusedDemandResponse:
    """Module 2 — fused demand from m2_fused_demand.parquet.

    Business meaning: per-SKU per-month demand after fusing signals from
    historical orders, sales, and UIO-driven estimates (Module 2 output).
    """
    df = load_module_parquet("m2_fused_demand.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 2 --save")

    if part_no:
        df = df[df["part_no"] == part_no]
    if method and "method" in df.columns:
        df = df[df["method"] == method]
    if demand_class and "demand_class" in df.columns:
        df = df[df["demand_class"] == demand_class]

    full = df  # for aggregates
    total = len(full)
    page = full.iloc[offset: offset + limit]

    method_counts: dict[str, int] = (
        {k: int(v) for k, v in full["method"].value_counts().items()}
        if "method" in full.columns else {}
    )
    dc_counts: dict[str, int] = (
        {k: int(v) for k, v in full["demand_class"].value_counts().items()}
        if "demand_class" in full.columns else {}
    )

    rows = [
        M2FusedDemandRow(
            part_no=str(r["part_no"]),
            month=str(r["month"]),
            demand_qty=float(r.get("demand_qty", 0) or 0),
            method=str(r.get("method", "")),
            cv=float(r.get("cv", 0) or 0),
            demand_class=str(r.get("demand_class", "")),
        )
        for _, r in page.iterrows()
    ]
    return M2FusedDemandResponse(
        total=total, offset=offset, limit=limit,
        rows=rows, method_counts=method_counts, demand_class_counts=dc_counts,
    )


@router.get("/orders-forecast", response_model=M2OrdersForecastResponse)
def list_orders_forecast(
    part_no: str | None = Query(None, description="Filter by part number"),
    source: str | None = Query(None, description="Filter by source"),
    limit: int = Query(100, le=5000),
    offset: int = Query(0, ge=0),
) -> M2OrdersForecastResponse:
    """Module 2 — orders-based demand forecast from m2_orders_forecast.parquet.

    Business meaning: per-SKU per-month forecast derived from purchase order history
    (dealer orders to Yamaha). Used as one signal in the Module 2 fusion.
    """
    df = load_module_parquet("m2_orders_forecast.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 2 --save")

    if part_no:
        df = df[df["part_no"] == part_no]
    if source and "source" in df.columns:
        df = df[df["source"] == source]

    full = df
    total = len(full)
    page = full.iloc[offset: offset + limit]

    source_counts: dict[str, int] = (
        {k: int(v) for k, v in full["source"].value_counts().items()}
        if "source" in full.columns else {}
    )

    rows = [
        M2OrdersForecastRow(
            part_no=str(r["part_no"]),
            month=str(r["month"]),
            forecast_qty=float(r.get("forecast_qty", 0) or 0),
            source=str(r.get("source", "")),
        )
        for _, r in page.iterrows()
    ]
    return M2OrdersForecastResponse(
        total=total, offset=offset, limit=limit, rows=rows, source_counts=source_counts,
    )


@router.get("/uio-demand", response_model=M2UIODemandModuleResponse)
def list_m2_uio_demand(
    part_no: str | None = Query(None, description="Filter by part number"),
    limit: int = Query(100, le=5000),
    offset: int = Query(0, ge=0),
) -> M2UIODemandModuleResponse:
    """Module 2 — UIO-driven demand from m2_uio_demand.parquet.

    Business meaning: per-SKU per-month demand estimates derived from the UIO
    fleet size (Module 1 output) and historical replacement frequency rates.
    """
    df = load_module_parquet("m2_uio_demand.parquet")
    if df is None:
        raise HTTPException(status_code=503, detail="Run: python -m scripts.run_module 2 --save")

    if part_no:
        df = df[df["part_no"] == part_no]

    total = len(df)
    page = df.iloc[offset: offset + limit]

    rows = [
        M2UIODemandModuleRow(
            part_no=str(r["part_no"]),
            month=str(r["month"]),
            uio_demand_qty=float(r.get("uio_demand_qty", 0) or 0),
            source=str(r.get("source", "")),
        )
        for _, r in page.iterrows()
    ]
    return M2UIODemandModuleResponse(total=total, offset=offset, limit=limit, rows=rows)


@router.get("/trend", response_model=list[MonthlyDemandPoint])
def monthly_trend(
    sku: str | None = Query(None, description="Filter by material_9 — omit for aggregate across all SKUs"),
) -> list[MonthlyDemandPoint]:
    df = get_monthly_demand()
    if df.empty:
        return []
    if sku and "material_9" in df.columns:
        df = df[df["material_9"] == sku]

    group_cols = ["year_month_str"]
    agg: dict[str, str] = {
        "issue_qty":        "sum",
        "issue_value_lkr":  "sum",
        "return_qty":       "sum",
        "net_demand":       "sum",
    }
    # only aggregate columns that exist
    agg = {k: v for k, v in agg.items() if k in df.columns}

    result = (
        df.groupby(group_cols)
        .agg(agg)
        .reset_index()
        .sort_values("year_month_str")
    )

    return [
        MonthlyDemandPoint(
            year_month_str=str(row["year_month_str"]),
            issue_qty=float(row.get("issue_qty", 0.0)),
            issue_value_lkr=float(row.get("issue_value_lkr", 0.0)),
            return_qty=float(row.get("return_qty", 0.0)),
            net_demand=float(row.get("net_demand", 0.0)),
        )
        for _, row in result.iterrows()
    ]
