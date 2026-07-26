"""Module 4: Inventory Planning.

Sub-components:
  1. Lead-Time Demand  — expected demand over the import horizon (lead time + review period)
  2. Safety Stock      — buffer to cover demand variability and lead-time uncertainty
  3. Reorder Level     — trigger point: lead-time demand + safety stock

Design: wrap-and-promote.  The existing stage12_rol_roq and _ss_model logic is
referenced for formula consistency, but Module 4 operates directly on the
Module 2 / Module 3 parquet outputs rather than the stage-level stock-tracker
parquet, so that Modules 1–3 are sufficient upstream dependencies.

Inputs:
  data/processed/m2_fused_demand.parquet  — per-SKU monthly demand (Module 2)
  data/processed/m3_inventory_position.parquet — per-SKU net position (Module 3)

Outputs (returned in InventoryPlanningResult):
  lead_time_demand : [part_no, mean_monthly_demand, lead_time_demand,
                       review_demand, horizon_demand]
  safety_stock     : [part_no, safety_stock, service_level, z_score,
                       sigma_demand, demand_class, ss_method]
  reorder_level    : [part_no, rol, lead_time_demand, safety_stock]
  planning_table   : all columns above + net_position columns +
                     signal_to_reorder (bool) + urgency_score (float)

Key business rules applied:
  - Lead time: 3 months (LEAD_TIME_DAYS = 90 from constants)
  - Review period: 1 month (ORDER_CYCLE_DAYS = 30 from constants)
  - Horizon: 4 months = lead_time + review_period
  - Service levels by ABC class: A=0.99, B=0.95, C=0.90, Unknown=0.95
  - Normal SS formula: Z × σ_demand × √(lead_time_months)
    where σ_demand = mean_monthly_demand × cv
  - Poisson SS formula (CV > 1.2 or cv unknown):
    Z × √(mean_monthly_demand × lead_time_months)
  - ROL = lead_time_demand + safety_stock
  - signal_to_reorder: True when net_position ≤ ROL and ROL > 0
  - urgency_score: (net_position − ROL) / mean_monthly_demand
      Negative → already below ROL (must order now)
      Positive → above ROL, value = months of coverage headroom
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import norm as _scipy_norm

from src.config.constants import LEAD_TIME_DAYS, ORDER_CYCLE_DAYS
from src.config.paths import DATA_PROCESSED

if TYPE_CHECKING:
    from src.modules.module2_demand_intelligence import DemandIntelligenceResult
    from src.modules.module3_inventory_intelligence import InventoryIntelligenceResult

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_FUSED_DEMAND_PATH = DATA_PROCESSED / "m2_fused_demand.parquet"
_INVENTORY_POS_PATH = DATA_PROCESSED / "m3_inventory_position.parquet"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class InventoryPlanningResult:
    """Typed container for all Module 4 outputs.

    Attributes:
        lead_time_demand: Per-SKU expected demand over lead-time and review horizon.
        safety_stock:     Per-SKU safety stock with method and service-level detail.
        reorder_level:    Per-SKU ROL (lead-time demand + safety stock).
        planning_table:   Full planning view joining all sub-components with the
                          current inventory position, reorder signal, and urgency.
        metadata:         Run diagnostics (counts, timings, warnings).
    """

    lead_time_demand: pd.DataFrame
    safety_stock: pd.DataFrame
    reorder_level: pd.DataFrame
    planning_table: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class InventoryPlanning:
    """Module 4: Inventory Planning.

    Computes per-SKU lead-time demand, safety stock, and reorder level from
    the fused demand signal (Module 2) and inventory position (Module 3).

    Service levels by ABC tier:
      A  → 99 %   (critical / revenue-driving spare parts)
      B  → 95 %   (standard managed parts)
      C  → 90 %   (low-value / slow-moving parts)
      Unknown → 95 %  (conservative default)

    Safety stock model selection:
      Normal   (CV ≤ 1.2, CV known): SS = Z × (mean × CV) × √(lead_time_months)
      Poisson  (CV > 1.2 or unknown): SS = Z × √(mean × lead_time_months)

    Class attributes are intentionally overridable so downstream modules can
    tune policy parameters per business unit without subclassing.
    """

    LEAD_TIME_MONTHS: int = LEAD_TIME_DAYS // 30        # 3
    REVIEW_PERIOD_MONTHS: int = ORDER_CYCLE_DAYS // 30  # 1

    SERVICE_LEVELS: dict[str, float] = {
        "A": 0.99,
        "B": 0.95,
        "C": 0.90,
        "Unknown": 0.95,
    }

    #: CV threshold above which Poisson SS is used instead of Normal
    _CV_POISSON_THRESHOLD: float = 1.2

    def __init__(
        self,
        demand_result: "DemandIntelligenceResult | None" = None,
        inventory_result: "InventoryIntelligenceResult | None" = None,
    ) -> None:
        """Initialise Module 4.

        Args:
            demand_result:   Module 2 result (DemandIntelligenceResult).
                When provided, fused_demand is read from result.fused_demand.
                When None, loaded from data/processed/m2_fused_demand.parquet.
            inventory_result: Module 3 result (InventoryIntelligenceResult).
                When provided, inventory_position is read from result.inventory_position.
                When None, loaded from data/processed/m3_inventory_position.parquet.
        """
        self._demand_result = demand_result
        self._inventory_result = inventory_result
        horizon = self.LEAD_TIME_MONTHS + self.REVIEW_PERIOD_MONTHS
        logger.info(
            f"InventoryPlanning: lead_time={self.LEAD_TIME_MONTHS}m | "
            f"review_period={self.REVIEW_PERIOD_MONTHS}m | "
            f"horizon={horizon}m"
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> InventoryPlanningResult:
        """Run all four sub-components and return a typed result.

        Returns:
            InventoryPlanningResult with all four DataFrames plus metadata.

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "lead_time_months": self.LEAD_TIME_MONTHS,
            "review_period_months": self.REVIEW_PERIOD_MONTHS,
            "horizon_months": self.LEAD_TIME_MONTHS + self.REVIEW_PERIOD_MONTHS,
            "service_levels": dict(self.SERVICE_LEVELS),
            "cv_poisson_threshold": self._CV_POISSON_THRESHOLD,
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 4: INVENTORY PLANNING")
        logger.info("=" * 60)

        # Load upstream data once, share across sub-components
        logger.info("Loading upstream data ...")
        fused = self._load_fused_demand()
        inv_pos = self._load_inventory_position()
        logger.info(
            f"  fused_demand: {fused.shape} | "
            f"inventory_position: {inv_pos.shape}"
        )
        metadata["fused_skus"] = int(fused["part_no"].nunique())
        metadata["inv_pos_skus"] = int(inv_pos["part_no"].nunique())

        # Sub-component 1: lead-time demand
        logger.info("Sub-component 1/4: lead-time demand")
        t1 = time.perf_counter()
        ltd = self._calc_lead_time_demand(fused)
        metadata["ltd_rows"] = len(ltd)
        logger.info(f"  -> {ltd.shape} in {time.perf_counter() - t1:.2f}s")

        # Sub-component 2: safety stock
        logger.info("Sub-component 2/4: safety stock")
        t2 = time.perf_counter()
        ss = self._calc_safety_stock(fused, ltd)
        metadata["ss_rows"] = len(ss)
        metadata["ss_normal_count"] = int((ss["ss_method"] == "Normal").sum())
        metadata["ss_poisson_count"] = int((ss["ss_method"] == "Poisson").sum())
        logger.info(
            f"  -> {ss.shape} | "
            f"Normal: {metadata['ss_normal_count']:,} | "
            f"Poisson: {metadata['ss_poisson_count']:,} | "
            f"in {time.perf_counter() - t2:.2f}s"
        )

        # Sub-component 3: reorder level
        logger.info("Sub-component 3/4: reorder level")
        t3 = time.perf_counter()
        rol = self._calc_reorder_level(ltd, ss)
        metadata["rol_rows"] = len(rol)
        metadata["rol_positive"] = int((rol["rol"] > 0).sum())
        logger.info(
            f"  -> {rol.shape} | "
            f"ROL > 0: {metadata['rol_positive']:,} | "
            f"in {time.perf_counter() - t3:.2f}s"
        )

        # Sub-component 4: planning table
        logger.info("Sub-component 4/4: planning table (join + signals)")
        t4 = time.perf_counter()
        planning = self._build_planning_table(ltd, ss, rol, inv_pos)
        n_signal = int(planning["signal_to_reorder"].sum())
        metadata["planning_rows"] = len(planning)
        metadata["signal_to_reorder_count"] = n_signal
        metadata["urgency_score_min"] = (
            float(planning["urgency_score"].min())
            if planning["urgency_score"].notna().any() else float("nan")
        )
        logger.info(
            f"  -> {planning.shape} | "
            f"signal_to_reorder: {n_signal:,} | "
            f"in {time.perf_counter() - t4:.2f}s"
        )

        # Sanity check: warn on ROL > 3× lead-time demand
        _check_sanity(rol, metadata)

        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info("MODULE 4 COMPLETE")
        logger.info(f"  Lead-time demand : {ltd.shape}")
        logger.info(f"  Safety stock     : {ss.shape}")
        logger.info(f"  Reorder level    : {rol.shape}")
        logger.info(f"  Planning table   : {planning.shape}")
        logger.info(f"  Parts signalling reorder: {n_signal:,}")
        logger.info(f"  Total time       : {elapsed:.2f}s")
        if metadata["warnings"]:
            for w in metadata["warnings"]:
                logger.warning(f"  WARN: {w}")
        logger.info("=" * 60)

        return InventoryPlanningResult(
            lead_time_demand=ltd,
            safety_stock=ss,
            reorder_level=rol,
            planning_table=planning,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Data loaders
    # ------------------------------------------------------------------

    def _load_fused_demand(self) -> pd.DataFrame:
        """Load fused demand from Module 2 result or saved parquet.

        Business meaning: the fused demand DataFrame contains per-SKU monthly
        demand estimates over the planning horizon, CV from ABC-XYZ-FSN
        classification, and the composite demand class code (e.g. 'AYF').

        Returns:
            DataFrame with columns [part_no, month, demand_qty, method, cv,
            demand_class].

        Raises:
            FileNotFoundError: If no in-memory result and parquet is missing.
        """
        if self._demand_result is not None:
            logger.debug("  Using in-memory Module 2 fused_demand")
            return self._demand_result.fused_demand.copy()
        if not _FUSED_DEMAND_PATH.exists():
            raise FileNotFoundError(
                f"m2_fused_demand.parquet not found at {_FUSED_DEMAND_PATH}. "
                "Run Module 2 first: python -m scripts.run_module 2 --save"
            )
        df = pd.read_parquet(_FUSED_DEMAND_PATH)
        logger.debug(f"  Loaded m2_fused_demand.parquet: {df.shape}")
        return df

    def _load_inventory_position(self) -> pd.DataFrame:
        """Load inventory position from Module 3 result or saved parquet.

        Business meaning: the inventory position captures the net supply picture
        per SKU (on-hand + pipeline − backorders) at the time of planning.

        Returns:
            DataFrame with columns [part_no, stock_qty, pipeline_qty,
            backorder_qty, net_position].

        Raises:
            FileNotFoundError: If no in-memory result and parquet is missing.
        """
        if self._inventory_result is not None:
            logger.debug("  Using in-memory Module 3 inventory_position")
            return self._inventory_result.inventory_position.copy()
        if not _INVENTORY_POS_PATH.exists():
            raise FileNotFoundError(
                f"m3_inventory_position.parquet not found at {_INVENTORY_POS_PATH}. "
                "Run Module 3 first: python -m scripts.run_module 3 --save"
            )
        df = pd.read_parquet(_INVENTORY_POS_PATH)
        logger.debug(f"  Loaded m3_inventory_position.parquet: {df.shape}")
        return df

    # ------------------------------------------------------------------
    # Sub-component 1: lead-time demand
    # ------------------------------------------------------------------

    def _calc_lead_time_demand(self, fused: pd.DataFrame) -> pd.DataFrame:
        """Compute per-SKU demand over the lead-time and review horizon.

        Business meaning: during the 3-month India import lead time, demand
        continues to be served from existing stock.  The lead-time demand is
        the expected quantity consumed while the replenishment order is in
        transit.  Adding one review period (1 month) gives the full horizon
        that the reorder level must cover.

        Formula:
          mean_monthly_demand = mean(demand_qty) over all forecast months
          lead_time_demand    = mean × LEAD_TIME_MONTHS        (3 months)
          review_demand       = mean × REVIEW_PERIOD_MONTHS    (1 month)
          horizon_demand      = lead_time_demand + review_demand (4 months)

        Args:
            fused: m2_fused_demand DataFrame with columns
                   [part_no, month, demand_qty, ...].

        Returns:
            DataFrame with columns:
                part_no             (str)   — SKU identifier
                mean_monthly_demand (float) — average monthly demand over horizon
                lead_time_demand    (float) — demand expected during lead time
                review_demand       (float) — demand expected in review period
                horizon_demand      (float) — total demand over lead time + review
        """
        L = self.LEAD_TIME_MONTHS
        R = self.REVIEW_PERIOD_MONTHS

        per_sku = (
            fused.groupby("part_no", sort=False)["demand_qty"]
            .mean()
            .reset_index(name="mean_monthly_demand")
        )
        per_sku["mean_monthly_demand"] = per_sku["mean_monthly_demand"].clip(lower=0.0)
        per_sku["lead_time_demand"] = (per_sku["mean_monthly_demand"] * L).round(4)
        per_sku["review_demand"] = (per_sku["mean_monthly_demand"] * R).round(4)
        per_sku["horizon_demand"] = (per_sku["lead_time_demand"] + per_sku["review_demand"]).round(4)

        logger.info(
            f"  Lead-time demand: {len(per_sku):,} SKUs | "
            f"mean lead_time_demand = {per_sku['lead_time_demand'].mean():.2f} | "
            f"max = {per_sku['lead_time_demand'].max():.2f}"
        )
        return per_sku[
            ["part_no", "mean_monthly_demand", "lead_time_demand", "review_demand", "horizon_demand"]
        ].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 2: safety stock
    # ------------------------------------------------------------------

    def _calc_safety_stock(
        self,
        fused: pd.DataFrame,
        ltd: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute per-SKU safety stock, wrapping the _ss_model formula logic.

        Business meaning: safety stock protects against demand variability and
        supplier lead-time uncertainty.  Higher-tier SKUs (A class) carry more
        safety stock because a stockout is more costly.

        Model selection:
          Normal  (CV ≤ 1.2 and CV is known):
            SS = Z(service_level) × σ_demand × √(lead_time_months)
            where σ_demand = mean_monthly_demand × CV

          Poisson (CV > 1.2 or CV unknown — intermittent / unclassified demand):
            SS = Z(service_level) × √(mean_monthly_demand × lead_time_months)
            (Poisson σ_lt = √(λ × L))

        The CV threshold _CV_POISSON_THRESHOLD = 1.2 is consistent with
        academic intermittent demand literature and the spirit of _ss_model.py's
        tiered ML vs classical split.

        Args:
            fused: m2_fused_demand with columns [part_no, cv, demand_class].
            ltd:   Lead-time demand DataFrame from _calc_lead_time_demand().

        Returns:
            DataFrame with columns:
                part_no       (str)
                safety_stock  (float) — computed SS ≥ 0
                service_level (float) — target service level (0–1)
                z_score       (float) — normal-distribution z for service_level
                sigma_demand  (float) — monthly demand std dev (Normal σ or Poisson √λ)
                demand_class  (str)   — compound ABC-XYZ-FSN code or "Unknown"
                ss_method     (str)   — "Normal" or "Poisson"
        """
        L = self.LEAD_TIME_MONTHS

        # Pull one row per SKU for cv and demand_class attributes
        sku_attrs = (
            fused[["part_no", "cv", "demand_class"]]
            .groupby("part_no", sort=False)
            .first()
            .reset_index()
        )

        df = ltd.merge(sku_attrs, on="part_no", how="left")
        df["demand_class"] = df["demand_class"].fillna("Unknown")

        # ABC class extraction: first character of compound code (e.g. "AYF" → "A")
        df["_abc"] = df["demand_class"].apply(_extract_abc_class)

        # Service level and z-score per ABC class
        df["service_level"] = df["_abc"].map(self.SERVICE_LEVELS).fillna(
            self.SERVICE_LEVELS["Unknown"]
        )
        df["z_score"] = df["service_level"].apply(
            lambda sl: round(float(_scipy_norm.ppf(sl)), 6)
        )

        # SS model selection: Poisson when CV > threshold or CV is missing
        poisson_mask: pd.Series = df["cv"].isna() | (df["cv"] > self._CV_POISSON_THRESHOLD)

        # Sigma: Normal uses mean × CV; Poisson uses √mean (per-month Poisson σ)
        normal_sigma = df["mean_monthly_demand"] * df["cv"].fillna(0.0)
        poisson_sigma = np.sqrt(df["mean_monthly_demand"].clip(lower=0.0))
        df["sigma_demand"] = np.where(poisson_mask, poisson_sigma, normal_sigma).round(6)

        # Safety stock computation
        # Normal:  Z × σ × √L
        # Poisson: Z × √(λ × L)
        normal_ss = df["z_score"] * df["sigma_demand"] * np.sqrt(L)
        poisson_ss = df["z_score"] * np.sqrt(df["mean_monthly_demand"].clip(lower=0.0) * L)

        df["safety_stock"] = (
            np.where(poisson_mask, poisson_ss, normal_ss)
            .clip(min=0.0)
            .round(4)
        )
        df["ss_method"] = np.where(poisson_mask, "Poisson", "Normal")

        logger.info(
            f"  Safety stock: mean SS = {df['safety_stock'].mean():.2f} | "
            f"max SS = {df['safety_stock'].max():.2f} | "
            f"SS = 0 count: {(df['safety_stock'] == 0).sum():,}"
        )
        return df[
            ["part_no", "safety_stock", "service_level", "z_score",
             "sigma_demand", "demand_class", "ss_method"]
        ].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 3: reorder level
    # ------------------------------------------------------------------

    def _calc_reorder_level(
        self,
        ltd: pd.DataFrame,
        ss: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute per-SKU reorder level: ROL = lead_time_demand + safety_stock.

        Business meaning: the ROL is the stock level at which the distributor
        must place an order with the Indian supplier so that, assuming average
        demand and a 3-month lead time, stock does not fall below the safety
        buffer before the shipment arrives.

        This wraps the formula in stage12_rol_roq.compute_rol(), adapted to
        the Module 4 DataFrame schema.

        Args:
            ltd: Lead-time demand DataFrame with [part_no, lead_time_demand].
            ss:  Safety stock DataFrame with [part_no, safety_stock].

        Returns:
            DataFrame with columns:
                part_no          (str)
                rol              (float) — reorder level ≥ 0
                lead_time_demand (float) — demand during lead time
                safety_stock     (float) — buffer stock component
        """
        df = ltd[["part_no", "lead_time_demand"]].merge(
            ss[["part_no", "safety_stock"]], on="part_no", how="left"
        )
        df["safety_stock"] = df["safety_stock"].fillna(0.0)
        df["rol"] = (df["lead_time_demand"] + df["safety_stock"]).clip(lower=0.0).round(4)

        logger.info(
            f"  ROL: mean = {df['rol'].mean():.2f} | "
            f"max = {df['rol'].max():.2f} | "
            f"ROL > 0: {(df['rol'] > 0).sum():,}"
        )
        return df[["part_no", "rol", "lead_time_demand", "safety_stock"]].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 4: planning table
    # ------------------------------------------------------------------

    def _build_planning_table(
        self,
        ltd: pd.DataFrame,
        ss: pd.DataFrame,
        rol: pd.DataFrame,
        inv_pos: pd.DataFrame,
    ) -> pd.DataFrame:
        """Join all sub-components with inventory position to build the planning view.

        Business meaning: the planning table is the single operational view that
        the inventory planner uses to decide what to order and how urgently.  It
        combines the forward-looking demand signal with the current stock reality.

        Columns added beyond the sub-components:
          stock_qty, pipeline_qty, backorder_qty, net_position  (from Module 3)
          signal_to_reorder (bool)  — True when net_position ≤ ROL and ROL > 0
          urgency_score (float)     — (net_position − ROL) / mean_monthly_demand
              Negative → already past ROL trigger (must order immediately)
              Positive → months of headroom before reaching ROL
              NaN → mean_monthly_demand == 0 (no demand, urgency undefined)

        Merge strategy: left join on part_no from the demand universe (ltd).
        SKUs with demand but no stock record receive stock values of 0,
        which correctly marks them as having fully depleted their stock.

        Args:
            ltd:     Lead-time demand (10,884 rows, demand universe).
            ss:      Safety stock per SKU.
            rol:     Reorder level per SKU.
            inv_pos: Inventory position from Module 3 (109,283 rows).

        Returns:
            Full planning DataFrame with all columns from ltd, ss (excluding
            redundant part_no merges), rol, and inventory position.
        """
        # Build from the demand universe (left anchor)
        table = ltd.merge(
            ss[["part_no", "safety_stock", "service_level", "z_score",
                "sigma_demand", "demand_class", "ss_method"]],
            on="part_no",
            how="left",
        )
        table = table.merge(
            rol[["part_no", "rol"]],
            on="part_no",
            how="left",
        )
        table = table.merge(
            inv_pos[["part_no", "stock_qty", "pipeline_qty", "backorder_qty", "net_position"]],
            on="part_no",
            how="left",
        )

        # Fill missing inventory values with 0 (demand exists but no stock record)
        for col in ("stock_qty", "pipeline_qty", "backorder_qty", "net_position"):
            table[col] = table[col].fillna(0.0)
        table["rol"] = table["rol"].fillna(0.0)
        table["safety_stock"] = table["safety_stock"].fillna(0.0)

        # signal_to_reorder: True when net_position ≤ ROL and ROL > 0
        # Guard ROL > 0 prevents false signals for zero-demand zero-stock parts.
        table["signal_to_reorder"] = (
            (table["net_position"] <= table["rol"]) & (table["rol"] > 0)
        )

        # urgency_score: (net_position − ROL) / mean_monthly_demand
        # Negative = already below ROL (must order now).
        # NaN = mean_monthly_demand == 0 (no expected demand, score undefined).
        safe_mean = table["mean_monthly_demand"].replace(0.0, np.nan)
        table["urgency_score"] = (
            (table["net_position"] - table["rol"]) / safe_mean
        ).round(4)

        # Column order: intuitive for planners
        col_order = [
            "part_no",
            "demand_class",
            "mean_monthly_demand",
            "lead_time_demand",
            "review_demand",
            "horizon_demand",
            "sigma_demand",
            "service_level",
            "z_score",
            "ss_method",
            "safety_stock",
            "rol",
            "stock_qty",
            "pipeline_qty",
            "backorder_qty",
            "net_position",
            "signal_to_reorder",
            "urgency_score",
        ]
        table = table[[c for c in col_order if c in table.columns]]

        n_signal = int(table["signal_to_reorder"].sum())
        logger.info(
            f"  Planning table: {len(table):,} SKUs | "
            f"signal_to_reorder: {n_signal:,} | "
            f"mean urgency_score (signalling): "
            f"{table.loc[table['signal_to_reorder'], 'urgency_score'].mean():.2f}"
            if n_signal > 0 else f"  Planning table: {len(table):,} SKUs | no signals"
        )
        return table.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_abc_class(demand_class: str | None) -> str:
    """Extract the ABC tier from a compound demand-class code.

    Business meaning: the ABC-XYZ-FSN classifier produces compound codes like
    'AYF' (A-class importance, Y-class variability, F-class movement speed).
    The first character is the ABC tier that drives the service-level target.

    Args:
        demand_class: Compound class code (e.g. 'AYF', 'CXN') or 'Unknown'.

    Returns:
        'A', 'B', 'C', or 'Unknown'.
    """
    if not demand_class or demand_class == "Unknown":
        return "Unknown"
    first = demand_class[0].upper()
    return first if first in ("A", "B", "C") else "Unknown"


def _check_sanity(
    rol: pd.DataFrame,
    metadata: dict[str, Any],
    multiplier: float = 3.0,
) -> None:
    """Warn if any ROL exceeds 3× its lead_time_demand.

    Business meaning (CLAUDE.md §15): a ROL more than 3× the lead-time demand
    indicates a potentially unreliable safety stock estimate (e.g. very short
    demand history or extreme CV).  These SKUs should be reviewed manually
    before the order is placed.

    Args:
        rol:        Reorder level DataFrame with [rol, lead_time_demand].
        metadata:   Mutable metadata dict; warnings and sanity count appended.
        multiplier: Flag threshold (default 3×, per CLAUDE.md).
    """
    active = rol[rol["lead_time_demand"] > 0].copy()
    if active.empty:
        metadata["sanity_flagged"] = 0
        return
    flagged = active[active["rol"] > active["lead_time_demand"] * multiplier]
    n = len(flagged)
    metadata["sanity_flagged"] = n
    if n > 0:
        msg = (
            f"{n:,} SKUs have ROL > {multiplier:.0f}× lead_time_demand "
            f"(max ROL = {flagged['rol'].max():.1f}). "
            "Review safety stock inputs for these SKUs."
        )
        metadata["warnings"].append(msg)
        logger.warning(f"  SANITY: {msg}")
    else:
        logger.info(f"  SANITY: all ROL values within {multiplier:.0f}× lead_time_demand")
