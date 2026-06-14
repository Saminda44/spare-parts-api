"""Stage 10 — Demand forecast: per-SKU 3-month ahead point forecasts.

Inputs:
  data/interim/monthly_demand.parquet  — per-SKU monthly demand (Stage 7 output)
  data/interim/abc_xyz_fsn.parquet     — per-SKU classification (Stage 9 output)

Outputs:
  data/interim/demand_forecast.parquet      — per-SKU forecasts (→ Stage 12)
  data/outputs/stage10_demand_forecast.xlsx — 5-sheet forecast report

Model selection — tiered by data complexity and SKU importance:

  Tier 0: Zero             — non-moving SKUs (active_months == 0)
  Tier 1: HistoricAverage  — very sparse (< 3 active months); not enough data for TS models
  Tier 2: CrostonOptimized — Z-class (erratic/lumpy), ≥ 3 months; specialized for intermittent
  Tier 3: AutoETS vs LightGBM (global ML) — X/Y-class, 3–11 active months;
           select winner per group by holdout MAE
  Tier 4: AutoETS vs LightGBM vs NHITS (DL) — A/B-class, X/Y, ≥ 12 months;
           select winner per SKU by holdout MAE

  LightGBM learns cross-SKU patterns (seasonality, trends) from all SKUs jointly.
  NHITS (Neural Hierarchical Interpolation for TS) decomposes forecasts into
  multi-scale components; applied only when ≥ 12 months of data are available.

Forecast horizon: 3 months (= India import lead time).
Key outputs used by Stage 12: forecast_lt (total over 3 months), demand_std_monthly.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

_DEMAND_PARQUET = DATA_INTERIM / "monthly_demand.parquet"
_ABC_XYZ_PARQUET = DATA_INTERIM / "abc_xyz_fsn.parquet"
_FORECAST_PARQUET = DATA_INTERIM / "demand_forecast.parquet"
_OUTPUT_XLSX = DATA_OUTPUTS / "stage10_demand_forecast.xlsx"

_HORIZON = 3               # months ahead = lead time
_MIN_ACTIVE_FOR_MODEL = 3  # minimum active months to fit ETS / Croston


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------

def build_panel(demand: pd.DataFrame, uids: list[str]) -> pd.DataFrame:
    """Build a complete (unique_id, ds, y) panel for the requested SKUs.

    Every SKU gets an entry for all months in the observation window; months
    with no movement are filled with y=0 so time-series models see the full
    demand pattern including silent periods.

    Args:
        demand: monthly_demand DataFrame with material_9, year_month_str, issue_qty.
        uids: list of material_9 values to include.

    Returns:
        DataFrame with columns [unique_id, ds, y] sorted by (unique_id, ds).
    """
    # all_months from the FULL demand table so the panel covers the complete
    # observation window; SKUs absent in a given month get y=0 via the merge.
    demand = demand.copy()
    demand["ds"] = pd.to_datetime(demand["year_month_str"] + "-01")
    all_months = sorted(demand["ds"].unique())

    sub = demand[demand["material_9"].isin(uids)].copy()
    full_idx = pd.MultiIndex.from_product(
        [uids, all_months], names=["unique_id", "ds"]
    )
    # Aggregate demand (sum handles any duplicate rows) then merge onto full grid
    agg = (
        sub.groupby(["material_9", "ds"])["issue_qty"]
        .sum()
        .reset_index()
        .rename(columns={"material_9": "unique_id", "issue_qty": "y"})
    )
    full_grid = pd.DataFrame(full_idx.tolist(), columns=["unique_id", "ds"])
    panel = full_grid.merge(agg, on=["unique_id", "ds"], how="left")
    panel["y"] = panel["y"].fillna(0.0)
    panel["unique_id"] = panel["unique_id"].astype(str)
    return panel.sort_values(["unique_id", "ds"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Forecast runners
# ---------------------------------------------------------------------------

def _run_batch(panel: pd.DataFrame, model: object, method_label: str) -> pd.DataFrame:
    """Run one StatsForecast batch and return a (unique_id, forecast_col) table.

    Returns:
        DataFrame with columns [unique_id, forecast_m1, forecast_m2, forecast_m3,
        forecast_lt, method] where forecast values are clipped to ≥ 0.
    """
    from statsforecast import StatsForecast

    sf = StatsForecast(models=[model], freq="MS", n_jobs=-1, verbose=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pred = sf.forecast(df=panel, h=_HORIZON)
    except Exception as exc:
        logger.warning(f"{method_label} batch failed ({exc!r}); falling back to HistoricAverage")
        from statsforecast.models import HistoricAverage as _HA
        sf = StatsForecast(models=[_HA()], freq="MS", n_jobs=-1, verbose=False)
        pred = sf.forecast(df=panel, h=_HORIZON)
        method_label = "HistoricAverage"

    # Column name matches the model class name
    model_col = [c for c in pred.columns if c not in ("unique_id", "ds")][0]
    pred[model_col] = pred[model_col].clip(lower=0.0)

    # Pivot the 3 horizon steps into columns
    pred = pred.sort_values(["unique_id", "ds"]).copy()
    pred["horizon"] = pred.groupby("unique_id").cumcount() + 1
    wide = pred.pivot(index="unique_id", columns="horizon", values=model_col).reset_index()
    wide.columns = ["unique_id", "forecast_m1", "forecast_m2", "forecast_m3"]
    wide["forecast_lt"] = wide[["forecast_m1", "forecast_m2", "forecast_m3"]].sum(axis=1)
    wide["method"] = method_label
    return wide


def _zero_forecast(uids: list[str]) -> pd.DataFrame:
    """Return a zero-forecast table for non-moving SKUs."""
    df = pd.DataFrame({"unique_id": uids})
    df["forecast_m1"] = 0.0
    df["forecast_m2"] = 0.0
    df["forecast_m3"] = 0.0
    df["forecast_lt"] = 0.0
    df["method"] = "Zero"
    return df


# ---------------------------------------------------------------------------
# Demand statistics
# ---------------------------------------------------------------------------

def compute_demand_stats(demand: pd.DataFrame, total_months: int) -> pd.DataFrame:
    """Compute historical demand statistics per SKU for safety stock sizing.

    Args:
        demand: monthly_demand DataFrame.
        total_months: length of the observation window (number of calendar months).

    Returns:
        DataFrame [material_9, demand_mean_monthly, demand_std_monthly,
                   demand_std_lt, cv_hist] where demand_std_lt = std * sqrt(HORIZON).
    """
    grp = demand.groupby("material_9")["issue_qty"].agg(["mean", "std"]).reset_index()
    grp.columns = ["material_9", "demand_mean_monthly", "demand_std_monthly"]
    grp["demand_std_monthly"] = grp["demand_std_monthly"].fillna(0.0)
    grp["demand_std_lt"] = grp["demand_std_monthly"] * np.sqrt(_HORIZON)
    grp["cv_hist"] = np.where(
        grp["demand_mean_monthly"] > 0,
        grp["demand_std_monthly"] / grp["demand_mean_monthly"],
        0.0,
    )
    return grp


# ---------------------------------------------------------------------------
# ML / DL model selection helpers
# ---------------------------------------------------------------------------

_MIN_MONTHS_TIER4 = 12   # minimum active months for NHITS candidacy


def _select_best_per_sku(
    ets_mae:   dict[str, float],
    lgbm_mae:  dict[str, float],
    nhits_mae: dict[str, float] | None,
    ets_fc:    pd.DataFrame,
    lgbm_fc:   pd.DataFrame,
    nhits_fc:  pd.DataFrame | None,
) -> pd.DataFrame:
    """Per-SKU winner selection: pick the model with lowest holdout MAE.

    Falls back to AutoETS if a model has no MAE entry (e.g. NHITS skipped a SKU).
    """
    all_uids = set(ets_fc["unique_id"])
    records: list[pd.DataFrame] = []

    for uid in all_uids:
        uid_str = str(uid)
        scores: dict[str, float] = {}
        if uid_str in ets_mae:
            scores["AutoETS"] = ets_mae[uid_str]
        if uid_str in lgbm_mae:
            scores["LightGBM"] = lgbm_mae[uid_str]
        if nhits_mae and uid_str in nhits_mae:
            scores["NHITS"] = nhits_mae[uid_str]

        if not scores:
            # No evaluation available → default to AutoETS
            winner = "AutoETS"
        else:
            winner = min(scores, key=scores.__getitem__)

        if winner == "LightGBM" and uid_str in lgbm_fc["unique_id"].values:
            row = lgbm_fc[lgbm_fc["unique_id"] == uid_str]
        elif winner == "NHITS" and nhits_fc is not None and uid_str in nhits_fc["unique_id"].values:
            row = nhits_fc[nhits_fc["unique_id"] == uid_str]
        else:
            row = ets_fc[ets_fc["unique_id"] == uid]
            row = row.copy()
            row["method"] = "AutoETS"

        records.append(row)

    return pd.concat(records, ignore_index=True)


def _run_tier3(
    demand: pd.DataFrame,
    classified: pd.DataFrame,
    uids: list[str],
) -> pd.DataFrame:
    """Tier 3: AutoETS vs LightGBM — pick best by holdout MAE."""
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS

    from src.models.demand_forecast._feature_eng import holdout_split
    from src.models.demand_forecast._lgbm_model import evaluate_lgbm, forecast_lgbm

    logger.info(f"Tier 3 (AutoETS vs LightGBM): {len(uids):,} SKUs")
    full_panel  = build_panel(demand, uids)
    train_panel, _ = holdout_split(full_panel, h=_HORIZON)

    # AutoETS — holdout MAE
    logger.info("  Evaluating AutoETS …")
    sf = StatsForecast(models=[AutoETS()], freq="MS", n_jobs=-1, verbose=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ets_cv = sf.cross_validation(df=full_panel, h=_HORIZON, n_windows=1)
        ets_col = [c for c in ets_cv.columns if c not in ("unique_id", "ds", "cutoff", "y")][0]
        ets_cv[ets_col] = ets_cv[ets_col].clip(lower=0.0)
        ets_mae_map = (
            ets_cv.groupby("unique_id")
            .apply(lambda g: float(np.abs(g["y"] - g[ets_col]).mean()))
            .to_dict()
        )
    except Exception as exc:
        logger.warning(f"  AutoETS CV failed: {exc!r}")
        ets_mae_map = {}

    # LightGBM — holdout MAE
    logger.info("  Evaluating LightGBM …")
    lgbm_mae_map = evaluate_lgbm(full_panel, classified, h=_HORIZON)

    # Full-data forecasts
    logger.info("  Fitting final AutoETS on full data …")
    ets_fc = _run_batch(full_panel, AutoETS(), "AutoETS")

    logger.info("  Fitting final LightGBM on full data …")
    lgbm_fc = forecast_lgbm(full_panel, classified, h=_HORIZON)
    lgbm_fc = lgbm_fc.rename(columns={"unique_id": "unique_id"})  # keep name

    result = _select_best_per_sku(ets_mae_map, lgbm_mae_map, None, ets_fc, lgbm_fc, None)
    counts = result["method"].value_counts().to_dict()
    logger.info(f"  Tier 3 winner counts: {counts}")
    return result


def _run_tier4(
    demand: pd.DataFrame,
    classified: pd.DataFrame,
    uids: list[str],
) -> pd.DataFrame:
    """Tier 4: AutoETS vs LightGBM vs NHITS — pick best per SKU by holdout MAE."""
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS

    from src.models.demand_forecast._feature_eng import holdout_split
    from src.models.demand_forecast._lgbm_model import evaluate_lgbm, forecast_lgbm
    from src.models.demand_forecast._neural_model import (
        available as nhits_available,
    )
    from src.models.demand_forecast._neural_model import evaluate_nhits, forecast_nhits

    logger.info(f"Tier 4 (AutoETS vs LightGBM vs NHITS): {len(uids):,} SKUs")
    full_panel = build_panel(demand, uids)

    # AutoETS holdout MAE
    logger.info("  Evaluating AutoETS …")
    sf = StatsForecast(models=[AutoETS()], freq="MS", n_jobs=-1, verbose=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ets_cv = sf.cross_validation(df=full_panel, h=_HORIZON, n_windows=1)
        ets_col = [c for c in ets_cv.columns if c not in ("unique_id", "ds", "cutoff", "y")][0]
        ets_cv[ets_col] = ets_cv[ets_col].clip(lower=0.0)
        ets_mae_map = (
            ets_cv.groupby("unique_id")
            .apply(lambda g: float(np.abs(g["y"] - g[ets_col]).mean()))
            .to_dict()
        )
    except Exception as exc:
        logger.warning(f"  AutoETS CV failed: {exc!r}")
        ets_mae_map = {}

    # LightGBM holdout MAE
    logger.info("  Evaluating LightGBM …")
    lgbm_mae_map = evaluate_lgbm(full_panel, classified, h=_HORIZON)

    # NHITS holdout MAE (only if library is available)
    nhits_mae_map: dict[str, float] | None = None
    nhits_fc: pd.DataFrame | None = None
    if nhits_available():
        logger.info("  Evaluating NHITS …")
        try:
            nhits_mae_map = evaluate_nhits(full_panel, h=_HORIZON)
        except Exception as exc:
            logger.warning(f"  NHITS evaluation failed: {exc!r}")
    else:
        logger.warning("  neuralforecast not available; skipping NHITS")

    # Full-data forecasts
    logger.info("  Fitting final AutoETS …")
    ets_fc = _run_batch(full_panel, AutoETS(), "AutoETS")

    logger.info("  Fitting final LightGBM …")
    lgbm_fc = forecast_lgbm(full_panel, classified, h=_HORIZON)

    if nhits_available() and nhits_mae_map:
        logger.info("  Fitting final NHITS …")
        try:
            nhits_fc = forecast_nhits(full_panel, h=_HORIZON)
        except Exception as exc:
            logger.warning(f"  NHITS final forecast failed: {exc!r}")

    result = _select_best_per_sku(ets_mae_map, lgbm_mae_map, nhits_mae_map, ets_fc, lgbm_fc, nhits_fc)
    counts = result["method"].value_counts().to_dict()
    logger.info(f"  Tier 4 winner counts: {counts}")
    return result


# ---------------------------------------------------------------------------
# Main forecasting orchestrator
# ---------------------------------------------------------------------------

def compute_forecasts(demand: pd.DataFrame, classified: pd.DataFrame) -> pd.DataFrame:
    """Select forecasting method per SKU tier and generate 3-month ahead forecasts.

    Business meaning: the 3-month horizon matches the India import lead time.
    forecast_lt is the total expected demand during the replenishment cycle;
    demand_std_lt is the demand variability over that same window — both are
    inputs to the ROL/ROQ calculation in Stage 12.

    Tier assignment:
      0: Zero             — active_months == 0
      1: HistoricAverage  — active_months 1–2
      2: Croston          — Z-class, ≥ 3 months
      3: AutoETS / LGBM   — X/Y-class, 3–11 months  (ML model selection)
      4: AutoETS / LGBM / NHITS — A/B-class, X/Y, ≥ 12 months (DL model selection)

    Args:
        demand: monthly_demand DataFrame (all SKUs, all months).
        classified: abc_xyz_fsn DataFrame with active_months, xyz, abc columns.

    Returns:
        DataFrame with one row per SKU containing forecast + demand statistics.
    """
    from statsforecast.models import CrostonOptimized, HistoricAverage

    total_months = demand["year_month_str"].nunique()

    nm_mask      = classified["active_months"] == 0
    sparse_mask  = (classified["active_months"] > 0) & (classified["active_months"] < _MIN_ACTIVE_FOR_MODEL)
    croston_mask = (classified["active_months"] >= _MIN_ACTIVE_FOR_MODEL) & (classified["xyz"] == "Z")
    # X/Y active — split into Tier 3 (3-11 months) and Tier 4 (≥12, A/B-class)
    xy_active    = (classified["active_months"] >= _MIN_ACTIVE_FOR_MODEL) & (classified["xyz"].isin(["X", "Y"]))
    tier4_mask   = xy_active & (classified["abc"].isin(["A", "B"])) & (classified["active_months"] >= _MIN_MONTHS_TIER4)
    tier3_mask   = xy_active & ~tier4_mask

    uid_nm      = classified.loc[nm_mask,      "material_9"].tolist()
    uid_sparse  = classified.loc[sparse_mask,  "material_9"].tolist()
    uid_croston = classified.loc[croston_mask, "material_9"].tolist()
    uid_tier3   = classified.loc[tier3_mask,   "material_9"].tolist()
    uid_tier4   = classified.loc[tier4_mask,   "material_9"].tolist()

    logger.info(
        f"Tier split — T0(Zero):{len(uid_nm):,} | T1(HistAvg):{len(uid_sparse):,} | "
        f"T2(Croston):{len(uid_croston):,} | T3(ETS+LGBM):{len(uid_tier3):,} | "
        f"T4(ETS+LGBM+NHITS):{len(uid_tier4):,}"
    )

    parts: list[pd.DataFrame] = []

    if uid_nm:
        parts.append(_zero_forecast(uid_nm))

    if uid_sparse:
        logger.info(f"T1: HistoricAverage for {len(uid_sparse):,} sparse SKUs")
        parts.append(_run_batch(build_panel(demand, uid_sparse), HistoricAverage(), "HistoricAverage"))

    if uid_croston:
        logger.info(f"T2: Croston for {len(uid_croston):,} intermittent SKUs")
        parts.append(_run_batch(build_panel(demand, uid_croston), CrostonOptimized(), "Croston"))

    if uid_tier3:
        parts.append(_run_tier3(demand, classified, uid_tier3))

    if uid_tier4:
        parts.append(_run_tier4(demand, classified, uid_tier4))

    forecasts = pd.concat(parts, ignore_index=True).rename(columns={"unique_id": "material_9"})

    # Attach demand statistics
    stats = compute_demand_stats(demand, total_months)
    forecasts = forecasts.merge(stats, on="material_9", how="left")
    forecasts["demand_std_monthly"] = forecasts["demand_std_monthly"].fillna(0.0)
    forecasts["demand_std_lt"]      = forecasts["demand_std_lt"].fillna(0.0)
    forecasts["cv_hist"]            = forecasts["cv_hist"].fillna(0.0)

    # Attach classification columns
    cls_cols = ["material_9", "description", "abc", "xyz", "fsn", "abc_xyz_fsn",
                "policy_tier", "active_months", "total_issue_value_lkr", "avg_monthly_demand"]
    cls_cols = [c for c in cls_cols if c in classified.columns]
    forecasts = forecasts.merge(classified[cls_cols], on="material_9", how="left")

    # Sort: critical first (A-class), then by forecast_lt descending
    forecasts["_abc_ord"] = forecasts["abc"].map({"A": 0, "B": 1, "C": 2}).fillna(3)
    forecasts = (
        forecasts.sort_values(["_abc_ord", "forecast_lt"], ascending=[True, False])
        .drop(columns=["_abc_ord"])
        .reset_index(drop=True)
    )

    logger.info(
        f"Forecasts computed: {len(forecasts):,} SKUs | "
        f"A-class forecast_lt sum: {forecasts.loc[forecasts['abc']=='A','forecast_lt'].sum():,.0f} units"
    )
    return forecasts


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def method_summary(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Count of SKUs and total forecast_lt per method."""
    grp = (
        forecasts.groupby("method")
        .agg(sku_count=("material_9", "count"), total_forecast_lt=("forecast_lt", "sum"))
        .reset_index()
        .sort_values("total_forecast_lt", ascending=False)
    )
    return grp.reset_index(drop=True)


def abc_forecast_summary(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Per-ABC-class forecast summary."""
    grp = (
        forecasts.groupby("abc")
        .agg(
            sku_count=("material_9", "count"),
            total_forecast_lt=("forecast_lt", "sum"),
            avg_forecast_monthly=("forecast_m1", "mean"),
            avg_cv=("cv_hist", "mean"),
        )
        .reindex(["A", "B", "C"])
        .reset_index()
    )
    return grp


def top_forecast_skus(forecasts: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Top N SKUs by forecast_lt (highest priority for replenishment planning)."""
    cols = [
        "material_9", "description", "method", "abc_xyz_fsn", "policy_tier",
        "forecast_m1", "forecast_m2", "forecast_m3", "forecast_lt",
        "demand_std_lt", "active_months",
    ]
    cols = [c for c in cols if c in forecasts.columns]
    return (
        forecasts[forecasts["forecast_lt"] > 0]
        .nlargest(top_n, "forecast_lt")[cols]
        .reset_index(drop=True)
    )


def summary_kpis(forecasts: pd.DataFrame) -> dict:
    """Headline KPIs for the forecast report."""
    non_zero = forecasts[forecasts["forecast_lt"] > 0]
    return {
        "Total SKUs": len(forecasts),
        "SKUs with Non-zero Forecast": len(non_zero),
        "Zero Forecast (Non-moving) SKUs": int((forecasts["method"] == "Zero").sum()),
        "AutoETS SKUs": int((forecasts["method"] == "AutoETS").sum()),
        "Croston SKUs": int((forecasts["method"] == "Croston").sum()),
        "HistoricAverage SKUs": int((forecasts["method"] == "HistoricAverage").sum()),
        "Total Forecast (3-month, all SKUs)": float(forecasts["forecast_lt"].sum()),
        "A-class Total Forecast (3-month)": float(forecasts.loc[forecasts["abc"] == "A", "forecast_lt"].sum()),
        "B-class Total Forecast (3-month)": float(forecasts.loc[forecasts["abc"] == "B", "forecast_lt"].sum()),
        "Median Forecast Monthly (active SKUs)": float(forecasts.loc[non_zero.index, "forecast_m1"].median()),
        "Max Forecast_LT (single SKU)": float(forecasts["forecast_lt"].max()),
    }


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

def _write_excel(
    kpis: dict,
    method_sum: pd.DataFrame,
    abc_sum: pd.DataFrame,
    top: pd.DataFrame,
    forecasts: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white"})

        def write_sheet(df: pd.DataFrame, sheet: str, widths: list[int]) -> None:
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for i, (col, w) in enumerate(zip(df.columns, widths)):
                ws.set_column(i, i, w)
                ws.write(0, i, col, hdr)

        # Sheet 1 — Summary KPIs
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        write_sheet(kpi_df, "Summary KPIs", [48, 25])

        # Sheet 2 — Method breakdown
        write_sheet(method_sum, "Method Breakdown", [18, 14, 20])

        # Sheet 3 — ABC forecast summary
        write_sheet(abc_sum, "ABC Forecast", [8, 12, 20, 20, 12])

        # Sheet 4 — Top SKUs by forecast
        write_sheet(
            top,
            "Top Forecast SKUs",
            [20, 40, 16, 14, 16, 14, 14, 14, 14, 14, 14],
        )

        # Sheet 5 — Full forecast table (capped at 50K)
        out_cols = [
            "material_9", "description", "abc", "xyz", "fsn", "abc_xyz_fsn",
            "policy_tier", "method",
            "forecast_m1", "forecast_m2", "forecast_m3", "forecast_lt",
            "demand_mean_monthly", "demand_std_monthly", "demand_std_lt", "cv_hist",
            "active_months",
        ]
        out_cols = [c for c in out_cols if c in forecasts.columns]
        write_sheet(
            forecasts[out_cols].head(50_000),
            "All Forecasts",
            [20, 40, 6, 6, 6, 12, 16, 16, 14, 14, 14, 14, 18, 18, 14, 10, 14],
        )

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 10 entry point: demand forecasting."""
    if not refresh and _FORECAST_PARQUET.exists():
        logger.info("Stage 10 cached — skipping (use --refresh to force)")
        return

    logger.info("Stage 10: Demand Forecast")

    for path, label in [(_DEMAND_PARQUET, "monthly_demand.parquet"), (_ABC_XYZ_PARQUET, "abc_xyz_fsn.parquet")]:
        if not path.exists():
            raise FileNotFoundError(f"{label} not found at {path}. Run earlier stages first.")

    demand = pd.read_parquet(_DEMAND_PARQUET)
    classified = pd.read_parquet(_ABC_XYZ_PARQUET)
    logger.info(f"Demand loaded: {len(demand):,} rows | Classification: {len(classified):,} SKUs")

    forecasts = compute_forecasts(demand, classified)

    forecasts.to_parquet(_FORECAST_PARQUET, index=False)
    logger.info(f"Forecasts saved → {_FORECAST_PARQUET} ({len(forecasts):,} rows)")

    kpis     = summary_kpis(forecasts)
    meth_sum = method_summary(forecasts)
    abc_sum  = abc_forecast_summary(forecasts)
    top      = top_forecast_skus(forecasts, top_n=20)

    _write_excel(kpis, meth_sum, abc_sum, top, forecasts)

    logger.info(
        f"Stage 10 complete | "
        f"Non-zero forecasts: {kpis['SKUs with Non-zero Forecast']:,} | "
        f"Total 3-month demand: {kpis['Total Forecast (3-month, all SKUs)']:,.0f} units"
    )
