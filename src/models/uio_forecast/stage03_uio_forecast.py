"""Stage 3: Units in Operation (UIO) Forecast — stock-flow projection.

The UIO fleet drives spare-parts demand (Stage 6.4 consumes this output).
Model: stock-flow with configurable annual attrition.

  UIO(t) = UIO(t-1) + new_sales(t) − attrition(t)
  attrition(t) = UIO(t-1) × monthly_attrition_rate
  monthly_attrition_rate = 1 − (1 − annual_attrition_rate)^(1/12)

New sales are distributed across models using the historical model-mix from
Stage 1 (sold VIN proportions).  Attrition is applied uniformly per model.

Inputs (from earlier stages):
  data/interim/mcsi_clean.parquet        — sold VINs (Status == "Sold")
  data/interim/mcsi_uio_summary.parquet  — current UIO by model (Stage 2)
  data/interim/unit_sales_forecast.parquet — monthly sales forecast (Stage 2)

Outputs:
  data/interim/uio_forecast.parquet
  data/outputs/stage03_uio_forecast.xlsx
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
import xlsxwriter
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────
_CLEAN_PARQUET    = DATA_INTERIM / "mcsi_clean.parquet"
_UIO_PARQUET      = DATA_INTERIM / "mcsi_uio_summary.parquet"
_FC_PARQUET       = DATA_INTERIM / "unit_sales_forecast.parquet"
_OUT_PARQUET      = DATA_INTERIM / "uio_forecast.parquet"
_OUTPUT_EXCEL     = DATA_OUTPUTS / "stage03_uio_forecast.xlsx"

# ── Defaults ───────────────────────────────────────────────────
DEFAULT_ANNUAL_ATTRITION = 0.05   # 5 % of fleet leaves service each year
DEFAULT_HORIZON_MONTHS   = 36     # 3-year forward projection


# ══════════════════════════════════════════════════════════════
# 1. Data loading
# ══════════════════════════════════════════════════════════════

def load_current_uio() -> pd.DataFrame:
    """Return current UIO by model: DataFrame[Model, UIO, UIO_pct]."""
    df = pd.read_parquet(_UIO_PARQUET)
    logger.info(f"Current UIO loaded: {df['UIO'].sum():,} units across {len(df)} models")
    return df


def load_sales_forecast() -> pd.DataFrame:
    """Return Stage 2 forecast rows (is_forecast == True): DataFrame[period, forecast]."""
    fc = pd.read_parquet(_FC_PARQUET)
    fc_only = fc[fc["is_forecast"]].copy()
    logger.info(f"Sales forecast loaded: {len(fc_only)} future months")
    return fc_only[["ds", "period", "forecast", "lower_80", "upper_80"]]


def load_historical_monthly_sales() -> pd.DataFrame:
    """Return actual monthly new VIN additions from MCSI (actuals only).

    Returns DataFrame[period, new_sales] sorted ascending, excluding partial
    months (2025-04) and Company province.
    """
    clean = pd.read_parquet(_CLEAN_PARQUET)
    sold  = clean[(clean["Status"] == "Sold") & (clean["Province"] != "Company")]
    monthly = (
        sold.groupby("Year_Month_str")["VIN"]
        .nunique()
        .reset_index()
        .rename(columns={"Year_Month_str": "period", "VIN": "new_sales"})
        .sort_values("period")
    )
    # Drop partial first month
    monthly = monthly[monthly["period"] != "2025-04"].reset_index(drop=True)
    return monthly


def get_model_mix(current_uio: pd.DataFrame) -> dict[str, float]:
    """Derive model mix shares from current UIO.

    Returns {model_name: share} where shares sum to 1.0.
    """
    total = current_uio["UIO"].sum()
    if total == 0:
        n = len(current_uio)
        return {row["Model"]: 1 / n for _, row in current_uio.iterrows()}
    return {row["Model"]: row["UIO"] / total for _, row in current_uio.iterrows()}


# ══════════════════════════════════════════════════════════════
# 2. UIO projection engine
# ══════════════════════════════════════════════════════════════

def monthly_attrition_rate(annual_rate: float) -> float:
    """Convert annual attrition rate to monthly equivalent.

    Business meaning: fraction of the fleet that leaves service each month,
    compounded from the annual rate so the total annual loss equals annual_rate.
    """
    return 1.0 - (1.0 - annual_rate) ** (1.0 / 12.0)


def project_uio_total(
    current_uio_total: int,
    monthly_sales: list[int],
    annual_attrition_rate: float = DEFAULT_ANNUAL_ATTRITION,
) -> list[int]:
    """Project total UIO forward for each month in monthly_sales.

    Args:
        current_uio_total:   Fleet size at the start of the projection window.
        monthly_sales:       New bike registrations per month (list, ordered).
        annual_attrition_rate: Fraction of fleet lost each year (default 5 %).

    Returns:
        List of UIO totals, one per month, same length as monthly_sales.
    """
    mrate = monthly_attrition_rate(annual_attrition_rate)
    uio = float(current_uio_total)
    result: list[int] = []
    for sales in monthly_sales:
        attrition = uio * mrate
        uio = uio - attrition + sales
        result.append(max(0, round(uio)))
    return result


def project_uio_by_model(
    current_uio: pd.DataFrame,
    monthly_sales: list[int],
    model_mix: dict[str, float],
    annual_attrition_rate: float = DEFAULT_ANNUAL_ATTRITION,
) -> pd.DataFrame:
    """Project UIO per model over the forecast horizon.

    Returns DataFrame with columns: [period_idx, Model, UIO]
    where period_idx is 0-based (0 = first forecast month).
    """
    mrate = monthly_attrition_rate(annual_attrition_rate)
    models = list(current_uio["Model"])
    uio_state: dict[str, float] = {
        row["Model"]: float(row["UIO"]) for _, row in current_uio.iterrows()
    }

    records: list[dict] = []
    for t, sales in enumerate(monthly_sales):
        for model in models:
            share    = model_mix.get(model, 0.0)
            inflow   = sales * share
            attrition = uio_state[model] * mrate
            uio_state[model] = max(0.0, uio_state[model] - attrition + inflow)
            records.append({"period_idx": t, "Model": model, "UIO": round(uio_state[model])})

    return pd.DataFrame(records)


# ══════════════════════════════════════════════════════════════
# 3. Build combined output table
# ══════════════════════════════════════════════════════════════

def build_uio_table(
    historical_sales: pd.DataFrame,
    current_uio_total: int,
    fc: pd.DataFrame,
    annual_attrition_rate: float,
) -> pd.DataFrame:
    """Combine historical cumulative UIO (actuals) + forward projection.

    Historical: cumulative sum of new_sales gives the UIO growth curve
    within our data window, anchored to current_uio_total at the last actual month.

    Forward: stock-flow model from current_uio_total using Stage 2 forecast.

    Returns DataFrame[period, uio_total, new_sales, attrition, is_forecast,
                       lower_80, upper_80].
    """
    # ── Historical segment ──────────────────────────────────────
    hist = historical_sales.copy()
    hist["cumulative_sales"] = hist["new_sales"].cumsum()
    # Scale so the last actual month = current_uio_total
    scale_factor = current_uio_total / hist["cumulative_sales"].iloc[-1]
    hist["uio_total"]   = (hist["cumulative_sales"] * scale_factor).round().astype(int)
    hist["is_forecast"] = False
    hist["lower_80"]    = pd.NA
    hist["upper_80"]    = pd.NA
    hist["attrition"]   = 0
    hist_out = hist[["period", "new_sales", "uio_total", "attrition", "is_forecast", "lower_80", "upper_80"]]

    # ── Forward projection ──────────────────────────────────────
    fc_sales  = list(fc["forecast"])
    fc_lower  = list(fc["lower_80"])
    fc_upper  = list(fc["upper_80"])
    fc_periods = list(fc["period"])

    mrate = monthly_attrition_rate(annual_attrition_rate)
    uio = float(current_uio_total)
    fc_rows: list[dict] = []
    for period, sales, lo, hi in zip(fc_periods, fc_sales, fc_lower, fc_upper):
        attr = uio * mrate
        uio  = max(0.0, uio - attr + sales)
        # Optimistic/pessimistic using upper/lower sales bounds
        uio_lo = max(0.0, uio - attr + lo) - sales + lo   # simplified band
        uio_hi = max(0.0, uio - attr + hi) - sales + hi
        fc_rows.append({
            "period":      period,
            "new_sales":   sales,
            "uio_total":   round(uio),
            "attrition":   round(attr),
            "is_forecast": True,
            "lower_80":    round(uio * (lo / max(sales, 1))),
            "upper_80":    round(uio * (hi / max(sales, 1))),
        })

    fc_out = pd.DataFrame(fc_rows)
    combined = pd.concat([hist_out, fc_out], ignore_index=True)
    return combined


# ══════════════════════════════════════════════════════════════
# 4. Excel report
# ══════════════════════════════════════════════════════════════

def _wb_fmts(wb: xlsxwriter.Workbook) -> dict:
    return {
        "title":  wb.add_format({"bold": True, "font_size": 13, "font_color": "#003087"}),
        "sub":    wb.add_format({"italic": True, "font_color": "#555555"}),
        "hdr":    wb.add_format({"bold": True, "bg_color": "#003087", "font_color": "white", "border": 1, "align": "center"}),
        "num":    wb.add_format({"num_format": "#,##0", "border": 1}),
        "pct":    wb.add_format({"num_format": "0.0%", "border": 1}),
        "cell":   wb.add_format({"border": 1}),
        "fc":     wb.add_format({"num_format": "#,##0", "border": 1, "bg_color": "#FFF3E0"}),
        "hi":     wb.add_format({"num_format": "#,##0", "border": 1, "bg_color": "#E8F5E9"}),
        "info":   wb.add_format({"italic": True, "font_color": "#555555", "border": 1}),
    }


def _write_sheet_uio_projection(
    wb: xlsxwriter.Workbook,
    fmts: dict,
    combined: pd.DataFrame,
    annual_attrition_rate: float,
) -> None:
    ws = wb.add_worksheet("UIO Projection")
    ws.set_column("A:A", 12)
    ws.set_column("B:G", 14)

    ws.write("A1", "UIO Fleet Projection — Stock-Flow Model", fmts["title"])
    ws.write("A2", f"Annual attrition rate: {annual_attrition_rate:.0%}  |  Actuals (May–Dec 2025) + Forecast", fmts["sub"])

    headers = ["Period", "New Sales", "Attrition", "UIO Total", "Lower 80%", "Upper 80%", "Is Forecast"]
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmts["hdr"])

    for r, row in combined.iterrows():
        fmt_n = fmts["fc"] if row["is_forecast"] else fmts["num"]
        ws.write(r + 4, 0, row["period"],                      fmts["cell"])
        ws.write(r + 4, 1, int(row["new_sales"]),              fmt_n)
        ws.write(r + 4, 2, int(row["attrition"]) if pd.notna(row.get("attrition", None)) else 0, fmt_n)
        ws.write(r + 4, 3, int(row["uio_total"]),              fmts["hi"] if row["is_forecast"] else fmts["num"])
        ws.write(r + 4, 4, int(row["lower_80"]) if pd.notna(row["lower_80"]) else "", fmt_n)
        ws.write(r + 4, 5, int(row["upper_80"]) if pd.notna(row["upper_80"]) else "", fmt_n)
        ws.write(r + 4, 6, "Forecast" if row["is_forecast"] else "Actual",           fmts["cell"])

    # Chart
    n_rows = len(combined)
    chart = wb.add_chart({"type": "line"})
    chart.add_series({
        "name":       "UIO Total",
        "categories": ["UIO Projection", 4, 0, 4 + n_rows - 1, 0],
        "values":     ["UIO Projection", 4, 3, 4 + n_rows - 1, 3],
        "line":       {"color": "#003087", "width": 2},
    })
    chart.set_title({"name": "Units in Operation — Historical & Forecast"})
    chart.set_x_axis({"name": "Month"})
    chart.set_y_axis({"name": "UIO (units)", "num_format": "#,##0"})
    chart.set_legend({"position": "bottom"})
    chart.set_size({"width": 720, "height": 340})
    ws.insert_chart("I4", chart)


def _write_sheet_model_breakdown(
    wb: xlsxwriter.Workbook,
    fmts: dict,
    current_uio: pd.DataFrame,
    model_projection: pd.DataFrame,
    fc_periods: list[str],
) -> None:
    ws = wb.add_worksheet("By Model")
    ws.set_column("A:A", 30)
    ws.set_column("B:Z", 12)

    ws.write("A1", "UIO Projection by Model", fmts["title"])
    ws.write("A2", "Current UIO (Dec 2025) + 36-month forward projection", fmts["sub"])

    # Header row: Model | Current | month1 | month2 ...
    ws.write(3, 0, "Model", fmts["hdr"])
    ws.write(3, 1, "Current UIO", fmts["hdr"])
    for c, p in enumerate(fc_periods):
        ws.write(3, c + 2, p, fmts["hdr"])

    # Pivot model_projection for display
    pivoted = model_projection.pivot(index="Model", columns="period_idx", values="UIO")
    models = list(current_uio["Model"])

    for r, model in enumerate(models):
        ws.write(r + 4, 0, model, fmts["cell"])
        cur = current_uio.loc[current_uio["Model"] == model, "UIO"].values
        ws.write(r + 4, 1, int(cur[0]) if len(cur) else 0, fmts["num"])
        if model in pivoted.index:
            for c, val in enumerate(pivoted.loc[model]):
                ws.write(r + 4, c + 2, int(val), fmts["fc"])

    # Totals row
    tr = len(models) + 4
    ws.write(tr, 0, "TOTAL", fmts["hdr"])
    ws.write(tr, 1, int(current_uio["UIO"].sum()), fmts["num"])
    for c in range(len(fc_periods)):
        col_vals = [
            int(pivoted.at[m, c]) for m in models if m in pivoted.index and c in pivoted.columns
        ]
        ws.write(tr, c + 2, sum(col_vals), fmts["num"])


def _write_sheet_assumptions(
    wb: xlsxwriter.Workbook,
    fmts: dict,
    current_uio_total: int,
    annual_attrition_rate: float,
    horizon_months: int,
    data_note: str,
) -> None:
    ws = wb.add_worksheet("Assumptions")
    ws.set_column("A:A", 35)
    ws.set_column("B:B", 45)

    ws.write("A1", "Stage 3 — Assumptions & Data Notes", fmts["title"])

    rows = [
        ("Current UIO (base)",        f"{current_uio_total:,} units (from MCSI, May–Dec 2025)"),
        ("Annual attrition rate",     f"{annual_attrition_rate:.0%} of fleet leaves service each year"),
        ("Monthly attrition rate",    f"{monthly_attrition_rate(annual_attrition_rate):.4%}"),
        ("Forecast horizon",          f"{horizon_months} months"),
        ("New sales source",          "Stage 2 Linear Trend forecast"),
        ("Model mix basis",           "Proportional to current UIO by model"),
        ("Data window",               "May 2025 – Dec 2025 (8 months; April partial, excluded)"),
        ("Data limitation",           data_note),
        ("Attrition model",           "Uniform across all models; age-stratified data not available"),
        ("Stage downstream",          "Stage 6.4 — UIO-based spare-parts demand"),
    ]
    for r, (k, v) in enumerate(rows):
        ws.write(r + 3, 0, k, fmts["hdr"])
        ws.write(r + 3, 1, v, fmts["cell"])


# ══════════════════════════════════════════════════════════════
# 5. Main entry point
# ══════════════════════════════════════════════════════════════

def run(
    annual_attrition_rate: float = DEFAULT_ANNUAL_ATTRITION,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
    refresh: bool = False,
) -> None:
    """Execute Stage 3: UIO Forecast.

    Args:
        annual_attrition_rate: Fraction of fleet that leaves service annually.
        horizon_months:        Months to forecast forward (default 36 = 3 years).
        refresh:               If True, recompute even if output already exists.
    """
    if not refresh and _OUT_PARQUET.exists():
        logger.info("UIO forecast already exists. Pass refresh=True to recompute.")
        return

    logger.info("=" * 55)
    logger.info("STAGE 3 — UIO FORECAST")
    logger.info("=" * 55)

    # Step 1: Load inputs
    current_uio    = load_current_uio()
    hist_sales     = load_historical_monthly_sales()
    fc_df          = load_sales_forecast()
    current_total  = int(current_uio["UIO"].sum())
    model_mix      = get_model_mix(current_uio)

    # Limit forecast to horizon_months
    fc_df = fc_df.head(horizon_months).copy()

    # Step 2: Total UIO projection
    logger.info(f"Projecting UIO for {len(fc_df)} months at {annual_attrition_rate:.0%} annual attrition ...")
    monthly_sales_fc = list(fc_df["forecast"])
    uio_totals       = project_uio_total(current_total, monthly_sales_fc, annual_attrition_rate)

    # Step 3: By-model projection
    model_proj = project_uio_by_model(current_uio, monthly_sales_fc, model_mix, annual_attrition_rate)
    model_proj["period"] = model_proj["period_idx"].apply(
        lambda i: fc_df["period"].iloc[i] if i < len(fc_df) else ""
    )

    # Step 4: Combined table (actuals + forecast)
    combined = build_uio_table(hist_sales, current_total, fc_df, annual_attrition_rate)

    # Step 5: Save parquet
    _OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(_OUT_PARQUET, index=False)
    logger.info(f"Parquet saved: {_OUT_PARQUET}")

    # Step 6: Excel report
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb   = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))
    fmts = _wb_fmts(wb)

    _write_sheet_uio_projection(wb, fmts, combined, annual_attrition_rate)
    _write_sheet_model_breakdown(
        wb, fmts, current_uio, model_proj,
        fc_periods=list(fc_df["period"]),
    )
    _write_sheet_assumptions(
        wb, fmts,
        current_uio_total=current_total,
        annual_attrition_rate=annual_attrition_rate,
        horizon_months=horizon_months,
        data_note=(
            "MCSI data covers May–Dec 2025 only. Fleet size reflects 8 months "
            "of registrations. Pre-2025 fleet is not captured — actual UIO is "
            "likely larger. Revise once full historical data is available."
        ),
    )

    wb.close()
    logger.info(f"Excel report: {_OUTPUT_EXCEL}")

    # Step 7: Summary
    end_uio = uio_totals[-1] if uio_totals else current_total
    logger.info("=" * 55)
    logger.info("STAGE 3 SUMMARY")
    logger.info("=" * 55)
    logger.info(f"  Current UIO (Dec 2025)  : {current_total:,} units")
    logger.info(f"  Projected UIO ({fc_df['period'].iloc[-1]}): {end_uio:,} units")
    logger.info(f"  Fleet growth            : {end_uio - current_total:+,} units ({(end_uio/current_total - 1)*100:+.1f}%)")
    logger.info(f"  Annual attrition rate   : {annual_attrition_rate:.0%}")
    logger.info(f"  Forecast horizon        : {len(fc_df)} months")
    logger.info("  Top 3 models by current UIO:")
    for _, row in current_uio.head(3).iterrows():
        logger.info(f"    {row['Model']:<35}: {row['UIO']:,} ({row['UIO_pct']:.1f}%)")
    logger.info("Stage 3 complete.")
