"""Bikes endpoints — Stages 1, 2, 3 (MCSI, Unit Sales Forecast, UIO Forecast)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Query

from src.api.deps import (
    get_mcsi_clean, get_mcsi_uio_summary, get_mcsi_vin_status, get_unit_sales_forecast,
    get_uio_based_demand, get_uio_external, get_uio_forecast,
    get_sales_targets, set_sales_targets,
)
from src.api.schemas import (
    BikesResponse, CrosstabResponse, CrosstabRow, DealerRow, DealersResponse,
    McsiEdaKpis, McsiEdaModelRow, McsiEdaProvinceRow, McsiEdaResponse, McsiEdaYearRow,
    McsiModelRow, McsiMonthlyPoint, McsiProvinceRow, McsiSummary,
    ModelForecastResponse, ModelForecastRow,
    SalesForecastRow, UIOComparisonResponse, UIODemandResponse, UIODemandRow,
    UIOExternalRow, UIOForecastRow, UIOSummaryRow,
)

router = APIRouter(prefix="/bikes", tags=["Bikes — Stages 1-3"])


def _safe_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return None


@router.get("", response_model=BikesResponse)
def get_bikes() -> BikesResponse:
    mcsi = get_mcsi_clean()
    usf  = get_unit_sales_forecast()
    uio  = get_uio_forecast()

    # ── MCSI summary ──────────────────────────────────────────────────────────
    if mcsi.empty:
        summary = McsiSummary(
            total_sold=0, date_from="", date_to="",
            by_model=[], by_province=[], monthly_trend=[],
        )
    else:
        total_sold = len(mcsi)
        date_from  = str(mcsi["Billing Date"].min())[:10]
        date_to    = str(mcsi["Billing Date"].max())[:10]

        model_vc = mcsi["Model"].value_counts()
        by_model = [
            McsiModelRow(model=m, count=int(c), pct=round(float(c) / total_sold * 100, 1))
            for m, c in model_vc.items()
        ]

        prov_vc    = mcsi["Province"].dropna().value_counts()
        by_province = [McsiProvinceRow(province=str(p), count=int(c)) for p, c in prov_vc.items()]

        has_revenue = "Net Sales" in mcsi.columns
        if has_revenue:
            monthly = (
                mcsi.groupby("Year_Month_str")
                .agg(sold=("Net Sales", "count"), revenue_lkr=("Net Sales", "sum"))
                .reset_index()
                .sort_values("Year_Month_str")
            )
        else:
            monthly = (
                mcsi.groupby("Year_Month_str").size()
                .reset_index(name="sold")
                .sort_values("Year_Month_str")
                .assign(revenue_lkr=0.0)
            )
        monthly_trend = [
            McsiMonthlyPoint(
                period=str(r["Year_Month_str"]),
                sold=int(r["sold"]),
                revenue_lkr=round(float(r["revenue_lkr"]), 0),
            )
            for _, r in monthly.iterrows()
        ]

        summary = McsiSummary(
            total_sold=total_sold, date_from=date_from, date_to=date_to,
            by_model=by_model, by_province=by_province, monthly_trend=monthly_trend,
        )

    # ── Unit sales forecast ───────────────────────────────────────────────────
    targets = get_sales_targets()
    yearly_target: float = float(targets.get("yearly_target", 0) or 0)
    monthly_overrides: dict[str, float] = {
        str(k): float(v) for k, v in targets.get("monthly_overrides", {}).items()
    }
    monthly_default = round(yearly_target / 12, 0) if yearly_target else None

    sales_rows: list[SalesForecastRow] = []
    if not usf.empty:
        for _, r in usf.iterrows():
            period = str(r.get("period", ""))
            parquet_target = _safe_float(r.get("target"))
            if period in monthly_overrides:
                target = monthly_overrides[period]
            elif monthly_default is not None:
                target = monthly_default
            else:
                target = parquet_target
            actual_or_fcst = _safe_float(r.get("actual")) or float(r.get("forecast", 0))
            target_gap = round(actual_or_fcst - target, 0) if target is not None else parquet_target
            sales_rows.append(SalesForecastRow(
                period=period,
                forecast=int(r.get("forecast", 0)),
                lower_80=int(r.get("lower_80", 0)),
                upper_80=int(r.get("upper_80", 0)),
                actual=_safe_float(r.get("actual")),
                is_forecast=bool(r.get("is_forecast", False)),
                target=target,
                target_gap=target_gap,
            ))

    # ── UIO forecast ──────────────────────────────────────────────────────────
    uio_rows: list[UIOForecastRow] = []
    if not uio.empty:
        for _, r in uio.iterrows():
            uio_rows.append(UIOForecastRow(
                period=str(r.get("period", "")),
                new_sales=int(r.get("new_sales", 0)),
                uio_total=int(r.get("uio_total", 0)),
                attrition=int(r.get("attrition", 0)),
                is_forecast=bool(r.get("is_forecast", False)),
                lower_80=_safe_float(r.get("lower_80")),
                upper_80=_safe_float(r.get("upper_80")),
            ))

    return BikesResponse(mcsi=summary, sales_forecast=sales_rows, uio_forecast=uio_rows)


@router.get("/forecast/by-model", response_model=ModelForecastResponse)
def get_forecast_by_model() -> ModelForecastResponse:
    mcsi = get_mcsi_clean()
    usf  = get_unit_sales_forecast()

    if mcsi.empty:
        return ModelForecastResponse(models=[], rows=[])

    models: list[str] = sorted(mcsi["Model"].dropna().unique().tolist())

    # Historical model mix — used to distribute total forecast across models
    total_by_model = mcsi.groupby("Model").size()
    grand = float(total_by_model.sum()) or 1.0
    model_mix: dict[str, float] = {m: float(total_by_model.get(m, 0)) / grand for m in models}

    rows: list[ModelForecastRow] = []

    # Actual periods: one row per (period, model) from MCSI
    actual_grp = (
        mcsi.groupby(["Year_Month_str", "Model"]).size()
        .reset_index(name="cnt")
        .sort_values("Year_Month_str")
    )
    for _, r in actual_grp.iterrows():
        rows.append(ModelForecastRow(
            period=str(r["Year_Month_str"]),
            model=str(r["Model"]),
            actual=int(r["cnt"]),
            forecast=int(r["cnt"]),
            is_forecast=False,
        ))

    # Forecast periods: distribute total forecast by historical mix
    if not usf.empty:
        fcast_df = usf[usf["is_forecast"].astype(bool)].sort_values("period")
        for _, r in fcast_df.iterrows():
            total_f = int(r.get("forecast", 0))
            for model in models:
                rows.append(ModelForecastRow(
                    period=str(r["period"]),
                    model=model,
                    actual=None,
                    forecast=round(total_f * model_mix.get(model, 0)),
                    is_forecast=True,
                ))

    return ModelForecastResponse(models=models, rows=rows)


@router.get("/dealers", response_model=DealersResponse)
def get_dealers(
    limit: int = Query(500, le=2000),
    year: int | None = Query(None, description="Filter by billing year, e.g. 2024"),
) -> DealersResponse:
    mcsi_full = get_mcsi_clean()

    if mcsi_full.empty:
        return DealersResponse(total_dealers=0, total_units=0, total_revenue_lkr=0.0, available_years=[], rows=[])

    available_years: list[int] = []
    if "Billing Date" in mcsi_full.columns:
        available_years = sorted(
            pd.to_datetime(mcsi_full["Billing Date"], errors="coerce").dt.year.dropna().unique().astype(int).tolist()
        )

    mcsi = mcsi_full
    if year is not None and "Billing Date" in mcsi.columns:
        mcsi = mcsi[pd.to_datetime(mcsi["Billing Date"], errors="coerce").dt.year == year]

    if mcsi.empty:
        return DealersResponse(total_dealers=0, total_units=0, total_revenue_lkr=0.0, available_years=available_years, rows=[])

    group_cols = [c for c in ["Province", "RM", "ASE", "Dealer", "Dealer Code"] if c in mcsi.columns]
    agg_kwargs: dict[str, object] = {"units_sold": ("Billing Date", "count")}
    if "Net Sales" in mcsi.columns:
        agg_kwargs["revenue_lkr"] = ("Net Sales", "sum")

    grp = (
        mcsi.groupby(group_cols, dropna=False)
        .agg(**agg_kwargs)  # type: ignore[arg-type]
        .reset_index()
    )
    if "revenue_lkr" in grp.columns:
        grp = grp.sort_values("revenue_lkr", ascending=False)
    else:
        grp = grp.sort_values("units_sold", ascending=False)
        grp["revenue_lkr"] = 0.0

    top = grp.head(limit)
    rows = [
        DealerRow(
            province=str(r.get("Province", "")),
            rm=str(r.get("RM", "")),
            ase=str(r.get("ASE", "")),
            dealer=str(r.get("Dealer", "")),
            dealer_code=str(r.get("Dealer Code", "")),
            units_sold=int(r["units_sold"]),
            revenue_lkr=round(float(r.get("revenue_lkr", 0) or 0), 0),
        )
        for _, r in top.iterrows()
    ]

    return DealersResponse(
        total_dealers=len(grp),
        total_units=int(grp["units_sold"].sum()),
        total_revenue_lkr=round(float(grp["revenue_lkr"].sum()), 0),
        available_years=available_years,
        rows=rows,
    )


@router.get("/crosstab", response_model=CrosstabResponse)
def get_crosstab(
    year: int | None = Query(None, description="Filter by billing year"),
) -> CrosstabResponse:
    mcsi = get_mcsi_clean()

    if mcsi.empty or "Province" not in mcsi.columns or "Model" not in mcsi.columns:
        return CrosstabResponse(models=[], rows=[])

    if year is not None and "Billing Date" in mcsi.columns:
        mcsi = mcsi[pd.to_datetime(mcsi["Billing Date"], errors="coerce").dt.year == year]

    if mcsi.empty:
        return CrosstabResponse(models=[], rows=[])

    pivot = pd.crosstab(mcsi["Province"], mcsi["Model"]).reset_index()
    models = [str(c) for c in pivot.columns if c != "Province"]

    rows = [
        CrosstabRow(province=str(r["Province"]), totals={m: int(r[m]) for m in models})
        for _, r in pivot.iterrows()
    ]
    return CrosstabResponse(models=models, rows=rows)


@router.get("/mcsi-eda", response_model=McsiEdaResponse)
def get_mcsi_eda() -> McsiEdaResponse:
    """Full Stage-1 MCSI EDA: KPIs, return rate, year/model/province breakdowns."""
    mcsi = get_mcsi_clean()
    vin_status = get_mcsi_vin_status()

    if mcsi.empty:
        kpis = McsiEdaKpis(
            total_vins=0, sold=0, returned=0, return_rate_pct=0.0,
            total_revenue_lkr=0.0, avg_monthly_units=0.0, avg_revenue_per_unit=0.0,
            active_provinces=0, active_dealers=0, models_sold=0,
            date_from="", date_to="", months_of_data=0,
        )
        return McsiEdaResponse(kpis=kpis, monthly_trend=[], by_year=[], by_model=[], by_province=[])

    has_revenue = "Net Sales" in mcsi.columns
    revenue_col = "Net Sales" if has_revenue else None

    total_sold = mcsi["VIN"].nunique()
    total_returned = int((vin_status["Status"] == "Returned").sum()) if not vin_status.empty else 0
    total_vins = total_sold + total_returned
    return_rate = round(total_returned / total_vins * 100, 2) if total_vins else 0.0
    total_revenue = float(mcsi[revenue_col].sum()) if revenue_col else 0.0
    months_active = mcsi["Year_Month_str"].nunique()
    avg_monthly = round(total_sold / months_active, 1) if months_active else 0.0
    avg_rev_per_unit = round(total_revenue / total_sold, 0) if total_sold and revenue_col else 0.0

    # KPIs
    dealer_col = next((c for c in ["Dealer Code", "Dealer_Code"] if c in mcsi.columns), None)
    kpis = McsiEdaKpis(
        total_vins=total_vins,
        sold=total_sold,
        returned=total_returned,
        return_rate_pct=return_rate,
        total_revenue_lkr=round(total_revenue, 0),
        avg_monthly_units=avg_monthly,
        avg_revenue_per_unit=avg_rev_per_unit,
        active_provinces=int(mcsi["Province"].nunique()) if "Province" in mcsi.columns else 0,
        active_dealers=int(mcsi[dealer_col].nunique()) if dealer_col else 0,
        models_sold=int(mcsi["Model"].nunique()) if "Model" in mcsi.columns else 0,
        date_from=str(mcsi["Billing Date"].min())[:10],
        date_to=str(mcsi["Billing Date"].max())[:10],
        months_of_data=months_active,
    )

    # Monthly trend
    agg_kwargs: dict[str, object] = {"sold": ("Billing Date", "count")}
    if revenue_col:
        agg_kwargs["revenue_lkr"] = (revenue_col, "sum")
    monthly = (
        mcsi.groupby("Year_Month_str")
        .agg(**agg_kwargs)  # type: ignore[arg-type]
        .reset_index()
        .sort_values("Year_Month_str")
    )
    if "revenue_lkr" not in monthly.columns:
        monthly["revenue_lkr"] = 0.0
    monthly_trend = [
        McsiMonthlyPoint(period=str(r["Year_Month_str"]), sold=int(r["sold"]), revenue_lkr=round(float(r["revenue_lkr"]), 0))
        for _, r in monthly.iterrows()
    ]

    # By year
    year_agg_kw: dict[str, object] = {"units_sold": ("VIN", "nunique")}
    if revenue_col:
        year_agg_kw["revenue_lkr"] = (revenue_col, "sum")
    year_grp = (
        mcsi.groupby("Year")
        .agg(**year_agg_kw)  # type: ignore[arg-type]
        .reset_index()
        .sort_values("Year")
    )
    if "revenue_lkr" not in year_grp.columns:
        year_grp["revenue_lkr"] = 0.0
    by_year: list[McsiEdaYearRow] = []
    for _, r in year_grp.iterrows():
        mo_count = int(mcsi[mcsi["Year"] == r["Year"]]["Month_Num"].nunique()) or 1
        by_year.append(McsiEdaYearRow(
            year=int(r["Year"]),
            units_sold=int(r["units_sold"]),
            revenue_lkr=round(float(r["revenue_lkr"]), 0),
            avg_monthly=round(float(r["units_sold"]) / mo_count, 1),
        ))

    # By model
    model_agg_kw: dict[str, object] = {"units_sold": ("VIN", "nunique")}
    if revenue_col:
        model_agg_kw["revenue_lkr"] = (revenue_col, "sum")
    model_grp = (
        mcsi.groupby("Model", dropna=False)
        .agg(**model_agg_kw)  # type: ignore[arg-type]
        .reset_index()
        .sort_values("units_sold", ascending=False)
    )
    if "revenue_lkr" not in model_grp.columns:
        model_grp["revenue_lkr"] = 0.0
    by_model: list[McsiEdaModelRow] = []
    for _, r in model_grp.iterrows():
        u = int(r["units_sold"])
        rev = float(r.get("revenue_lkr", 0) or 0)
        by_model.append(McsiEdaModelRow(
            model=str(r["Model"]),
            units_sold=u,
            revenue_lkr=round(rev, 0),
            share_pct=round(u / total_sold * 100, 1) if total_sold else 0.0,
            avg_revenue_per_unit=round(rev / u, 0) if u else 0.0,
        ))

    # By province
    prov_agg_kw: dict[str, object] = {"units_sold": ("VIN", "nunique")}
    if revenue_col:
        prov_agg_kw["revenue_lkr"] = (revenue_col, "sum")
    if dealer_col:
        prov_agg_kw["dealer_count"] = (dealer_col, "nunique")
    prov_grp = (
        mcsi.groupby("Province", dropna=False)
        .agg(**prov_agg_kw)  # type: ignore[arg-type]
        .reset_index()
        .sort_values("units_sold", ascending=False)
    )
    if "revenue_lkr" not in prov_grp.columns:
        prov_grp["revenue_lkr"] = 0.0
    if "dealer_count" not in prov_grp.columns:
        prov_grp["dealer_count"] = 0
    by_province: list[McsiEdaProvinceRow] = []
    for _, r in prov_grp.iterrows():
        u = int(r["units_sold"])
        by_province.append(McsiEdaProvinceRow(
            province=str(r["Province"]),
            units_sold=u,
            revenue_lkr=round(float(r.get("revenue_lkr", 0) or 0), 0),
            share_pct=round(u / total_sold * 100, 1) if total_sold else 0.0,
            dealer_count=int(r.get("dealer_count", 0) or 0),
        ))

    return McsiEdaResponse(
        kpis=kpis,
        monthly_trend=monthly_trend,
        by_year=by_year,
        by_model=by_model,
        by_province=by_province,
    )


@router.get("/uio", response_model=UIOComparisonResponse)
def get_uio_comparison() -> UIOComparisonResponse:
    """UIO from two sources:
    - external: UIO.xlsx cohort-survival model (full historical fleet, all model generations)
    - mcsi: MCSI.xlsx VIN-verified sold bikes (recent period only)
    """
    ext  = get_uio_external()
    msci = get_mcsi_uio_summary()

    external_rows: list[UIOExternalRow] = []
    if not ext.empty:
        for _, r in ext.iterrows():
            external_rows.append(UIOExternalRow(
                model=str(r["model"]),
                total_sales_units=int(r["total_sales_units"]),
                uio=int(r["uio"]),
            ))

    mcsi_rows: list[UIOSummaryRow] = []
    if not msci.empty:
        for _, r in msci.iterrows():
            mcsi_rows.append(UIOSummaryRow(
                model=str(r["Model"]),
                uio=int(r["UIO"]),
                uio_pct=round(float(r["UIO_pct"]), 1),
            ))

    return UIOComparisonResponse(external=external_rows, mcsi=mcsi_rows)


@router.get("/targets")
def read_targets() -> dict:
    """Return current yearly + monthly sales targets."""
    return get_sales_targets()


@router.post("/targets")
def write_targets(body: dict = Body(...)) -> dict:
    """Persist yearly target and per-month overrides.

    Body: { "yearly_target": 40000, "monthly_overrides": {"2024-01": 3200, ...} }
    """
    set_sales_targets(body)
    return {"ok": True}


@router.get("/uio-demand", response_model=UIODemandResponse)
def get_uio_demand(
    limit: int = Query(500, le=5000),
    min_demand: float = Query(0.0, ge=0.0),
) -> UIODemandResponse:
    """Stage 6.4 — UIO-driven spare-parts demand estimates.

    Returns estimated monthly demand per SKU based on fleet size and
    historical replacement frequency.
    """
    df = get_uio_based_demand()

    if df.empty:
        return UIODemandResponse(
            total_parts=0, parts_with_demand=0, projected_uio=0.0,
            supply_pct=0.0, lead_time_months=3,
            sum_uio_demand_monthly=0.0, sum_uio_demand_leadtime=0.0,
            rows=[],
        )

    total_parts      = len(df)
    parts_with_demand = int((df["uio_demand_monthly"] > 0).sum())
    projected_uio    = float(df["projected_uio"].iloc[0]) if "projected_uio" in df.columns else 0.0
    supply_pct       = float(df["supply_pct_applied"].iloc[0]) if "supply_pct_applied" in df.columns else 0.0
    sum_monthly      = round(float(df["uio_demand_monthly"].sum()), 2)
    sum_lt           = round(float(df["uio_demand_leadtime"].sum()), 2)

    display = df[df["uio_demand_monthly"] >= min_demand].head(limit)

    rows: list[UIODemandRow] = []
    for _, r in display.iterrows():
        rows.append(UIODemandRow(
            material_9=str(r.get("material_9", "")),
            description=str(r.get("description", "") or ""),
            compatible_models=str(r.get("compatible_models", "") or "") or None,
            model_count=int(r.get("model_count", 0) or 0),
            avg_monthly=round(float(r.get("avg_monthly", 0) or 0), 4),
            hist_months=int(r.get("hist_months", 0) or 0),
            model_uio_total=round(float(r.get("model_uio_total", 0) or 0), 1),
            replacement_freq_per_uio=round(float(r.get("replacement_freq_per_uio", 0) or 0), 6),
            projected_uio=round(float(r.get("projected_uio", 0) or 0), 0),
            uio_demand_monthly=round(float(r.get("uio_demand_monthly", 0) or 0), 4),
            uio_demand_leadtime=round(float(r.get("uio_demand_leadtime", 0) or 0), 4),
            supply_pct_applied=round(float(r.get("supply_pct_applied", 0) or 0), 2),
        ))

    return UIODemandResponse(
        total_parts=total_parts,
        parts_with_demand=parts_with_demand,
        projected_uio=projected_uio,
        supply_pct=supply_pct,
        lead_time_months=3,
        sum_uio_demand_monthly=sum_monthly,
        sum_uio_demand_leadtime=sum_lt,
        rows=rows,
    )
