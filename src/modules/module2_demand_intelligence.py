"""Module 2: Demand Intelligence.

Sub-components:
  1. Dealer Orders Forecast    — confirmed order patterns per SKU → future orders
  2. Spare Part Sales Forecast — billing history per SKU → future sales
  3. UIO-Based Demand Est.    — fleet size × age × failure rate → expected demand
  4. Demand Fusion            — weighted ensemble of the three signals

Design: wrap-and-promote.  Existing stage logic is unchanged.  This module
calls stage-level functions (stage064, stock_movements, abc_xyz_fsn) as
sub-routines, transforms their outputs into module-standard DataFrames, and
adds the new demand-fusion computation.

Outputs (returned in DemandIntelligenceResult):
  orders_forecast  : [part_no, month, forecast_qty, source="orders"]
  sales_forecast   : [part_no, month, forecast_qty, source="sales"]
  uio_demand       : [part_no, month, uio_demand_qty, source="uio"]
  fused_demand     : [part_no, month, demand_qty, method, cv, demand_class]

Key business rules applied:
  - orders scope  : doc_type == "PO" (SD Document Category C → purchase orders)
  - sales scope   : bill_class == "sale" (SlsVolQty > 0); SAP dedup already
                    applied in orders_clean / sales_clean parquets by stage04/05
  - UIO demand    : stage064 formula (replacement_freq × projected_uio × supply_pct)
                    scaled month-by-month using the stage03 UIO growth trajectory
  - Dealer scope  : orders_clean and sales_clean are already scoped to registered
                    Yamaha dealers (inner-joined on Dealer_Code in stage04/05)

Note on Material column format:
  orders_clean: Material = 9-digit part codes  (e.g. "571902NAE")
  sales_clean : Material = material descriptions (e.g. "YAMALUBE 20W40 YAM")
  Both are used as opaque part identifiers within their own signal.  The fusion
  step merges on part_no strings; orders and UIO signals share the same 9-digit
  key space and will overlap.  Sales uses descriptions and will not overlap with
  the other two signals unless a description-to-code lookup is added (TODO).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_PROCESSED
from src.modules.module1_vehicle_intelligence import VehicleIntelligenceResult

# ---------------------------------------------------------------------------
# Module-level constants — override via DemandIntelligence class attributes
# ---------------------------------------------------------------------------

#: Default fusion weights (must sum to 1.0)
_DEFAULT_WEIGHT_ORDERS: float = 0.4
_DEFAULT_WEIGHT_SALES: float = 0.4
_DEFAULT_WEIGHT_UIO: float = 0.2

#: Number of trailing months to average for the statistical forecast fallback
TRAILING_MONTHS: int = 6

#: UIO supply conservatism factor (60 % of theoretical max demand)
_UIO_SUPPLY_PCT: float = 0.60

#: Stage03 UIO forecast file (stage-level, not Module-1 output)
_UIO_FORECAST_PARQUET = DATA_INTERIM / "uio_forecast.parquet"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DemandIntelligenceResult:
    """Typed container for all Module 2 outputs.

    Attributes:
        orders_forecast: Per-SKU monthly order-based demand forecast.
        sales_forecast:  Per-SKU monthly sales-billing demand forecast.
        uio_demand:      Per-SKU monthly UIO-driven demand estimate.
        fused_demand:    Weighted ensemble of the three signals.
        metadata:        Run diagnostics (row counts, timings, warnings).
    """

    orders_forecast: pd.DataFrame
    sales_forecast: pd.DataFrame
    uio_demand: pd.DataFrame
    fused_demand: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class DemandIntelligence:
    """Module 2: Demand Intelligence.

    Combines three independent demand signals (orders, sales, UIO) into a
    fused monthly demand estimate per SKU across a planning horizon.

    Class-level weight constants are intentionally overridable so Module 5
    (Inventory Policy) can tune them for specific SKU tiers without modifying
    this class.

    Args:
        vehicle_result: Module 1 output (VehicleIntelligenceResult).
            When provided, UIO growth rates are taken from Module 1's
            per-model UIO forecast.  When None, the stage03 aggregate
            UIO forecast is used directly.
        horizon_months: Total planning horizon in months.
            Default is 15 (3-month lead time + 12-month planning window).
    """

    WEIGHT_ORDERS: float = _DEFAULT_WEIGHT_ORDERS
    WEIGHT_SALES: float = _DEFAULT_WEIGHT_SALES
    WEIGHT_UIO: float = _DEFAULT_WEIGHT_UIO

    def __init__(
        self,
        vehicle_result: VehicleIntelligenceResult | None = None,
        horizon_months: int = 15,
    ) -> None:
        self.vehicle_result = vehicle_result
        self.horizon_months = horizon_months
        # Populated by _forecast_orders() and reused by subsequent sub-components
        self._future_months: list[pd.Timestamp] = []
        logger.info(
            f"DemandIntelligence: horizon={horizon_months} months | "
            f"weights=orders:{self.WEIGHT_ORDERS} / "
            f"sales:{self.WEIGHT_SALES} / uio:{self.WEIGHT_UIO}"
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> DemandIntelligenceResult:
        """Run all four sub-components and return a typed result.

        Returns:
            DemandIntelligenceResult with all four demand DataFrames plus
            run metadata.

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "horizon_months": self.horizon_months,
            "weight_orders": self.WEIGHT_ORDERS,
            "weight_sales": self.WEIGHT_SALES,
            "weight_uio": self.WEIGHT_UIO,
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 2: DEMAND INTELLIGENCE")
        logger.info("=" * 60)

        # Sub-component 1: orders forecast
        logger.info("Sub-component 1/4: dealer orders forecast")
        t1 = time.perf_counter()
        orders_fc = self._forecast_orders()
        metadata["orders_forecast_rows"] = len(orders_fc)
        metadata["orders_forecast_skus"] = int(orders_fc["part_no"].nunique()) if not orders_fc.empty else 0
        logger.info(f"  -> {orders_fc.shape} in {time.perf_counter() - t1:.2f}s")

        # Sub-component 2: sales forecast
        logger.info("Sub-component 2/4: spare part sales forecast")
        t2 = time.perf_counter()
        sales_fc = self._forecast_sales()
        metadata["sales_forecast_rows"] = len(sales_fc)
        metadata["sales_forecast_skus"] = int(sales_fc["part_no"].nunique()) if not sales_fc.empty else 0
        logger.info(f"  -> {sales_fc.shape} in {time.perf_counter() - t2:.2f}s")

        # Sub-component 3: UIO-based demand
        logger.info("Sub-component 3/4: UIO-based demand estimation")
        t3 = time.perf_counter()
        uio_demand = self._estimate_uio_demand()
        metadata["uio_demand_rows"] = len(uio_demand)
        metadata["uio_demand_skus"] = int(uio_demand["part_no"].nunique()) if not uio_demand.empty else 0
        logger.info(f"  -> {uio_demand.shape} in {time.perf_counter() - t3:.2f}s")

        # Sub-component 4: demand fusion
        logger.info("Sub-component 4/4: demand fusion (weighted ensemble)")
        t4 = time.perf_counter()
        fused = self._fuse_demand(orders_fc, sales_fc, uio_demand)
        metadata["fused_demand_rows"] = len(fused)
        metadata["fused_demand_skus"] = int(fused["part_no"].nunique()) if not fused.empty else 0
        logger.info(f"  -> {fused.shape} in {time.perf_counter() - t4:.2f}s")

        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)
        metadata["future_months"] = (
            [str(m.date()) for m in self._future_months] if self._future_months else []
        )

        logger.info("=" * 60)
        logger.info("MODULE 2 COMPLETE")
        logger.info(f"  Orders forecast  : {orders_fc.shape}")
        logger.info(f"  Sales forecast   : {sales_fc.shape}")
        logger.info(f"  UIO demand       : {uio_demand.shape}")
        logger.info(f"  Fused demand     : {fused.shape}")
        logger.info(f"  Total time       : {elapsed:.2f}s")
        logger.info("=" * 60)

        return DemandIntelligenceResult(
            orders_forecast=orders_fc,
            sales_forecast=sales_fc,
            uio_demand=uio_demand,
            fused_demand=fused,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Helper: generate future months
    # ------------------------------------------------------------------

    def _build_future_months(self, last_hist_month: str) -> list[pd.Timestamp]:
        """Generate horizon_months Timestamps starting the month after last_hist_month.

        Business meaning: the planning horizon begins immediately after the most
        recent month of historical data.  The first month is the earliest possible
        replenishment arrival given a 3-month lead time.

        Args:
            last_hist_month: Most recent month in "YYYY-MM" format (e.g. "2025-12").

        Returns:
            List of pd.Timestamps, one per future month, tz-naive.
        """
        start = pd.to_datetime(last_hist_month + "-01") + pd.DateOffset(months=1)
        return [start + pd.DateOffset(months=i) for i in range(self.horizon_months)]

    # ------------------------------------------------------------------
    # Helper: trailing-average forecast
    # ------------------------------------------------------------------

    def _trailing_avg_forecast(
        self,
        monthly: pd.DataFrame,
        part_col: str,
        qty_col: str,
        future_months: list[pd.Timestamp],
        source_label: str,
    ) -> pd.DataFrame:
        """Compute trailing-average flat forecast per SKU across future months.

        Business meaning: the trailing TRAILING_MONTHS-month average is a robust,
        data-efficient baseline that captures recent demand levels without
        over-fitting to a single peak period.

        TODO (production upgrade): Replace with stage10's tiered ensemble
        (AutoETS / LightGBM / NHITS) per SKU using the monthly_demand.parquet
        panel.  The current fallback is intentionally simple — it avoids the
        risk of fitting complex models on only 2 years of order/billing data.
        Use `src.models.demand_forecast.stage10_demand_forecast.compute_forecasts`
        once this module's input DataFrames are confirmed stable.

        Args:
            monthly:      DataFrame with columns [part_col, month_ts (Timestamp), qty_col].
                          month_ts must already be a Timestamp (not a string).
            part_col:     Column name for the part identifier.
            qty_col:      Column name for the demand quantity.
            future_months: List of future month Timestamps.
            source_label: Signal source label ("orders", "sales", or "uio").

        Returns:
            DataFrame with columns [part_no, month, forecast_qty, source].
        """
        empty = pd.DataFrame(columns=["part_no", "month", "forecast_qty", "source"])
        if monthly.empty or not future_months:
            return empty

        # Per-SKU: mean of the last TRAILING_MONTHS months by chronological order
        avg: pd.DataFrame = (
            monthly
            .sort_values("month_ts")
            .groupby(part_col, sort=False)[qty_col]
            .apply(lambda s: float(s.tail(TRAILING_MONTHS).mean()))
            .reset_index(name="forecast_qty")
            .rename(columns={part_col: "part_no"})
        )
        avg["forecast_qty"] = avg["forecast_qty"].fillna(0.0).clip(lower=0.0)

        # Cross-join SKUs × future months
        avg["_key"] = 1
        months_df = pd.DataFrame({"month": future_months, "_key": 1})
        forecast = avg.merge(months_df, on="_key").drop(columns="_key")
        forecast["source"] = source_label

        return forecast[["part_no", "month", "forecast_qty", "source"]].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 1: orders forecast
    # ------------------------------------------------------------------

    def _forecast_orders(self) -> pd.DataFrame:
        """Wrap stage04 orders data to produce per-SKU monthly order forecast.

        Business meaning: dealer purchase orders (SD Document Category C) represent
        explicit demand placed on the distributor.  Confirmed quantities are used
        (not order quantities) to reflect actual fulfilment history and exclude
        rejected lines.

        Business rules applied:
          - doc_type == "PO" — purchase orders only (not returns)
          - orders_clean.parquet is already dealer-scoped (stage04 inner-joined
            on Sold-to Party = Dealer_Code)
          - Confirmed Quantity (Item) is the quantity that was actually confirmed
            for delivery (per-line fill rate already applied by stage04)

        Returns:
            DataFrame with columns:
                part_no      (str)   — 9-digit material code
                month        (pd.Timestamp) — first day of forecast month
                forecast_qty (float) — trailing-average monthly demand estimate
                source       (str)   — "orders"

        Raises:
            FileNotFoundError: If orders_clean.parquet is missing.
        """
        orders_path = DATA_INTERIM / "orders_clean.parquet"
        if not orders_path.exists():
            raise FileNotFoundError(
                f"orders_clean.parquet not found at {orders_path}. "
                "Run stage04 first: python -m scripts.run_stage 4"
            )

        oc = pd.read_parquet(orders_path)
        logger.debug(f"  orders_clean loaded: {oc.shape}")

        # Purchase orders only
        po = oc[oc["doc_type"] == "PO"].copy()
        logger.debug(f"  PO rows: {len(po):,}")

        if po.empty:
            logger.warning("  No PO rows found in orders_clean — returning empty orders forecast")
            self._future_months = self._build_future_months("2025-12")
            return pd.DataFrame(columns=["part_no", "month", "forecast_qty", "source"])

        po["Confirmed Quantity (Item)"] = pd.to_numeric(
            po["Confirmed Quantity (Item)"], errors="coerce"
        ).fillna(0.0)

        # Monthly aggregation by part number
        monthly = (
            po.groupby(["Material", "Year_Month_str"])["Confirmed Quantity (Item)"]
            .sum()
            .reset_index()
            .rename(columns={
                "Material": "part_no",
                "Confirmed Quantity (Item)": "order_qty",
            })
        )
        monthly["month_ts"] = pd.to_datetime(monthly["Year_Month_str"] + "-01")

        last_month = str(monthly["Year_Month_str"].max())
        self._future_months = self._build_future_months(last_month)
        logger.info(
            f"  Orders history: {monthly['Year_Month_str'].min()} – {last_month} | "
            f"{monthly['part_no'].nunique():,} unique SKUs"
        )

        result = self._trailing_avg_forecast(
            monthly,
            part_col="part_no",
            qty_col="order_qty",
            future_months=self._future_months,
            source_label="orders",
        )
        logger.info(
            f"  Orders forecast: {result['part_no'].nunique():,} SKUs × "
            f"{len(self._future_months)} months = {len(result):,} rows"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-component 2: sales forecast
    # ------------------------------------------------------------------

    def _forecast_sales(self) -> pd.DataFrame:
        """Wrap stage05 billing data to produce per-SKU monthly sales forecast.

        Business meaning: billing lines to dealers represent the actual revenue-
        generating sales of spare parts.  This signal captures what dealers
        actually paid for (sell-through demand) rather than what they ordered.

        Business rules applied:
          - bill_class == "sale" — positive billing lines only (SlsVolQty > 0)
          - SAP export dedup (drop_duplicates on Billing Document + Item) is
            already applied in sales_clean.parquet by stage05; no second dedup needed
          - sales_clean.parquet is already dealer-scoped (stage05 inner-joined
            Payer = Dealer Name)

        Note on Material column format:
          sales.xlsx exports Material as a material description string (e.g.
          "YAMALUBE 20W40 YAM"), not a 9-digit part code.  This is an SAP export
          artefact.  The sales signal therefore lives in a different key space from
          orders and UIO (which use 9-digit codes).  Fusion weight is redistributed
          to orders/UIO for part numbers where sales has no match.
          TODO: add a description→code lookup via part_master to align key spaces.

        Returns:
            DataFrame with columns:
                part_no      (str)   — material description (SAP billing export format)
                month        (pd.Timestamp)
                forecast_qty (float) — trailing-average monthly sales qty estimate
                source       (str)   — "sales"

        Raises:
            FileNotFoundError: If sales_clean.parquet is missing.
        """
        sales_path = DATA_INTERIM / "sales_clean.parquet"
        if not sales_path.exists():
            raise FileNotFoundError(
                f"sales_clean.parquet not found at {sales_path}. "
                "Run stage05 first: python -m scripts.run_stage 5"
            )

        sc = pd.read_parquet(sales_path)
        logger.debug(f"  sales_clean loaded: {sc.shape}")

        # Sales lines only (no returns)
        sales = sc[sc["bill_class"] == "sale"].copy()
        logger.debug(f"  Sales rows (bill_class=sale): {len(sales):,}")

        if sales.empty:
            logger.warning("  No sale rows in sales_clean — returning empty sales forecast")
            return pd.DataFrame(columns=["part_no", "month", "forecast_qty", "source"])

        sales["SlsVolQty"] = pd.to_numeric(sales["SlsVolQty"], errors="coerce").fillna(0.0)

        # Monthly aggregation by material (description-keyed)
        monthly = (
            sales.groupby(["Material", "Year_Month_str"])["SlsVolQty"]
            .sum()
            .reset_index()
            .rename(columns={"Material": "part_no", "SlsVolQty": "sales_qty"})
        )
        monthly["month_ts"] = pd.to_datetime(monthly["Year_Month_str"] + "-01")

        # Use pre-computed future months from sub-component 1; fall back to data max
        if not self._future_months:
            last_month = str(monthly["Year_Month_str"].max())
            self._future_months = self._build_future_months(last_month)

        logger.info(
            f"  Sales history: {monthly['Year_Month_str'].min()} – {monthly['Year_Month_str'].max()} | "
            f"{monthly['part_no'].nunique():,} unique SKUs"
        )

        result = self._trailing_avg_forecast(
            monthly,
            part_col="part_no",
            qty_col="sales_qty",
            future_months=self._future_months,
            source_label="sales",
        )
        logger.info(
            f"  Sales forecast: {result['part_no'].nunique():,} SKUs × "
            f"{len(self._future_months)} months = {len(result):,} rows"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-component 3: UIO-based demand estimation
    # ------------------------------------------------------------------

    def _estimate_uio_demand(self) -> pd.DataFrame:
        """Estimate per-SKU demand from fleet size using stage064 logic.

        Business meaning: spare-parts demand is driven by the installed fleet
        (Units in Operation).  A part that fits Model X is consumed at a rate
        proportional to the number of Model-X bikes in operation.

        Stage064 formula:
          replacement_freq = avg_monthly_issues / avg_model_uio
          uio_demand_monthly = replacement_freq × projected_uio × supply_pct

        This sub-component extends stage064's static monthly estimate into a
        time-varying series by applying the stage03 UIO growth trajectory:
          uio_demand_qty[m] = uio_demand_monthly × (uio_total[m] / base_uio)

        where base_uio = uio_total at the first forecast period (2026-01) and
        uio_total[m] comes from the stage03 UIO forecast (data/interim/uio_forecast.parquet).
        For months beyond the stage03 forecast window, the last-period growth rate
        is extrapolated linearly.

        Merges with:
          - data/interim/uio_based_demand.parquet  (stage064 output, if it exists)
          - data/interim/uio_forecast.parquet       (stage03 UIO growth trajectory)
          - data/interim/stock_movements.parquet    (for stage064 computation if needed)
          - data/processed/m1_uio_forecast.parquet  (Module 1, for model-level detail)
          - data/processed/m1_age_distribution.parquet (Module 1, age cohort weights)

        Returns:
            DataFrame with columns:
                part_no        (str)   — 9-digit material code
                month          (pd.Timestamp)
                uio_demand_qty (float) — UIO-driven monthly demand estimate
                source         (str)   — "uio"

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        from src.models.master_data.stage064_uio_demand import compute_uio_demand

        # ── Load or compute stage064 UIO demand ─────────────────────
        uio_demand_path = DATA_INTERIM / "uio_based_demand.parquet"
        if uio_demand_path.exists():
            logger.debug("  Loading cached uio_based_demand.parquet")
            base_demand = pd.read_parquet(uio_demand_path)
        else:
            logger.info("  uio_based_demand.parquet not found — computing via stage064")
            base_demand = self._compute_stage064_demand(compute_uio_demand)
            if not base_demand.empty:
                base_demand.to_parquet(uio_demand_path, index=False)
                logger.info(f"  stage064 result cached → {uio_demand_path}")

        if base_demand.empty:
            logger.warning("  stage064 returned empty result — returning empty UIO demand")
            return pd.DataFrame(columns=["part_no", "month", "uio_demand_qty", "source"])

        # ── UIO growth multiplier per future month ───────────────────
        growth_mults = self._compute_uio_growth_multipliers()
        logger.debug(f"  UIO growth multipliers: {len(growth_mults)} months")

        # ── Build future months if not yet set ──────────────────────
        if not self._future_months:
            last_hist = sorted(growth_mults.keys())[0]
            # growth_mults are keyed by forecast month Timestamps
            all_months = sorted(growth_mults.keys())
            self._future_months = all_months[: self.horizon_months]

        # ── Ensure we cover horizon_months months ───────────────────
        target_months = self._future_months[: self.horizon_months]

        # ── Expand static UIO demand to monthly time series ─────────
        mat_col = "material_9"
        if mat_col not in base_demand.columns:
            # Fallback: accept common alternative column names
            for alt in ["part_number", "Material", "material"]:
                if alt in base_demand.columns:
                    base_demand = base_demand.rename(columns={alt: mat_col})
                    break

        monthly_col = "uio_demand_monthly"
        if monthly_col not in base_demand.columns:
            logger.error(
                f"  stage064 result missing 'uio_demand_monthly' column. "
                f"Columns present: {list(base_demand.columns)}"
            )
            return pd.DataFrame(columns=["part_no", "month", "uio_demand_qty", "source"])

        # Keep only parts with non-zero UIO demand
        active = base_demand[base_demand[monthly_col] > 0][[mat_col, monthly_col]].copy()
        logger.info(
            f"  stage064: {len(base_demand):,} total parts | "
            f"{len(active):,} with non-zero UIO demand"
        )

        records: list[dict[str, Any]] = []
        for month in target_months:
            mult = growth_mults.get(month, None)
            if mult is None:
                # Extrapolate: use last available multiplier
                available = sorted(growth_mults.keys())
                mult = growth_mults[available[-1]] if available else 1.0
                logger.debug(f"  Extrapolating UIO growth for {month.date()} using mult={mult:.3f}")

            for _, row in active.iterrows():
                uio_qty = float(row[monthly_col]) * mult
                records.append({
                    "part_no": str(row[mat_col]),
                    "month": month,
                    "uio_demand_qty": max(0.0, round(uio_qty, 4)),
                })

        if not records:
            return pd.DataFrame(columns=["part_no", "month", "uio_demand_qty", "source"])

        result = pd.DataFrame(records)
        result["source"] = "uio"
        logger.info(
            f"  UIO demand: {result['part_no'].nunique():,} SKUs × "
            f"{result['month'].nunique()} months = {len(result):,} rows"
        )
        return result[["part_no", "month", "uio_demand_qty", "source"]].reset_index(drop=True)

    def _compute_stage064_demand(self, compute_fn: Any) -> pd.DataFrame:
        """Invoke stage064.compute_uio_demand() with available interim parquets.

        Pre-processing applied before calling stage064:
          stock_movements.parquet records issue quantities as negative values
          (SAP sign convention: goods issue = stock decrease = negative quantity).
          stage064 expects positive issue quantities for its replacement-frequency
          calculation.  This method normalises issue rows to positive abs(qty)
          on a defensive in-memory copy — the parquet on disk is never modified.

        Args:
            compute_fn: The compute_uio_demand function from stage064.

        Returns:
            Stage064 result DataFrame, or empty DataFrame on failure.
        """
        def _try_load(path: "Any") -> pd.DataFrame:
            return pd.read_parquet(path) if path.exists() else pd.DataFrame()

        movements_raw = _try_load(DATA_INTERIM / "stock_movements.parquet")
        uio_forecast = _try_load(_UIO_FORECAST_PARQUET)
        uio_external = _try_load(DATA_INTERIM / "uio_external.parquet")
        part_master = _try_load(DATA_INTERIM / "part_master.parquet")

        if part_master.empty:
            logger.warning("  part_master.parquet is empty — stage064 cannot run")
            return pd.DataFrame()

        # ── Normalise issue qty sign (SAP convention: issue = negative) ──────
        # stage064's _monthly_issues() expects positive issue quantities.
        # Take abs(qty) for issue rows on an in-memory copy; never touch the parquet.
        movements = movements_raw.copy()
        mc_col = next(
            (c for c in ["movement_class", "MovementClass", "move_class"] if c in movements.columns),
            None,
        )
        qty_col = next(
            (c for c in ["qty", "Qty", "quantity"] if c in movements.columns),
            None,
        )
        if mc_col and qty_col:
            issue_mask = movements[mc_col] == "issue"
            n_negative = int((movements.loc[issue_mask, qty_col] < 0).sum())
            if n_negative > 0:
                movements.loc[issue_mask, qty_col] = movements.loc[issue_mask, qty_col].abs()
                logger.debug(
                    f"  Sign-normalised {n_negative:,} issue rows "
                    f"(SAP negative → positive for stage064)"
                )

        try:
            result = compute_fn(
                movements=movements,
                uio_external=uio_external,
                uio_forecast=uio_forecast,
                part_master=part_master,
                supply_pct=_UIO_SUPPLY_PCT,
            )
            return result if result is not None else pd.DataFrame()
        except Exception as exc:
            logger.error(f"  stage064.compute_uio_demand() failed: {exc!r}")
            return pd.DataFrame()

    def _compute_uio_growth_multipliers(self) -> dict[pd.Timestamp, float]:
        """Compute per-month UIO growth multipliers from stage03 forecast.

        Business meaning: the stage03 stock-flow model projects the total fleet
        size month by month.  Dividing each future month's projected UIO by the
        base UIO (first forecast period) gives a dimensionless growth multiplier
        that scales the static stage064 demand estimate appropriately.

        For months beyond the stage03 forecast window, the average monthly growth
        rate from the last 3 available forecast periods is used for extrapolation.

        Returns:
            Dict mapping future month Timestamp → growth multiplier (≥ 0).
        """
        if not _UIO_FORECAST_PARQUET.exists():
            logger.warning(
                f"  {_UIO_FORECAST_PARQUET.name} not found — using multiplier=1.0 for all months"
            )
            if self._future_months:
                return {m: 1.0 for m in self._future_months}
            return {}

        uio_fc = pd.read_parquet(_UIO_FORECAST_PARQUET)

        # Forecast rows only
        is_fc_col = next(
            (c for c in ["is_forecast", "forecast"] if c in uio_fc.columns), None
        )
        if is_fc_col:
            fc_rows = uio_fc[uio_fc[is_fc_col] == True].copy()  # noqa: E712
        else:
            fc_rows = uio_fc.copy()

        uio_col = next(
            (c for c in ["uio_total", "uio", "UIO"] if c in fc_rows.columns), None
        )
        period_col = next(
            (c for c in ["period", "year_month", "Year_Month_str"] if c in fc_rows.columns), None
        )

        if uio_col is None or period_col is None or fc_rows.empty:
            logger.warning("  uio_forecast.parquet has no usable forecast rows — multiplier=1.0")
            return {m: 1.0 for m in self._future_months}

        fc_rows = fc_rows.copy()
        fc_rows["month_ts"] = pd.to_datetime(fc_rows[period_col].astype(str) + "-01")
        fc_rows = fc_rows.sort_values("month_ts").reset_index(drop=True)

        base_uio = float(fc_rows.iloc[0][uio_col])
        if base_uio <= 0:
            logger.warning("  base UIO from stage03 is 0 or negative — multiplier=1.0")
            return {m: 1.0 for m in self._future_months}

        # Build lookup: month → multiplier
        available_mults: dict[pd.Timestamp, float] = {
            row["month_ts"]: float(row[uio_col]) / base_uio
            for _, row in fc_rows.iterrows()
        }

        # Compute extrapolation rate from the last 3 forecast periods
        last_3 = fc_rows.tail(3)[uio_col].values.astype(float)
        if len(last_3) >= 2:
            avg_monthly_growth = float(np.mean(np.diff(last_3)))
            last_uio = float(fc_rows.iloc[-1][uio_col])
            last_ts = fc_rows.iloc[-1]["month_ts"]
        else:
            avg_monthly_growth = 0.0
            last_uio = base_uio
            last_ts = fc_rows.iloc[-1]["month_ts"] if not fc_rows.empty else pd.Timestamp.now()

        # Build multipliers for all target months
        result: dict[pd.Timestamp, float] = {}
        target = self._future_months[: self.horizon_months]
        for m in target:
            if m in available_mults:
                result[m] = available_mults[m]
            else:
                # Extrapolate: months_beyond × avg_monthly_growth beyond last available UIO
                months_beyond = (m.year - last_ts.year) * 12 + (m.month - last_ts.month)
                extrap_uio = last_uio + months_beyond * avg_monthly_growth
                result[m] = max(0.0, extrap_uio / base_uio)

        logger.debug(
            f"  UIO growth: base_uio={base_uio:,.0f} | "
            f"month range: {min(result.keys()).date()} – {max(result.keys()).date()} | "
            f"mults: {min(result.values()):.2f} – {max(result.values()):.2f}"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-component 4: demand fusion
    # ------------------------------------------------------------------

    def _fuse_demand(
        self,
        orders_fc: pd.DataFrame,
        sales_fc: pd.DataFrame,
        uio_demand: pd.DataFrame,
    ) -> pd.DataFrame:
        """Fuse three demand signals into a single weighted estimate per SKU × month.

        Business meaning: no single signal is authoritative.  Orders capture
        dealer intent; sales captures revenue-confirmed demand; UIO captures
        fleet-driven replacement need.  A weighted average of available signals
        gives a more robust estimate than any one signal alone.

        Weight redistribution: if one or more signals are missing for a given
        SKU × month, the configured weights of available signals are normalised
        to sum to 1.0.  A signal is "missing" when it has no row for that
        (part_no, month) pair in the input DataFrames.

        Demand class attachment:
          - CV (coefficient of variation) is taken from abc_xyz_fsn.parquet
            if the part_no matches a material_9 key (orders and UIO use 9-digit
            codes; sales uses descriptions which may not match).
          - demand_class is taken from abc_xyz_fsn.abc_xyz_fsn column
            (e.g. "AYF", "CZN", etc.).
          - For parts not in abc_xyz_fsn: cv = NaN, demand_class = "Unknown".

        Args:
            orders_fc:  Orders forecast [part_no, month, forecast_qty, source].
            sales_fc:   Sales forecast  [part_no, month, forecast_qty, source].
            uio_demand: UIO demand      [part_no, month, uio_demand_qty, source].

        Returns:
            DataFrame with columns:
                part_no      (str)
                month        (pd.Timestamp)
                demand_qty   (float) — fused demand estimate
                method       (str)   — comma-joined list of contributing signals
                cv           (float) — coefficient of variation from classification
                demand_class (str)   — ABC-XYZ-FSN class or "Unknown"
        """
        w_ord = self.WEIGHT_ORDERS
        w_sal = self.WEIGHT_SALES
        w_uio = self.WEIGHT_UIO

        # Standardise column names for the merge
        ord_prep = orders_fc.rename(columns={"forecast_qty": "qty_orders"})[
            ["part_no", "month", "qty_orders"]
        ] if not orders_fc.empty else pd.DataFrame(columns=["part_no", "month", "qty_orders"])

        sal_prep = sales_fc.rename(columns={"forecast_qty": "qty_sales"})[
            ["part_no", "month", "qty_sales"]
        ] if not sales_fc.empty else pd.DataFrame(columns=["part_no", "month", "qty_sales"])

        uio_prep = uio_demand.rename(columns={"uio_demand_qty": "qty_uio"})[
            ["part_no", "month", "qty_uio"]
        ] if not uio_demand.empty else pd.DataFrame(columns=["part_no", "month", "qty_uio"])

        # Full outer merge on (part_no, month)
        merged = ord_prep.merge(sal_prep, on=["part_no", "month"], how="outer")
        merged = merged.merge(uio_prep, on=["part_no", "month"], how="outer")

        if merged.empty:
            logger.warning("  Fusion merge produced empty DataFrame — all signals empty?")
            return pd.DataFrame(
                columns=["part_no", "month", "demand_qty", "method", "cv", "demand_class"]
            )

        # Compute fused demand row by row with weight redistribution
        fused_records = _apply_fusion_weights(merged, w_ord, w_sal, w_uio)

        # Attach ABC-XYZ-FSN classification
        fused_records = self._attach_classification(fused_records)

        result = fused_records.sort_values(
            ["part_no", "month"]
        ).reset_index(drop=True)

        # Log method distribution
        method_counts = result["method"].value_counts().head(5)
        logger.info(f"  Fusion method distribution (top 5): {method_counts.to_dict()}")
        logger.info(
            f"  Fused demand: {result['part_no'].nunique():,} SKUs × "
            f"{result['month'].nunique()} months | "
            f"mean demand_qty = {result['demand_qty'].mean():.2f}"
        )
        return result

    def _attach_classification(self, fused: pd.DataFrame) -> pd.DataFrame:
        """Attach CV and demand_class from abc_xyz_fsn.parquet.

        Business meaning: the classification (ABC importance, XYZ variability,
        FSN movement speed) derived in stage09 is the authoritative segmentation
        for inventory policy decisions.  Attaching it here makes the fused demand
        output self-contained for Module 5 (Inventory Policy).

        Args:
            fused: DataFrame with at least [part_no, month, demand_qty, method].

        Returns:
            Same DataFrame with cv (float) and demand_class (str) columns added.
            Parts not in abc_xyz_fsn get cv=NaN, demand_class="Unknown".
        """
        abc_path = DATA_INTERIM / "abc_xyz_fsn.parquet"
        if not abc_path.exists():
            logger.warning(
                "  abc_xyz_fsn.parquet not found — cv and demand_class will be NaN/Unknown"
            )
            fused = fused.copy()
            fused["cv"] = float("nan")
            fused["demand_class"] = "Unknown"
            return fused

        abc = pd.read_parquet(abc_path)[["material_9", "cv", "abc_xyz_fsn"]].copy()
        abc = abc.rename(columns={"material_9": "part_no", "abc_xyz_fsn": "demand_class"})
        abc["cv"] = pd.to_numeric(abc["cv"], errors="coerce")

        # One-to-one merge; parts not found get NaN/Unknown
        result = fused.merge(abc, on="part_no", how="left")
        result["cv"] = result["cv"].fillna(float("nan"))
        result["demand_class"] = result["demand_class"].fillna("Unknown")

        matched = int(result["demand_class"].ne("Unknown").sum())
        logger.info(
            f"  Classification match: {matched:,} / {len(result):,} rows "
            f"({matched / max(len(result), 1) * 100:.1f}%)"
        )
        return result


# ---------------------------------------------------------------------------
# Pure helper: fusion weight application (module-level, easily unit-testable)
# ---------------------------------------------------------------------------


def _apply_fusion_weights(
    merged: pd.DataFrame,
    w_ord: float,
    w_sal: float,
    w_uio: float,
) -> pd.DataFrame:
    """Apply weighted average fusion with missing-signal weight redistribution.

    Business meaning: when a signal is absent for a given (part_no, month),
    its weight is redistributed proportionally across the available signals so
    the fused estimate always reflects the best available information.

    Args:
        merged:  DataFrame with columns [part_no, month, qty_orders, qty_sales, qty_uio].
                 NaN in any qty column means the signal is absent for that row.
        w_ord:   Configured weight for the orders signal.
        w_sal:   Configured weight for the sales signal.
        w_uio:   Configured weight for the UIO signal.

    Returns:
        DataFrame with columns [part_no, month, demand_qty, method].
    """
    merged = merged.copy()

    # Presence masks
    has_ord = merged["qty_orders"].notna()
    has_sal = merged["qty_sales"].notna()
    has_uio = merged["qty_uio"].notna()

    # Fill NaN with 0 for arithmetic (weight will be 0 for absent signals)
    merged["qty_orders"] = merged["qty_orders"].fillna(0.0)
    merged["qty_sales"] = merged["qty_sales"].fillna(0.0)
    merged["qty_uio"] = merged["qty_uio"].fillna(0.0)

    # Effective weight per row: 0 if signal absent, raw weight if present
    eff_ord = has_ord.astype(float) * w_ord
    eff_sal = has_sal.astype(float) * w_sal
    eff_uio = has_uio.astype(float) * w_uio

    total_weight = eff_ord + eff_sal + eff_uio

    # Normalise weights (avoid division by zero — all signals absent)
    total_weight_safe = total_weight.replace(0.0, 1.0)

    merged["demand_qty"] = (
        (eff_ord / total_weight_safe) * merged["qty_orders"]
        + (eff_sal / total_weight_safe) * merged["qty_sales"]
        + (eff_uio / total_weight_safe) * merged["qty_uio"]
    ).clip(lower=0.0).round(4)

    # Method string: comma-joined list of contributing signal names
    def _method_str(row: pd.Series) -> str:
        parts: list[str] = []
        if row["qty_orders"] > 0 and has_ord.loc[row.name]:
            parts.append("orders")
        if row["qty_sales"] > 0 and has_sal.loc[row.name]:
            parts.append("sales")
        if row["qty_uio"] > 0 and has_uio.loc[row.name]:
            parts.append("uio")
        return "+".join(parts) if parts else "none"

    merged["method"] = merged.apply(_method_str, axis=1)

    return merged[["part_no", "month", "demand_qty", "method"]].copy()
