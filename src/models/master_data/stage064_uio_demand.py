"""Stage 6.4 — UIO-based demand estimation for spare parts.

Business context:
  Spare-parts demand is driven by the installed fleet (Units in Operation).
  A part that fits Model X will be consumed proportionally to the number of
  Model-X bikes in the field, scaled by how frequently that part needs replacing.

Formula:
  replacement_frequency  = avg_monthly_issues / avg_uio   (per part × model)
  uio_demand_monthly     = projected_uio × replacement_frequency × supply_pct
  uio_demand_leadtime    = uio_demand_monthly × 3          (3-month lead time)

Inputs:
  data/interim/stock_movements.parquet  — issue-level demand history
  data/interim/uio_forecast.parquet     — projected UIO per period (all models summed)
  data/interim/uio_external.parquet     — model-level UIO breakdown
  data/interim/part_master.parquet      — compatible_models column per part

Outputs:
  data/interim/uio_based_demand.parquet
  data/outputs/stage064_uio_demand.xlsx
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xlsxwriter
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

warnings.filterwarnings("ignore")

_MOVEMENTS_PARQUET  = DATA_INTERIM / "stock_movements.parquet"
_UIO_FORECAST_PAR   = DATA_INTERIM / "uio_forecast.parquet"
_UIO_EXTERNAL_PAR   = DATA_INTERIM / "uio_external.parquet"
_PART_MASTER_PAR    = DATA_INTERIM / "part_master.parquet"
_OUT_PARQUET        = DATA_INTERIM / "uio_based_demand.parquet"
_OUT_EXCEL          = DATA_OUTPUTS / "stage064_uio_demand.xlsx"

DEFAULT_SUPPLY_PCT  = 0.60   # conservative: only plan for 60% of max theoretical demand
LEAD_TIME_MONTHS    = 3


# ══════════════════════════════════════════════════════════════
# 1. Data loading
# ══════════════════════════════════════════════════════════════

def _load(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _latest_uio_by_model(uio_external: pd.DataFrame) -> dict[str, float]:
    """Return the latest UIO count per model from the external UIO table."""
    if uio_external.empty:
        return {}
    model_col = next((c for c in ["model", "Model", "model_name"] if c in uio_external.columns), None)
    uio_col   = next((c for c in ["uio", "UIO", "total_uio", "uio_total"] if c in uio_external.columns), None)
    if model_col is None or uio_col is None:
        return {}
    latest = uio_external.copy()
    # If there's a period column, take the most recent period's UIO
    period_col = next((c for c in ["period", "year_month", "Year_Month_str"] if c in latest.columns), None)
    if period_col:
        latest = latest.sort_values(period_col).groupby(model_col).last().reset_index()
    return {str(r[model_col]): float(r[uio_col]) for _, r in latest.iterrows()}


def _projected_uio_total(uio_forecast: pd.DataFrame) -> float:
    """Return the next forecast UIO total (first future period)."""
    if uio_forecast.empty:
        return 0.0
    fc = uio_forecast[uio_forecast.get("is_forecast", pd.Series([False] * len(uio_forecast)))]
    if fc.empty:
        fc = uio_forecast
    uio_col = next((c for c in ["uio_total", "uio", "UIO"] if c in fc.columns), None)
    if uio_col is None:
        return 0.0
    return float(fc.iloc[0][uio_col])


# ══════════════════════════════════════════════════════════════
# 2. Demand history per part (from stock movements)
# ══════════════════════════════════════════════════════════════

def _monthly_issues(movements: pd.DataFrame) -> pd.DataFrame:
    """Aggregate monthly issue quantities per material from stock movements."""
    if movements.empty:
        return pd.DataFrame(columns=["material_9", "year_month", "issue_qty"])

    # Accept multiple movement-class column names written by different Stage 7 versions
    mc_col   = next((c for c in ["movement_class", "MovementClass", "move_class"] if c in movements.columns), None)
    mat_col  = next((c for c in ["material_9", "Material", "material"] if c in movements.columns), None)
    qty_col  = next((c for c in ["qty", "Qty", "quantity"] if c in movements.columns), None)
    dt_col   = next((c for c in ["posting_date", "PostingDate", "date"] if c in movements.columns), None)

    if any(c is None for c in [mc_col, mat_col, qty_col, dt_col]):
        logger.warning("stage064: stock_movements missing required columns — skipping demand history")
        return pd.DataFrame(columns=["material_9", "year_month", "issue_qty"])

    issues = movements[movements[mc_col] == "issue"].copy()
    issues["year_month"] = pd.to_datetime(issues[dt_col], errors="coerce").dt.to_period("M").astype(str)
    agg = (
        issues.groupby([mat_col, "year_month"])[qty_col]
        .sum()
        .reset_index()
        .rename(columns={mat_col: "material_9", qty_col: "issue_qty"})
    )
    return agg


# ══════════════════════════════════════════════════════════════
# 3. Core computation
# ══════════════════════════════════════════════════════════════

def compute_uio_demand(
    movements:    pd.DataFrame,
    uio_external: pd.DataFrame,
    uio_forecast: pd.DataFrame,
    part_master:  pd.DataFrame,
    supply_pct:   float = DEFAULT_SUPPLY_PCT,
) -> pd.DataFrame:
    """Compute UIO-driven monthly demand estimate per SKU.

    Returns one row per part_number with columns:
      material_9, description, compatible_models, model_count,
      avg_monthly_hist_demand, model_uio_total, replacement_freq_per_uio,
      projected_uio, uio_demand_monthly, uio_demand_leadtime,
      supply_pct_applied, hist_months
    """
    monthly_issues = _monthly_issues(movements)
    uio_by_model   = _latest_uio_by_model(uio_external)
    projected_uio  = _projected_uio_total(uio_forecast)

    if part_master.empty:
        logger.warning("stage064: part_master is empty — cannot compute UIO demand")
        return pd.DataFrame()

    # Normalise part master columns
    pn_col   = next((c for c in ["material_9", "part_number", "requested_pn", "latest_pn"] if c in part_master.columns), None)
    desc_col = next((c for c in ["description", "Description"] if c in part_master.columns), None)
    mdl_col  = next((c for c in ["compatible_models", "Compatible models"] if c in part_master.columns), None)

    if pn_col is None:
        logger.error("stage064: part_master has no recognisable part-number column")
        return pd.DataFrame()

    pm = part_master[[c for c in [pn_col, desc_col, mdl_col] if c]].copy()
    pm = pm.rename(columns={pn_col: "material_9", desc_col: "description", mdl_col: "compatible_models"})
    pm["material_9"] = pm["material_9"].astype(str).str.strip()

    # Average monthly historical demand per part
    if not monthly_issues.empty:
        hist_avg = (
            monthly_issues.groupby("material_9")
            .agg(avg_monthly=("issue_qty", "mean"), hist_months=("year_month", "nunique"))
            .reset_index()
        )
    else:
        hist_avg = pd.DataFrame(columns=["material_9", "avg_monthly", "hist_months"])

    pm = pm.merge(hist_avg, on="material_9", how="left")
    pm["avg_monthly"] = pm["avg_monthly"].fillna(0.0)
    pm["hist_months"] = pm["hist_months"].fillna(0).astype(int)

    # UIO for compatible models — sum UIO across all models the part fits
    def _sum_uio(models_str: str | None) -> float:
        if not models_str or str(models_str).lower() in ("", "nan", "none"):
            return projected_uio  # unknown compatibility → use fleet total
        models = [m.strip() for m in str(models_str).split(",") if m.strip()]
        return sum(uio_by_model.get(m, 0.0) for m in models) or projected_uio

    def _model_count(models_str: str | None) -> int:
        if not models_str or str(models_str).lower() in ("", "nan", "none"):
            return 0
        return len([m for m in str(models_str).split(",") if m.strip()])

    pm["model_uio_total"] = pm.get("compatible_models", pd.Series([""] * len(pm))).apply(_sum_uio)
    pm["model_count"]     = pm.get("compatible_models", pd.Series([""] * len(pm))).apply(_model_count)

    # Replacement frequency: parts consumed per UIO per month
    # Guard: only compute for parts with any historical demand and non-zero UIO
    pm["replacement_freq_per_uio"] = np.where(
        (pm["avg_monthly"] > 0) & (pm["model_uio_total"] > 0),
        pm["avg_monthly"] / pm["model_uio_total"],
        0.0,
    )

    # Project demand using forecasted UIO
    pm["projected_uio"]       = projected_uio
    pm["uio_demand_monthly"]  = pm["replacement_freq_per_uio"] * pm["projected_uio"] * supply_pct
    pm["uio_demand_leadtime"] = pm["uio_demand_monthly"] * LEAD_TIME_MONTHS
    pm["supply_pct_applied"]  = supply_pct

    # Round sensible
    for col in ["avg_monthly", "model_uio_total", "replacement_freq_per_uio",
                "uio_demand_monthly", "uio_demand_leadtime"]:
        pm[col] = pm[col].round(4)

    out_cols = [
        "material_9", "description", "compatible_models", "model_count",
        "avg_monthly", "hist_months", "model_uio_total",
        "replacement_freq_per_uio", "projected_uio",
        "uio_demand_monthly", "uio_demand_leadtime", "supply_pct_applied",
    ]
    result = pm[[c for c in out_cols if c in pm.columns]].copy()
    result = result.sort_values("uio_demand_monthly", ascending=False).reset_index(drop=True)

    logger.info(
        f"stage064: {len(result):,} parts | "
        f"projected UIO={projected_uio:,.0f} | "
        f"supply_pct={supply_pct:.0%} | "
        f"non-zero demand={int((result['uio_demand_monthly'] > 0).sum()):,}"
    )
    return result


# ══════════════════════════════════════════════════════════════
# 4. Excel report
# ══════════════════════════════════════════════════════════════

def write_excel(result: pd.DataFrame, supply_pct: float) -> None:
    _OUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb   = xlsxwriter.Workbook(str(_OUT_EXCEL))
    tf   = wb.add_format({"bold": True, "font_size": 13, "font_color": "#003087"})
    sf   = wb.add_format({"italic": True, "font_color": "#555555"})
    hdr  = wb.add_format({"bold": True, "bg_color": "#003087", "font_color": "white", "border": 1, "align": "center"})
    num  = wb.add_format({"num_format": "#,##0.00", "border": 1})
    cell = wb.add_format({"border": 1})
    lbl  = wb.add_format({"bold": True, "bg_color": "#E3F2FD", "border": 1})

    ws = wb.add_worksheet("UIO Demand")
    ws.set_column("A:A", 18)
    ws.set_column("B:B", 45)
    ws.set_column("C:C", 30)
    ws.set_column("D:M", 20)

    ws.write("A1", "Stage 6.4 — UIO-Based Demand Estimates", tf)
    ws.write("A2", f"supply_pct={supply_pct:.0%} · lead_time={LEAD_TIME_MONTHS} months · replacement_freq = avg_issues / model_UIO", sf)

    for c, col in enumerate(result.columns):
        ws.write(2, c, col, hdr)
    for r, (_, row) in enumerate(result.iterrows()):
        for c, val in enumerate(row):
            if isinstance(val, (int, np.integer)):
                ws.write(3 + r, c, int(val), num)
            elif isinstance(val, (float, np.floating)):
                ws.write(3 + r, c, round(float(val), 4), num)
            else:
                ws.write(3 + r, c, str(val) if pd.notna(val) else "", cell)

    # Summary sheet
    ws2 = wb.add_worksheet("Summary")
    ws2.set_column("A:A", 40)
    ws2.set_column("B:B", 20)
    ws2.write("A1", "UIO Demand — Summary", tf)
    summary = [
        ("Total parts evaluated",    len(result)),
        ("Parts with UIO demand > 0", int((result["uio_demand_monthly"] > 0).sum())),
        ("Supply % applied",          f"{supply_pct:.0%}"),
        ("Lead time (months)",        LEAD_TIME_MONTHS),
        ("Projected UIO (fleet)",     float(result["projected_uio"].iloc[0]) if len(result) else 0),
        ("Sum uio_demand_monthly",    round(float(result["uio_demand_monthly"].sum()), 2)),
        ("Sum uio_demand_leadtime",   round(float(result["uio_demand_leadtime"].sum()), 2)),
    ]
    for r, (k, v) in enumerate(summary):
        ws2.write(r + 3, 0, k, lbl)
        ws2.write(r + 3, 1, v, cell)

    wb.close()
    logger.info(f"stage064 Excel written: {_OUT_EXCEL}")


# ══════════════════════════════════════════════════════════════
# 5. Entry point
# ══════════════════════════════════════════════════════════════

def run(supply_pct: float = DEFAULT_SUPPLY_PCT, refresh: bool = False) -> None:
    """Execute Stage 6.4 — UIO-based demand estimation."""
    if not refresh and _OUT_PARQUET.exists():
        logger.info("stage064: output parquet exists. Pass refresh=True to recompute.")
        return

    logger.info("=" * 55)
    logger.info("STAGE 6.4 — UIO-BASED DEMAND ESTIMATION")
    logger.info("=" * 55)

    movements    = _load(_MOVEMENTS_PARQUET)
    uio_forecast = _load(_UIO_FORECAST_PAR)
    uio_external = _load(_UIO_EXTERNAL_PAR)
    part_master  = _load(_PART_MASTER_PAR)

    result = compute_uio_demand(movements, uio_external, uio_forecast, part_master, supply_pct)

    if result.empty:
        logger.warning("stage064: empty result — check input parquets")
        return

    _OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(_OUT_PARQUET, index=False)
    logger.info(f"stage064: saved {_OUT_PARQUET}")

    write_excel(result, supply_pct)
    logger.info("Stage 6.4 complete.")
