"""Module 1: Vehicle Intelligence.

Responsibilities:
  - Motorcycle sales forecast (wraps stage02)
  - UIO forecast (wraps stage03)
  - Vehicle age distribution (new: derives age cohorts from MCSI VIN + billing date)

Design: wrap-and-promote.  Existing stage logic is unchanged.  This module
calls stage02 and stage03 as sub-routines, transforms their outputs into
module-standard DataFrames, and adds the new age-distribution computation.

Outputs (returned in VehicleIntelligenceResult):
  sales_forecast   : pd.DataFrame  [month, model, forecast_units, lower_ci, upper_ci]
  uio_forecast     : pd.DataFrame  [month, model, uio_forecast]
  age_distribution : pd.DataFrame  [model, age_cohort, vehicle_count, pct_of_fleet]

Upstream parquet dependencies (must exist before calling run()):
  data/interim/mcsi_clean.parquet         — stage01 output, sold VINs only
  data/interim/mcsi_uio_summary.parquet   — stage02 output, current UIO by model
  data/interim/unit_sales_forecast.parquet — stage02 output, 12-month sales forecast
  data/interim/uio_forecast.parquet        — stage03 output, fleet projection
  data/interim/mcsi.parquet               — cleaner output, all MCSI rows
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from loguru import logger

from src.config.constants import TIMEZONE
from src.config.paths import DATA_INTERIM
from src.ingestion.cleaner import load_clean

# ---------------------------------------------------------------------------
# Age cohort bins (right-closed intervals, in months)
# ---------------------------------------------------------------------------
# (0, 12]   → new fleet:     parts demand low, mainly consumables
# (12, 24]  → maturing fleet: routine service parts
# (24, 36]  → mid-life:       wear-item replacement begins
# (36, 60]  → aging fleet:    higher demand across all part categories
# (60, inf] → old fleet:      high demand, sourcing risk for discontinued parts

AGE_COHORT_BINS: list[float] = [0.0, 12.0, 24.0, 36.0, 60.0, float("inf")]
AGE_COHORT_LABELS: list[str] = [
    "0-12 months",
    "13-24 months",
    "25-36 months",
    "37-60 months",
    "60+ months",
]

# Days per month (average, Gregorian)
_DAYS_PER_MONTH: float = 30.4375


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class VehicleIntelligenceResult:
    """Typed container for all Module 1 outputs.

    Attributes:
        sales_forecast:    Monthly unit sales forecast per model, 12 months ahead.
        uio_forecast:      Monthly UIO forecast per model, 12 months ahead.
        age_distribution:  Current fleet broken down by model and age cohort.
        reference_date:    Date used as "today" for age calculations.
        metadata:          Run diagnostics (row counts, timings, warnings).
    """

    sales_forecast: pd.DataFrame
    uio_forecast: pd.DataFrame
    age_distribution: pd.DataFrame
    reference_date: pd.Timestamp
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class VehicleIntelligence:
    """Module 1: wraps stage02 (sales forecast) + stage03 (UIO forecast)
    and adds vehicle age cohort analysis.

    Args:
        reference_date: The "today" anchor for age calculations.
            Defaults to current date in Asia/Colombo timezone, tz-naive
            (times are stripped — only the date matters for age in months).
    """

    def __init__(self, reference_date: pd.Timestamp | None = None) -> None:
        if reference_date is None:
            # Today at midnight in Asia/Colombo, stored tz-naive
            reference_date = pd.Timestamp.now(tz=TIMEZONE).normalize().tz_localize(None)
        self.reference_date: pd.Timestamp = reference_date
        logger.info(f"VehicleIntelligence: reference_date = {self.reference_date.date()}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> VehicleIntelligenceResult:
        """Run all three sub-components and return a typed result.

        Returns:
            VehicleIntelligenceResult with sales_forecast, uio_forecast,
            age_distribution DataFrames plus run metadata.

        Raises:
            FileNotFoundError: If any required upstream parquet is missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "reference_date": str(self.reference_date.date()),
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 1: VEHICLE INTELLIGENCE")
        logger.info("=" * 60)

        # ── Sub-component 1: sales forecast ───────────────────────
        logger.info("Sub-component 1/3: sales forecast (stage02)")
        t1 = time.perf_counter()
        sales_forecast = self._forecast_sales()
        metadata["sales_forecast_rows"] = len(sales_forecast)
        metadata["sales_forecast_months"] = int(sales_forecast["month"].nunique()) if not sales_forecast.empty else 0
        metadata["sales_forecast_models"] = int(sales_forecast["model"].nunique()) if not sales_forecast.empty else 0
        logger.info(f"  -> {sales_forecast.shape[0]} rows in {time.perf_counter() - t1:.2f}s")

        # ── Sub-component 2: UIO forecast ─────────────────────────
        logger.info("Sub-component 2/3: UIO forecast (stage03)")
        t2 = time.perf_counter()
        uio_forecast = self._forecast_uio()
        metadata["uio_forecast_rows"] = len(uio_forecast)
        metadata["uio_forecast_months"] = int(uio_forecast["month"].nunique()) if not uio_forecast.empty else 0
        metadata["uio_forecast_models"] = int(uio_forecast["model"].nunique()) if not uio_forecast.empty else 0
        logger.info(f"  -> {uio_forecast.shape[0]} rows in {time.perf_counter() - t2:.2f}s")

        # ── Sub-component 3: age distribution ─────────────────────
        logger.info("Sub-component 3/3: vehicle age distribution (new)")
        t3 = time.perf_counter()
        age_distribution = self._calc_age_distribution()
        metadata["age_distribution_rows"] = len(age_distribution)
        metadata["age_distribution_models"] = int(age_distribution["model"].nunique()) if not age_distribution.empty else 0
        logger.info(f"  -> {age_distribution.shape[0]} rows in {time.perf_counter() - t3:.2f}s")

        # ── Summary ───────────────────────────────────────────────
        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info("MODULE 1 COMPLETE")
        logger.info(f"  Sales forecast   : {sales_forecast.shape}")
        logger.info(f"  UIO forecast     : {uio_forecast.shape}")
        logger.info(f"  Age distribution : {age_distribution.shape}")
        logger.info(f"  Total time       : {elapsed:.2f}s")
        logger.info("=" * 60)

        return VehicleIntelligenceResult(
            sales_forecast=sales_forecast,
            uio_forecast=uio_forecast,
            age_distribution=age_distribution,
            reference_date=self.reference_date,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Sub-component 1: sales forecast
    # ------------------------------------------------------------------

    def _forecast_sales(self) -> pd.DataFrame:
        """Wrap stage02 to produce per-model monthly sales forecast.

        Business meaning: stage02 forecasts total monthly unit sales across
        all models.  This method distributes that total by the historical
        model-mix (share of each model in the current fleet) to give a
        per-model breakdown.  The model mix is derived from
        ``mcsi_uio_summary.parquet`` (stage02 output).

        Returns:
            DataFrame with columns:
                month          (pd.Timestamp, first day of month)
                model          (str)
                forecast_units (int, point estimate)
                lower_ci       (int, 80 % lower bound)
                upper_ci       (int, 80 % upper bound)

        Raises:
            FileNotFoundError: If ``mcsi_clean.parquet`` or
                ``unit_sales_forecast.parquet`` are missing.
        """
        import src.models.unit_sales_forecast.stage02_unit_sales_forecast as s02

        logger.debug("  Calling stage02.run() (will use cache if available)")
        combined = s02.run()

        fc = combined[combined["is_forecast"]].copy()
        if fc.empty:
            logger.warning("stage02 returned no forecast rows — returning empty DataFrame")
            metadata_warn = "stage02 produced zero forecast rows"
            return pd.DataFrame(columns=["month", "model", "forecast_units", "lower_ci", "upper_ci"])

        logger.debug(f"  stage02 forecast: {len(fc)} months")

        # Model mix from current UIO by model
        model_mix = self._get_model_mix_from_uio_summary()
        if not model_mix:
            logger.warning("No model mix available — returning aggregate forecast only")
            result = fc[["ds", "forecast", "lower_80", "upper_80"]].copy()
            result = result.rename(columns={
                "ds": "month",
                "forecast": "forecast_units",
                "lower_80": "lower_ci",
                "upper_80": "upper_ci",
            })
            result["model"] = "All Models"
            return result[["month", "model", "forecast_units", "lower_ci", "upper_ci"]]

        # Distribute total forecast by model share
        records: list[dict[str, Any]] = []
        for _, row in fc.iterrows():
            total_fc = float(row["forecast"])
            total_lo = float(row["lower_80"])
            total_hi = float(row["upper_80"])
            for model, share in model_mix.items():
                records.append({
                    "month": row["ds"],
                    "model": model,
                    "forecast_units": max(0, round(total_fc * share)),
                    "lower_ci": max(0, round(total_lo * share)),
                    "upper_ci": max(0, round(total_hi * share)),
                })

        result = (
            pd.DataFrame(records)
            .sort_values(["month", "model"])
            .reset_index(drop=True)
        )
        logger.info(
            f"Sales forecast: {result['month'].nunique()} months "
            f"x {result['model'].nunique()} models = {len(result)} rows"
        )
        return result

    def _get_model_mix_from_uio_summary(self) -> dict[str, float]:
        """Derive model-mix proportions from ``mcsi_uio_summary.parquet``.

        Business meaning: each model's share of total current UIO determines
        how monthly new-sales forecasts are allocated across models.

        Returns:
            Dict mapping model name → fractional share (0–1, sums to 1.0).

        Raises:
            FileNotFoundError: If ``mcsi_uio_summary.parquet`` is missing.
        """
        parquet = DATA_INTERIM / "mcsi_uio_summary.parquet"
        if not parquet.exists():
            raise FileNotFoundError(
                f"mcsi_uio_summary.parquet not found at {parquet}. "
                "Run stage02 first: python -m scripts.run_stage 2"
            )
        uio = pd.read_parquet(parquet)
        total = int(uio["UIO"].sum())
        if total == 0:
            return {}
        mix = {str(row["Model"]): int(row["UIO"]) / total for _, row in uio.iterrows()}
        logger.debug(f"  Model mix: {len(mix)} models, total UIO = {total:,}")
        return mix

    # ------------------------------------------------------------------
    # Sub-component 2: UIO forecast
    # ------------------------------------------------------------------

    def _forecast_uio(self) -> pd.DataFrame:
        """Wrap stage03 to produce per-model monthly UIO forecast.

        Business meaning: stage03 projects the total fleet forward using a
        stock-flow model (new sales in, attrition out).  This method calls
        stage03's ``project_uio_by_model`` helper to break that projection
        down by model using the current model mix.

        Returns:
            DataFrame with columns:
                month        (pd.Timestamp, first day of month)
                model        (str)
                uio_forecast (int)

        Raises:
            FileNotFoundError: If upstream parquets are missing.
        """
        import src.models.uio_forecast.stage03_uio_forecast as s03

        logger.debug("  Calling stage03.run() (will use cache if available)")
        s03.run()  # ensures uio_forecast.parquet exists; returns None when cached

        # Re-compute model-level projection using stage03's helpers
        current_uio = s03.load_current_uio()
        fc_df = s03.load_sales_forecast()   # forecast-only rows with period + forecast cols
        model_mix = s03.get_model_mix(current_uio)

        monthly_sales_fc = list(fc_df["forecast"])
        model_proj = s03.project_uio_by_model(current_uio, monthly_sales_fc, model_mix)

        # Map 0-based period_idx to actual month timestamps
        fc_periods: list[str] = list(fc_df["period"])
        model_proj = model_proj.copy()
        model_proj["month"] = model_proj["period_idx"].apply(
            lambda i: pd.to_datetime(fc_periods[i] + "-01")
            if i < len(fc_periods)
            else pd.NaT
        )

        result = (
            model_proj[["month", "Model", "UIO"]]
            .rename(columns={"Model": "model", "UIO": "uio_forecast"})
            .sort_values(["month", "model"])
            .reset_index(drop=True)
        )

        logger.info(
            f"UIO forecast: {result['month'].nunique()} months "
            f"x {result['model'].nunique()} models = {len(result)} rows"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-component 3: vehicle age distribution
    # ------------------------------------------------------------------

    def _calc_age_distribution(self) -> pd.DataFrame:
        """Compute age cohort breakdown of the current sold fleet.

        Business meaning: older vehicles generate higher spare-parts demand.
        Age cohorts allow Module 2 (UIO-based demand estimation) to weight
        spare-parts consumption rates by fleet age — a 60+ month vehicle
        requires far more replacement parts than a 0-12 month vehicle.

        Applies the CLAUDE.md business rule:
            groupby(VIN).SlsVolQty.sum() == 1  →  sold
            groupby(VIN).SlsVolQty.sum() == 0  →  returned

        Age in months = (reference_date − Billing Date of sale).days / 30.4375

        Age cohort bins (right-closed):
            (0, 12]   0-12 months
            (12, 24]  13-24 months
            (24, 36]  25-36 months
            (36, 60]  37-60 months
            (60, inf] 60+ months

        Args:
            (uses self.reference_date)

        Returns:
            DataFrame with columns:
                model          (str)
                age_cohort     (Categorical, ordered)
                vehicle_count  (int)
                pct_of_fleet   (float, percentage of total analysed fleet)

        Raises:
            KeyError:           If ``VIN``, ``SlsVolQty``, ``Billing Date``,
                                or ``Model`` columns are absent.
            FileNotFoundError:  If ``mcsi.parquet`` (cleaner cache) is missing.
        """
        mcsi = load_clean("mcsi")

        # ── Validate required columns ──────────────────────────────
        required = {"VIN", "SlsVolQty", "Billing Date", "Model"}
        missing_cols = required - set(mcsi.columns)
        if missing_cols:
            raise KeyError(
                f"mcsi.parquet is missing required columns: {sorted(missing_cols)}. "
                "Re-run the data cleaner: python -m scripts.clean_data"
            )

        # ── Business rule: identify sold VINs ─────────────────────
        # Coerce SlsVolQty to numeric (cleaner may preserve original dtype)
        mcsi = mcsi.copy()
        mcsi["SlsVolQty"] = pd.to_numeric(mcsi["SlsVolQty"], errors="coerce").fillna(0)

        vin_sums = mcsi.groupby("VIN")["SlsVolQty"].sum()
        sold_vins: set[str] = set(vin_sums[vin_sums == 1].index.astype(str))
        returned_vins: set[str] = set(vin_sums[vin_sums == 0].index.astype(str))

        logger.debug(
            f"  VIN status: {len(sold_vins):,} sold, "
            f"{len(returned_vins):,} returned, "
            f"{len(vin_sums) - len(sold_vins) - len(returned_vins):,} other"
        )

        # ── Extract sale-date row for each sold VIN ────────────────
        # The sale transaction is the row where SlsVolQty > 0 for a sold VIN.
        # drop_duplicates ensures one row per VIN in case of data anomalies.
        mcsi["VIN_str"] = mcsi["VIN"].astype(str)
        sold_rows = (
            mcsi[
                mcsi["VIN_str"].isin(sold_vins)
                & (mcsi["SlsVolQty"] > 0)
            ]
            .drop_duplicates("VIN_str", keep="first")
            .copy()
        )

        logger.debug(f"  Sale rows after dedup: {len(sold_rows):,}")

        # ── Parse Billing Date ─────────────────────────────────────
        # Raw MCSI stores Billing Date as "DD.MM.YYYY" strings.
        # The cleaner preserves them as object dtype.
        if sold_rows["Billing Date"].dtype == "object":
            sold_rows["billing_date_parsed"] = pd.to_datetime(
                sold_rows["Billing Date"], dayfirst=True, errors="coerce"
            )
        else:
            sold_rows["billing_date_parsed"] = pd.to_datetime(
                sold_rows["Billing Date"], errors="coerce"
            )

        # Drop rows where date could not be parsed
        unparsed = sold_rows["billing_date_parsed"].isna().sum()
        if unparsed > 0:
            logger.warning(
                f"  {unparsed:,} sold VINs had unparseable Billing Date — excluded from age analysis"
            )
        sold_rows = sold_rows.dropna(subset=["billing_date_parsed"])

        # ── Compute age in months ──────────────────────────────────
        # reference_date is tz-naive (set in __init__); billing_date_parsed is tz-naive.
        ref = self.reference_date
        if ref.tzinfo is not None:
            # Strip timezone for arithmetic with tz-naive billing dates
            ref = pd.Timestamp(ref.date())

        sold_rows["age_months"] = (
            (ref - sold_rows["billing_date_parsed"]).dt.days / _DAYS_PER_MONTH
        ).clip(lower=0.0)

        # ── Assign age cohort ──────────────────────────────────────
        sold_rows["age_cohort"] = pd.cut(
            sold_rows["age_months"],
            bins=AGE_COHORT_BINS,
            labels=AGE_COHORT_LABELS,
            right=True,
        )

        # ── Group by model × cohort ────────────────────────────────
        grouped = (
            sold_rows.groupby(["Model", "age_cohort"], observed=True)
            .size()
            .reset_index(name="vehicle_count")
            .rename(columns={"Model": "model"})
        )

        total_fleet = int(grouped["vehicle_count"].sum())
        if total_fleet == 0:
            logger.warning("Age distribution: total_fleet is 0 — check MCSI data")
            return pd.DataFrame(columns=["model", "age_cohort", "vehicle_count", "pct_of_fleet"])

        grouped["pct_of_fleet"] = (
            (grouped["vehicle_count"] / total_fleet * 100).round(2)
        )

        result = (
            grouped
            .sort_values(["model", "age_cohort"])
            .reset_index(drop=True)
        )

        # ── Log summary ────────────────────────────────────────────
        logger.info(
            f"Age distribution: {result['model'].nunique()} models "
            f"x {result['age_cohort'].nunique()} cohorts = {len(result)} rows"
        )
        logger.info(f"  Total fleet analysed: {total_fleet:,} vehicles")
        logger.info(f"  Reference date: {ref.date()}")
        logger.info("  Cohort summary:")
        cohort_totals = result.groupby("age_cohort", observed=True)["vehicle_count"].sum()
        for cohort, count in cohort_totals.items():
            pct = count / total_fleet * 100
            logger.info(f"    {str(cohort):<18}: {count:>6,}  ({pct:5.1f}%)")

        return result
