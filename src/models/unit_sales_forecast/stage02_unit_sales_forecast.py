"""Stage 2: Motorcycle Unit Sales Forecast — with seasonality diagnostics, UIO, and target management.

Pipeline:
  1. Load monthly series from Stage 1 parquet (drop partial month, exclude Company).
  2. Seasonality diagnostic:
       - Linear trend test (linregress)
       - Autocorrelation test (Ljung-Box at lags 1-3)
       - Unit-root test (ADF, flagged unreliable if n < 20)
       - Seasonal-period check: flag if < 24 months available
     → Select model family based on test evidence.
  3. Candidate models:
       - Linear Trend (OLS) — baseline
       - Holt ETS (damped additive trend, statsmodels)
       - ARIMA (auto_arima via pmdarima, non-seasonal)
       - Prophet (flexible trend, no seasonality forced)
     → Holdout on last 2 months; best MAPE wins.
  4. Refit winner on full history → 12-month forecast + 80 % CI.
  5. UIO calculation from MCSI parquet (cumulative sold VINs).
  6. Target management:
       - set_annual_target(year, units) — store in JSON
       - auto-distribute annual target to monthly via forecast seasonal weights
       - set_monthly_target(year, month, units) — per-month override
       - targets persist across runs in data/interim/unit_sales_targets.json
  7. Write multi-sheet Excel report.

Outputs:
  data/interim/unit_sales_forecast.parquet
  data/interim/unit_sales_targets.json
  data/interim/mcsi_uio_summary.parquet
  data/outputs/stage02_unit_sales_forecast.xlsx
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import xlsxwriter
from loguru import logger
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.stattools import acf, adfuller

from src.config.constants import CURRENCY
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────
_CLEAN_PARQUET    = DATA_INTERIM / "mcsi_clean.parquet"
_FORECAST_PARQUET = DATA_INTERIM / "unit_sales_forecast.parquet"
_TARGETS_JSON     = DATA_INTERIM / "unit_sales_targets.json"
_UIO_PARQUET      = DATA_INTERIM / "mcsi_uio_summary.parquet"
_OUTPUT_EXCEL     = DATA_OUTPUTS / "stage02_unit_sales_forecast.xlsx"

_DROP_MONTHS        = {"2025-04"}          # partial month (1 unit)
_EXCLUDE_PROVINCES  = {"Company"}          # internal showroom, not market demand


# ══════════════════════════════════════════════════════════════
# 1. Data loading
# ══════════════════════════════════════════════════════════════

def load_monthly_series() -> pd.DataFrame:
    """Return monthly unit-sales series as DataFrame[ds, y, month_str]."""
    df = pd.read_parquet(_CLEAN_PARQUET)
    df = df[~df["Province"].isin(_EXCLUDE_PROVINCES)]
    monthly = (
        df.groupby("Year_Month_str")["VIN"]
        .nunique()
        .reset_index()
        .rename(columns={"Year_Month_str": "month_str", "VIN": "y"})
    )
    monthly = monthly[~monthly["month_str"].isin(_DROP_MONTHS)].copy()
    monthly["ds"] = pd.to_datetime(monthly["month_str"] + "-01")
    monthly = monthly.sort_values("ds").reset_index(drop=True)
    logger.info(
        f"Monthly series: {len(monthly)} months "
        f"({monthly['ds'].min().strftime('%b-%Y')} → {monthly['ds'].max().strftime('%b-%Y')})"
    )
    return monthly[["ds", "y", "month_str"]]


# ══════════════════════════════════════════════════════════════
# 2. UIO calculation
# ══════════════════════════════════════════════════════════════

def calculate_uio() -> dict:
    """Calculate current Units in Operation from MCSI sold VINs.

    UIO = cumulative net sold motorcycles (sum(SlsVolQty) per VIN == 1).
    Note: reflects only the period covered by MCSI.xlsx.
    Returns summary dict + saves parquet to data/interim/.
    """
    df = pd.read_parquet(_CLEAN_PARQUET)

    total_uio = df["VIN"].nunique()

    # By model
    by_model = (
        df.drop_duplicates("VIN")
        .groupby("Model")["VIN"]
        .count()
        .reset_index()
        .rename(columns={"VIN": "UIO"})
        .sort_values("UIO", ascending=False)
    )
    by_model["UIO_pct"] = (by_model["UIO"] / total_uio * 100).round(1)

    # By province
    by_province = (
        df.drop_duplicates("VIN")
        .groupby("Province")["VIN"]
        .count()
        .reset_index()
        .rename(columns={"VIN": "UIO"})
        .sort_values("UIO", ascending=False)
    )
    by_province["UIO_pct"] = (by_province["UIO"] / total_uio * 100).round(1)

    # Monthly additions (cumulative)
    monthly_add = (
        df.groupby("Year_Month_str")["VIN"]
        .nunique()
        .reset_index()
        .rename(columns={"Year_Month_str": "month", "VIN": "monthly_additions"})
        .sort_values("month")
    )
    monthly_add["cumulative_uio"] = monthly_add["monthly_additions"].cumsum()

    summary = {
        "total_uio": int(total_uio),
        "data_from": str(df["Billing Date"].min().date()) if "Billing Date" in df.columns else "N/A",
        "data_to":   str(df["Billing Date"].max().date()) if "Billing Date" in df.columns else "N/A",
        "by_model":    by_model,
        "by_province": by_province,
        "monthly":     monthly_add,
    }

    # Persist for dashboard
    by_model.to_parquet(_UIO_PARQUET, index=False)
    logger.info(f"Current UIO: {total_uio:,} units across {by_model['Model'].nunique()} models")
    return summary


# ══════════════════════════════════════════════════════════════
# 3. Seasonality diagnostics
# ══════════════════════════════════════════════════════════════

@dataclass
class SeasonalityReport:
    n: int
    trend_slope: float
    trend_pvalue: float
    trend_r2: float
    has_significant_trend: bool
    ljungbox_pvalues: list[float]
    has_autocorrelation: bool
    adf_pvalue: float
    adf_reliable: bool                    # ADF unreliable if n < 20
    seasonal_period_detectable: bool      # False unless n >= 24
    seasonal_note: str
    recommended_model_family: str         # "trend" | "arima" | "seasonal" | "naive"
    model_rationale: str


def check_seasonality(series: pd.DataFrame) -> SeasonalityReport:
    """Run statistical tests and return a SeasonalityReport.

    Tests applied:
      - Linear trend significance (scipy linregress)
      - Autocorrelation (Ljung-Box lags 1-3)
      - Unit root (ADF, flagged unreliable if n < 20)
      - Seasonal period: requires n >= 2 × period
    """
    y = series["y"].values
    n = len(y)
    t = np.arange(n)

    # Trend test
    slope, intercept, r, p_trend, _ = stats.linregress(t, y)
    has_trend = bool(p_trend < 0.05)

    # Ljung-Box autocorrelation (up to lag 3, or n//2 if smaller)
    max_lag = min(3, n // 2)
    lb = acorr_ljungbox(y, lags=list(range(1, max_lag + 1)), return_df=True)
    lb_pvalues = lb["lb_pvalue"].tolist()
    has_ac = any(p < 0.05 for p in lb_pvalues)

    # ADF unit root
    try:
        adf_stat, adf_p, *_ = adfuller(y, autolag="AIC")
    except Exception:
        adf_p = float("nan")
    adf_reliable = n >= 20

    # Seasonal period = 12 (monthly data, yearly cycle)
    seasonal_detectable = n >= 24
    if not seasonal_detectable:
        seasonal_note = (
            f"Yearly seasonality (period=12) cannot be tested: "
            f"need ≥24 months, have {n}. "
            "No seasonal model will be fitted."
        )
    else:
        seasonal_note = "Sufficient data for seasonal decomposition."

    # Model family recommendation
    if not seasonal_detectable:
        if has_trend and not has_ac:
            family = "trend"
            rationale = (
                f"Significant linear trend (p={p_trend:.3f}, R²={r**2:.2f}), "
                f"no autocorrelation (Ljung-Box p>{min(lb_pvalues):.2f}). "
                "Using trend-based models (Linear Trend + Holt ETS + Prophet)."
            )
        elif has_ac:
            family = "arima"
            rationale = (
                f"Autocorrelation detected (Ljung-Box p<0.05). "
                "Adding ARIMA candidate alongside trend models."
            )
        else:
            family = "naive"
            rationale = "No significant trend or autocorrelation. Using naive + ETS."
    else:
        family = "seasonal"
        rationale = "Sufficient data — seasonal decomposition and SARIMA will be fitted."

    logger.info("─" * 55)
    logger.info("SEASONALITY DIAGNOSTIC")
    logger.info(f"  n = {n} months")
    logger.info(f"  Trend: slope={slope:.1f}, p={p_trend:.4f}, R²={r**2:.3f}  → {'SIGNIFICANT' if has_trend else 'not significant'}")
    logger.info(f"  Ljung-Box (lags 1-{max_lag}): p-values = {[round(p,4) for p in lb_pvalues]}  → {'autocorrelation' if has_ac else 'no autocorrelation'}")
    logger.info(f"  ADF: p={adf_p:.4f}  {'(unreliable, n<20)' if not adf_reliable else ''}")
    logger.info(f"  Seasonal: {seasonal_note}")
    logger.info(f"  → Recommendation: {family.upper()} models — {rationale}")
    logger.info("─" * 55)

    return SeasonalityReport(
        n=n,
        trend_slope=float(slope),
        trend_pvalue=float(p_trend),
        trend_r2=float(r ** 2),
        has_significant_trend=has_trend,
        ljungbox_pvalues=lb_pvalues,
        has_autocorrelation=has_ac,
        adf_pvalue=float(adf_p),
        adf_reliable=adf_reliable,
        seasonal_period_detectable=seasonal_detectable,
        seasonal_note=seasonal_note,
        recommended_model_family=family,
        model_rationale=rationale,
    )


# ══════════════════════════════════════════════════════════════
# 4. Models
# ══════════════════════════════════════════════════════════════

def _mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    mask = actual != 0
    if not mask.any():
        return float("inf")
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


def _fit_prophet(train: pd.DataFrame, horizon: int) -> pd.DataFrame:
    from prophet import Prophet
    m = Prophet(
        yearly_seasonality=False,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.3,
        interval_width=0.80,
    )
    m.fit(train[["ds", "y"]])
    future = m.make_future_dataframe(periods=horizon, freq="MS")
    fc = m.predict(future)
    return fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].rename(
        columns={"yhat": "forecast", "yhat_lower": "lower_80", "yhat_upper": "upper_80"}
    )


def _fit_holt_ets(train: pd.DataFrame, horizon: int) -> pd.DataFrame:
    model = ExponentialSmoothing(
        train["y"].values, trend="add", damped_trend=True, seasonal=None
    ).fit(optimized=True)
    fitted = model.fittedvalues
    fc_vals = model.forecast(horizon)
    fc_dates = pd.date_range(
        start=train["ds"].iloc[-1] + pd.DateOffset(months=1), periods=horizon, freq="MS"
    )
    all_ds   = list(train["ds"]) + list(fc_dates)
    all_yhat = np.concatenate([fitted, fc_vals])
    return pd.DataFrame({"ds": all_ds, "forecast": all_yhat})


def _fit_linear_trend(train: pd.DataFrame, horizon: int) -> pd.DataFrame:
    n = len(train)
    t = np.arange(1, n + 1)
    slope, intercept = np.polyfit(t, train["y"].values, 1)
    t_all = np.arange(1, n + horizon + 1)
    yhat = np.maximum(slope * t_all + intercept, 0)
    dates = pd.date_range(start=train["ds"].iloc[0], periods=n + horizon, freq="MS")
    return pd.DataFrame({"ds": dates, "forecast": yhat})


def _fit_arima(train: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Auto-ARIMA (non-seasonal) via pmdarima."""
    import pmdarima as pm
    model = pm.auto_arima(
        train["y"].values,
        seasonal=False,
        stepwise=True,
        suppress_warnings=True,
        error_action="ignore",
        max_p=3, max_q=2, max_d=2,
    )
    fc_vals, conf = model.predict(n_periods=horizon, return_conf_int=True, alpha=0.20)
    fitted = model.predict_in_sample()
    all_dates = list(train["ds"]) + list(
        pd.date_range(train["ds"].iloc[-1] + pd.DateOffset(months=1), periods=horizon, freq="MS")
    )
    all_fc    = list(fitted) + list(fc_vals)
    lower_all = [np.nan] * len(train) + list(conf[:, 0])
    upper_all = [np.nan] * len(train) + list(conf[:, 1])
    return pd.DataFrame({"ds": all_dates, "forecast": all_fc, "lower_80": lower_all, "upper_80": upper_all})


def _add_ci(fc: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    """Add 80 % CI columns to models that don't produce them natively."""
    if "lower_80" not in fc.columns:
        fitted_len = len(train)
        fitted_vals = fc.head(fitted_len)["forecast"].values
        resid_std = float(np.std(train["y"].values - fitted_vals))
        fc = fc.copy()
        fc["lower_80"] = fc["forecast"] - 1.28 * resid_std
        fc["upper_80"] = fc["forecast"] + 1.28 * resid_std
    return fc


# ══════════════════════════════════════════════════════════════
# 5. Model selection
# ══════════════════════════════════════════════════════════════

def _candidate_models(report: SeasonalityReport) -> list[str]:
    """Return list of models to evaluate based on diagnostics."""
    base = ["Linear Trend", "Holt ETS", "Prophet"]
    if report.has_autocorrelation or report.recommended_model_family == "arima":
        base.append("ARIMA")
    return base


def evaluate_models(
    series: pd.DataFrame, report: SeasonalityReport, holdout: int = 2
) -> dict[str, float]:
    """Holdout validation on last `holdout` months. Return MAPE per model."""
    n = len(series)
    if n < holdout + 2:
        raise ValueError(f"Need ≥{holdout + 2} months for holdout; got {n}.")
    train = series.iloc[:-holdout].copy()
    test  = series.iloc[-holdout:].copy()
    actual = test["y"].values

    candidates = _candidate_models(report)
    results: dict[str, float] = {}

    fitters = {
        "Prophet":      lambda: _fit_prophet(train, holdout),
        "Holt ETS":     lambda: _fit_holt_ets(train, holdout),
        "Linear Trend": lambda: _fit_linear_trend(train, holdout),
        "ARIMA":        lambda: _fit_arima(train, holdout),
    }

    for name in candidates:
        try:
            fc = fitters[name]()
            pred = fc.tail(holdout)["forecast"].values
            results[name] = _mape(actual, pred)
        except Exception as exc:
            logger.warning(f"  {name} failed: {exc}")
            results[name] = float("inf")

    logger.info("Holdout MAPE (last %d months):", holdout)
    for name, mape in sorted(results.items(), key=lambda x: x[1]):
        marker = " ← best" if mape == min(results.values()) else ""
        logger.info(f"  {name:<15}: {mape:6.1f}%{marker}")

    return results


def generate_forecast(
    series: pd.DataFrame, best_model: str, horizon: int
) -> pd.DataFrame:
    """Refit best model on all data → horizon-month forecast."""
    fitters = {
        "Prophet":      lambda: _fit_prophet(series, horizon),
        "Holt ETS":     lambda: _fit_holt_ets(series, horizon),
        "Linear Trend": lambda: _fit_linear_trend(series, horizon),
        "ARIMA":        lambda: _fit_arima(series, horizon),
    }
    fc = fitters[best_model]()
    fc = _add_ci(fc, series)
    fc["lower_80"] = fc["lower_80"].clip(lower=0)
    fc["upper_80"] = fc["upper_80"].clip(lower=0)
    fc["forecast"] = fc["forecast"].clip(lower=0).round().astype(int)
    fc["lower_80"] = fc["lower_80"].round().astype(int)
    fc["upper_80"] = fc["upper_80"].round().astype(int)
    return fc


def build_combined_table(series: pd.DataFrame, fc: pd.DataFrame) -> pd.DataFrame:
    actuals  = series[["ds", "y"]].rename(columns={"y": "actual"})
    combined = fc[["ds", "forecast", "lower_80", "upper_80"]].merge(actuals, on="ds", how="left")
    combined["period"]      = combined["ds"].dt.to_period("M").astype(str)
    combined["is_forecast"] = combined["actual"].isna()
    return combined.sort_values("ds").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 6. Target management
# ══════════════════════════════════════════════════════════════

def _load_targets() -> dict:
    if _TARGETS_JSON.exists():
        with open(_TARGETS_JSON) as f:
            return json.load(f)
    return {}


def _save_targets(targets: dict) -> None:
    with open(_TARGETS_JSON, "w") as f:
        json.dump(targets, f, indent=2)


def set_annual_target(year: int, annual_units: int) -> None:
    """Set or update the annual unit target for `year`."""
    targets = _load_targets()
    if str(year) not in targets:
        targets[str(year)] = {"annual": annual_units, "monthly_override": {}}
    else:
        targets[str(year)]["annual"] = annual_units
    _save_targets(targets)
    logger.info(f"Annual target set: {year} → {annual_units:,} units")


def set_monthly_target(year: int, month: int, units: int) -> None:
    """Override the target for a specific month.

    Args:
        year:  Calendar year (e.g. 2026).
        month: Month number 1-12.
        units: Target unit sales for that month.
    """
    targets = _load_targets()
    key = f"{year}-{month:02d}"
    if str(year) not in targets:
        targets[str(year)] = {"annual": 0, "monthly_override": {}}
    targets[str(year)]["monthly_override"][key] = units
    _save_targets(targets)
    logger.info(f"Monthly override: {key} → {units:,} units")


def clear_monthly_target(year: int, month: int) -> None:
    """Remove a monthly override (reverts to auto-distributed value)."""
    targets = _load_targets()
    key = f"{year}-{month:02d}"
    if str(year) in targets:
        targets[str(year)]["monthly_override"].pop(key, None)
        _save_targets(targets)
        logger.info(f"Monthly override cleared: {key}")


def _distribute_annual(annual: int, months: list[str], fc_vals: list[int]) -> dict[str, int]:
    """Distribute annual target proportionally to forecast seasonal weights."""
    total = sum(fc_vals) or 1
    return {m: round(annual * v / total) for m, v in zip(months, fc_vals)}


def attach_targets(combined: pd.DataFrame, forecast_year: int) -> pd.DataFrame:
    """Add target and gap columns to the combined table."""
    targets  = _load_targets()
    year_cfg = targets.get(str(forecast_year), {})
    annual   = year_cfg.get("annual", 0)
    overrides = year_cfg.get("monthly_override", {})

    fc_rows   = combined[combined["is_forecast"]]
    fc_months = list(fc_rows["period"])
    fc_values = list(fc_rows["forecast"])

    distributed = _distribute_annual(annual, fc_months, fc_values) if annual > 0 else {}
    distributed.update(overrides)   # per-month overrides win

    combined = combined.copy()
    combined["target"] = combined["period"].map(distributed)

    # Auto-calculate monthly from annual for months without override
    combined["target_source"] = combined["period"].apply(
        lambda p: "override" if p in overrides else ("auto" if p in distributed else "—")
    )
    combined["target_gap"] = combined.apply(
        lambda r: (int(r["target"]) - int(r["forecast"]))
        if r["is_forecast"] and pd.notna(r["target"]) else None,
        axis=1,
    )
    return combined


def get_target_summary(year: int) -> dict:
    """Return a summary of annual and monthly targets for `year`."""
    targets = _load_targets()
    return targets.get(str(year), {"annual": 0, "monthly_override": {}})


# ── Public API aliases (used by tests and external callers) ───

def load_targets() -> dict:
    return _load_targets()


def save_targets(targets: dict) -> None:
    _save_targets(targets)


def distribute_annual_target(annual: int, months: list[str], fc_vals: list[int]) -> dict[str, int]:
    return _distribute_annual(annual, months, fc_vals)


def fit_holt_ets(series: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Public wrapper: returns DataFrame[ds, yhat] covering actuals + horizon."""
    fc = _fit_holt_ets(series, horizon)
    return fc.rename(columns={"forecast": "yhat"})


def fit_linear_trend(series: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Public wrapper: returns DataFrame[ds, yhat] covering actuals + horizon."""
    fc = _fit_linear_trend(series, horizon)
    return fc.rename(columns={"forecast": "yhat"})


# ══════════════════════════════════════════════════════════════
# 7. Excel report
# ══════════════════════════════════════════════════════════════

def _wb_fmts(wb: xlsxwriter.Workbook) -> dict:
    return {
        "title":   wb.add_format({"bold": True, "font_size": 13, "font_color": "#003087"}),
        "sub":     wb.add_format({"italic": True, "font_color": "#555555"}),
        "warn":    wb.add_format({"bold": True, "font_color": "#CC0000"}),
        "hdr":     wb.add_format({"bold": True, "bg_color": "#003087", "font_color": "white", "border": 1, "align": "center"}),
        "num":     wb.add_format({"num_format": "#,##0", "border": 1}),
        "pct":     wb.add_format({"num_format": "0.00", "border": 1}),
        "cell":    wb.add_format({"border": 1}),
        "fc":      wb.add_format({"num_format": "#,##0", "border": 1, "bg_color": "#FFF3E0"}),
        "green":   wb.add_format({"num_format": "#,##0", "border": 1, "bg_color": "#E8F5E9"}),
        "red_num": wb.add_format({"num_format": "#,##0", "border": 1, "font_color": "#CC0000"}),
        "ok_num":  wb.add_format({"num_format": "#,##0", "border": 1, "font_color": "#1B7E24"}),
    }


def _write_df(ws: Any, df: pd.DataFrame, fmts: dict, row0: int = 1) -> None:
    for c, col in enumerate(df.columns):
        ws.write(row0 - 1, c, col, fmts["hdr"])
    for r, record in enumerate(df.itertuples(index=False), start=row0):
        for c, val in enumerate(record):
            if pd.isna(val) or val is None:
                ws.write(r, c, "", fmts["cell"])
            elif isinstance(val, (int, float, np.integer, np.floating)):
                ws.write_number(r, c, float(val), fmts["num"])
            else:
                ws.write(r, c, str(val), fmts["cell"])
    for c, col in enumerate(df.columns):
        w = max(len(str(col)), df[col].astype(str).str.len().max()) + 2
        ws.set_column(c, c, min(w, 24))


def write_excel_report(
    combined: pd.DataFrame,
    mape_results: dict[str, float],
    best_model: str,
    report: SeasonalityReport,
    uio: dict,
    annual_target: int,
    forecast_year: int,
) -> None:
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))
    F = _wb_fmts(wb)

    # ── Sheet 1: Forecast ────────────────────────────────────
    ws1 = wb.add_worksheet("Forecast")
    ws1.write(0, 0, "Stage 2 — Motorcycle Unit Sales Forecast", F["title"])
    ws1.write(1, 0, f"Model: {best_model}  |  {CURRENCY}  |  Orange = forecast period", F["sub"])
    if annual_target > 0:
        ws1.write(2, 0, f"Annual target {forecast_year}: {annual_target:,} units (auto-distributed monthly unless overridden)", F["sub"])

    cols   = ["period", "actual", "forecast", "lower_80", "upper_80", "target", "target_source", "target_gap"]
    labels = ["Month", "Actual", "Forecast", "Lower 80%", "Upper 80%", "Monthly Target", "Source", "Gap (Target−Forecast)"]
    for c, lbl in enumerate(labels):
        ws1.write(4, c, lbl, F["hdr"])

    for r, row in enumerate(combined[cols].itertuples(index=False), start=5):
        period, actual, forecast, lower, upper, target, src, gap = row
        is_fc = pd.isna(actual)
        rf = F["fc"] if is_fc else F["num"]
        ws1.write(r, 0, str(period), rf)
        ws1.write(r, 1, "" if pd.isna(actual) else int(actual), rf)
        ws1.write_number(r, 2, int(forecast), rf)
        ws1.write_number(r, 3, int(lower), rf)
        ws1.write_number(r, 4, int(upper), rf)
        ws1.write(r, 5, "" if pd.isna(target) else int(target), rf)
        ws1.write(r, 6, str(src) if pd.notna(src) else "—", F["cell"])
        if pd.notna(gap):
            ws1.write_number(r, 7, int(gap), F["ok_num"] if int(gap) >= 0 else F["red_num"])
        else:
            ws1.write(r, 7, "", F["cell"])

    # Totals
    fc_rows   = combined[combined["is_forecast"]]
    total_row = 5 + len(combined) + 1
    ws1.write(total_row, 0, f"Forecast Total ({forecast_year})", F["hdr"])
    ws1.write_number(total_row, 2, int(fc_rows["forecast"].sum()), F["green"])
    ws1.write_number(total_row, 3, int(fc_rows["lower_80"].sum()), F["green"])
    ws1.write_number(total_row, 4, int(fc_rows["upper_80"].sum()), F["green"])
    if annual_target > 0:
        ws1.write_number(total_row, 5, annual_target, F["green"])
        gap_total = annual_target - int(fc_rows["forecast"].sum())
        ws1.write_number(total_row, 7, gap_total, F["ok_num"] if gap_total >= 0 else F["red_num"])

    ws1.write(total_row + 2, 0, "⚠ Only 9 months of history. Forecast confidence is limited. Use annual target as primary plan.", F["warn"])

    for c, w in enumerate([12, 10, 10, 10, 10, 14, 10, 22]):
        ws1.set_column(c, c, w)

    # Chart
    n_act = int(combined["actual"].notna().sum())
    n_tot = len(combined)
    chart = wb.add_chart({"type": "column"})
    chart.add_series({"name": "Actual",   "categories": ["Forecast", 5, 0, 4 + n_act, 0],       "values": ["Forecast", 5, 1, 4 + n_act, 1],       "fill": {"color": "#003087"}})
    chart.add_series({"name": "Forecast", "categories": ["Forecast", 5 + n_act, 0, 4 + n_tot, 0], "values": ["Forecast", 5 + n_act, 2, 4 + n_tot, 2], "fill": {"color": "#FF8C00"}})
    if annual_target > 0:
        chart.add_series({"name": "Target", "categories": ["Forecast", 5 + n_act, 0, 4 + n_tot, 0], "values": ["Forecast", 5 + n_act, 5, 4 + n_tot, 5], "type": "line", "line": {"color": "#CC0000", "dash_type": "dash", "width": 2}})
    chart.set_title({"name": "Monthly Unit Sales — Actuals & 12-Month Forecast"})
    chart.set_x_axis({"name": "Month"})
    chart.set_y_axis({"name": "Units"})
    chart.set_size({"width": 720, "height": 350})
    ws1.insert_chart("J5", chart)

    # ── Sheet 2: Seasonality Diagnostics ─────────────────────
    ws2 = wb.add_worksheet("Seasonality Diagnostics")
    ws2.set_column(0, 1, 38)
    ws2.write(0, 0, "Seasonality & Stationarity Diagnostics", F["title"])
    tests = [
        ("Data points (months)", report.n),
        ("Linear trend slope (units/month)", round(report.trend_slope, 1)),
        ("Trend significance p-value", round(report.trend_pvalue, 4)),
        ("Trend R²", round(report.trend_r2, 3)),
        ("Significant trend detected?", "YES" if report.has_significant_trend else "NO"),
        ("Ljung-Box p-value (lag 1)", round(report.ljungbox_pvalues[0], 4)),
        ("Significant autocorrelation?", "YES" if report.has_autocorrelation else "NO"),
        ("ADF p-value", round(report.adf_pvalue, 4) if not np.isnan(report.adf_pvalue) else "N/A"),
        ("ADF test reliable?", "NO (n<20, use with caution)" if not report.adf_reliable else "YES"),
        ("Yearly seasonality detectable?", "YES" if report.seasonal_period_detectable else "NO"),
        ("Seasonality note", report.seasonal_note),
        ("Recommended model family", report.recommended_model_family.upper()),
        ("Rationale", report.model_rationale),
    ]
    for r, (label, val) in enumerate(tests, start=2):
        ws2.write(r, 0, label, F["cell"])
        ws2.write(r, 1, str(val), F["cell"])

    # Model comparison
    ws2.write(len(tests) + 4, 0, "Model Holdout Comparison (last 2 months held out)", F["title"])
    ws2.write(len(tests) + 5, 0, "Model",         F["hdr"])
    ws2.write(len(tests) + 5, 1, "Holdout MAPE %", F["hdr"])
    ws2.write(len(tests) + 5, 2, "Selected",       F["hdr"])
    ws2.set_column(2, 2, 10)
    for r, (model, mape) in enumerate(sorted(mape_results.items(), key=lambda x: x[1]), start=len(tests) + 6):
        ws2.write(r, 0, model, F["cell"])
        ws2.write_number(r, 1, round(mape, 1), F["pct"])
        ws2.write(r, 2, "✓" if model == best_model else "", F["cell"])

    # ── Sheet 3: UIO Summary ─────────────────────────────────
    ws3 = wb.add_worksheet("UIO Summary")
    ws3.write(0, 0, "Units in Operation (UIO) — Calculated from MCSI Sales Data", F["title"])
    ws3.write(1, 0, f"Total current UIO: {uio['total_uio']:,} units  |  Period: {uio['data_from']} to {uio['data_to']}", F["sub"])
    ws3.write(2, 0, "Note: reflects bikes sold since data start date only. Older fleet not included unless additional files are provided.", F["warn"])

    ws3.write(4, 0, "UIO by Model", F["title"])
    _write_df(ws3, uio["by_model"].rename(columns={"UIO": "UIO (units)", "UIO_pct": "Share %"}), F, row0=6)

    offset = 6 + len(uio["by_model"]) + 3
    ws3.write(offset, 0, "UIO by Province", F["title"])
    _write_df(ws3, uio["by_province"].rename(columns={"UIO": "UIO (units)", "UIO_pct": "Share %"}), F, row0=offset + 2)

    offset2 = offset + 2 + len(uio["by_province"]) + 3
    ws3.write(offset2, 0, "Monthly UIO Additions (Cumulative)", F["title"])
    _write_df(ws3, uio["monthly"].rename(columns={"month": "Month", "monthly_additions": "New Units", "cumulative_uio": "Cumulative UIO"}), F, row0=offset2 + 2)

    # ── Sheet 4: Target Settings ─────────────────────────────
    ws4 = wb.add_worksheet("Target Settings")
    ws4.write(0, 0, "Annual & Monthly Target Configuration", F["title"])
    ws4.write(1, 0, "Green = auto-distributed from annual.  Orange = manually overridden.", F["sub"])
    ws4.write(2, 0, "To override a month: run set_monthly_target(year, month, units)", F["sub"])

    targets = _load_targets()
    row = 4
    for yr, cfg in targets.items():
        annual_t = cfg.get("annual", 0)
        ws4.write(row, 0, f"Year {yr}  —  Annual Target: {annual_t:,} units", F["hdr"])
        row += 1
        ws4.write(row, 0, "Month", F["hdr"])
        ws4.write(row, 1, "Monthly Target", F["hdr"])
        ws4.write(row, 2, "Source", F["hdr"])
        ws4.write(row, 3, "Forecast", F["hdr"])
        row += 1
        overrides  = cfg.get("monthly_override", {})
        fc_by_month = dict(zip(combined["period"], combined["forecast"]))
        fc_months = [p for p in combined[combined["is_forecast"]]["period"]]
        fc_vals   = [int(combined.loc[combined["period"] == p, "forecast"].values[0]) for p in fc_months]
        auto_dist = _distribute_annual(annual_t, fc_months, fc_vals) if annual_t > 0 else {}
        auto_dist.update(overrides)
        for m in sorted(auto_dist.keys()):
            is_override = m in overrides
            fmt = F["fc"] if is_override else F["green"]
            ws4.write(row, 0, m, F["cell"])
            ws4.write_number(row, 1, auto_dist[m], fmt)
            ws4.write(row, 2, "Manual override" if is_override else "Auto (proportional)", F["cell"])
            ws4.write(row, 3, fc_by_month.get(m, ""), F["num"])
            row += 1
        row += 2

    if not targets:
        ws4.write(4, 0, "No targets set. Call set_annual_target(2026, 40000) first.", F["warn"])

    ws4.set_column(0, 3, 26)

    wb.close()
    logger.info(f"Excel report: {_OUTPUT_EXCEL}")


# ══════════════════════════════════════════════════════════════
# 8. Main run
# ══════════════════════════════════════════════════════════════

def run(
    refresh: bool = False,
    annual_target: int = 0,
    forecast_year: int = 2026,
    horizon: int = 12,
) -> pd.DataFrame:
    """Execute Stage 2 end-to-end.

    Args:
        refresh:       Force re-run ignoring cache.
        annual_target: Annual unit target for forecast_year (0 = no target).
        forecast_year: Year being forecast (default 2026).
        horizon:       Months ahead to forecast (default 12).
    """
    if not refresh and _FORECAST_PARQUET.exists():
        logger.info("Stage 2 cached. Pass refresh=True to recompute.")
        return pd.read_parquet(_FORECAST_PARQUET)

    # Step 1: Load
    series = load_monthly_series()

    # Step 2: UIO
    uio = calculate_uio()

    # Step 3: Seasonality diagnostics → model family
    diag = check_seasonality(series)

    # Step 4: Holdout evaluation on diagnostic-selected candidates
    mape_results = evaluate_models(series, diag)
    best_model   = min(mape_results, key=mape_results.get)
    logger.info(f"Winner: {best_model}  (MAPE {mape_results[best_model]:.1f}%)")

    # Step 5: Final forecast on full series
    fc       = generate_forecast(series, best_model, horizon)
    combined = build_combined_table(series, fc)

    # Step 6: Targets
    if annual_target > 0:
        set_annual_target(forecast_year, annual_target)
    combined = attach_targets(combined, forecast_year)

    # Step 7: Save
    combined.to_parquet(_FORECAST_PARQUET, index=False)

    # Step 8: Console summary
    fc_rows = combined[combined["is_forecast"]]
    logger.info("=" * 55)
    logger.info("STAGE 2 — UNIT SALES FORECAST SUMMARY")
    logger.info("=" * 55)
    logger.info(f"  Best model        : {best_model}  (MAPE {mape_results[best_model]:.1f}%)")
    logger.info(f"  Trend             : slope={diag.trend_slope:.1f} units/month, p={diag.trend_pvalue:.4f}")
    logger.info(f"  Seasonality       : {diag.seasonal_note}")
    logger.info(f"  Forecast total    : {fc_rows['forecast'].sum():,} units ({forecast_year})")
    if annual_target > 0:
        gap = annual_target - int(fc_rows["forecast"].sum())
        logger.info(f"  Annual target     : {annual_target:,}  |  Gap: {gap:+,} ({gap/annual_target*100:+.1f}%)")
    logger.info(f"\n  Current UIO       : {uio['total_uio']:,} units (from {uio['data_from']} to {uio['data_to']})")
    logger.info("\n  Monthly forecast:")
    for _, row in fc_rows.iterrows():
        tgt_str = f"  target {int(row['target']):,}  gap {int(row['target_gap']):+,}" if pd.notna(row.get("target")) else ""
        logger.info(f"    {row['period']}  {row['forecast']:>6,}  [{row['lower_80']:,}–{row['upper_80']:,}]{tgt_str}")

    # Step 9: Excel report
    write_excel_report(combined, mape_results, best_model, diag, uio, annual_target, forecast_year)

    logger.info("Stage 2 complete.")
    return combined
