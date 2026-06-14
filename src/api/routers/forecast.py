"""Demand forecast endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from src.api.deps import get_forecast, get_monthly_demand
from src.api.schemas import ForecastResponse, ForecastRow, MonthlyDemandPoint

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
