"""Module 5: Procurement Optimization.

Sub-components:
  1. Net Procurement Need    — how much to order per SKU
  2. Constraint Application  — budget, container, MOQ, supplier limits
  3. ROQ Calculation         — economic order quantity adjusted for constraints
  4. Import Recommendation   — final constrained order quantities per SKU

Design: extends the Module 4 reorder signal into actionable import order
quantities. Reads m4_planning_table.parquet or accepts an in-memory
InventoryPlanningResult.  All constraint parameters are class attributes
intentionally tunable at instantiation — Module 6 (dashboard) exposes them
as interactive controls so the planner can re-run the optimisation live.

Key business rules applied:
  - Constraint priority order: MOQ → Budget → Container
  - Budget constraint is SKIPPED when no unit_value_lkr column is present in
    the planning table.  All SKUs proceed with binding_constraint="none".
    TODO: enable once part cost / price data is available.
  - Container constraint is always applied (physical shipping limit).
  - Most urgent SKUs (lowest urgency_score — most below ROL) are served first
    in the greedy container-fill allocation; least urgent are reduced or dropped.
  - urgency_score < 0 → already below ROL → Priority 1 (Critical).

Inputs:
  data/processed/m4_planning_table.parquet  (or in-memory InventoryPlanningResult)

Outputs (returned in ProcurementOptimizationResult):
  net_need              : [part_no, net_need_qty, horizon_demand,
                           net_position, rationale]
  constrained_order     : [part_no, raw_roq, constrained_qty,
                           binding_constraint]
  import_recommendation : [part_no, order_qty, priority, urgency_score,
                           demand_class, mean_monthly_demand,
                           binding_constraint]
  constraint_summary    : dict of KPIs
  metadata              : run diagnostics
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from loguru import logger

from src.config.constants import (
    DEFAULT_HOLDING_COST_RATE,
    DEFAULT_ORDERING_COST,
    ORDER_CYCLE_DAYS,
)
from src.config.paths import DATA_PROCESSED

if TYPE_CHECKING:
    from src.modules.module4_inventory_planning import InventoryPlanningResult

# ---------------------------------------------------------------------------
# Module-level path
# ---------------------------------------------------------------------------

_PLANNING_TABLE_PATH = DATA_PROCESSED / "m4_planning_table.parquet"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProcurementOptimizationResult:
    """Typed container for all Module 5 outputs.

    Attributes:
        net_need:             Per-SKU procurement need (signaling SKUs only).
        constrained_order:    Per-SKU order quantities after all constraints.
        import_recommendation: Final sorted recommendation with priority tiers.
        constraint_summary:   Scalar KPIs about constraint binding.
        metadata:             Run diagnostics (counts, timings, warnings).
    """

    net_need: pd.DataFrame
    constrained_order: pd.DataFrame
    import_recommendation: pd.DataFrame
    constraint_summary: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ProcurementOptimization:
    """Module 5: Procurement Optimization.

    Converts the Module 4 reorder signal into actionable import order quantities
    subject to MOQ, budget, and container capacity constraints.

    All class attributes are exposed for interactive tuning by Module 6 dashboard.
    Pass override values at instantiation; do not subclass.

    Constraint application order (per business priority):
      1. MOQ      — hard floor on units per SKU per order (always applied)
      2. Budget   — LKR spend ceiling; most urgent SKUs funded first
                    (SKIPPED if no unit cost data available — logs TODO)
      3. Container — physical volume ceiling; greedy fill by urgency_score;
                    most urgent SKUs kept whole, least urgent reduced/dropped

    Safety stock model:
      EOQ when unit_value_lkr is present in planning table.
      Cover-period fallback (2 × REVIEW_PERIOD_MONTHS of mean demand) otherwise.
    """

    #: Default procurement budget in LKR (0 = no limit)
    BUDGET_LKR: float = 50_000_000

    #: Physical capacity of one standard 20-ft shipping container (cubic metres)
    CONTAINER_VOLUME_M3: float = 28.0

    #: Average volume per spare part unit (cubic metres)
    #: Default 0.002 m3 ≈ 2 litres — rough average across small-to-medium parts.
    #: Tune via dashboard or at instantiation once volumetric data is available.
    AVG_PART_VOLUME_M3: float = 0.002

    #: Minimum order quantity per SKU per shipment (supplier contract floor)
    MOQ: int = 1

    #: Review period in months (mirrors ORDER_CYCLE_DAYS / 30 from constants)
    REVIEW_PERIOD_MONTHS: int = ORDER_CYCLE_DAYS // 30

    def __init__(
        self,
        planning_result: "InventoryPlanningResult | None" = None,
        budget_lkr: float | None = None,
        container_volume_m3: float | None = None,
        moq: int | None = None,
        avg_part_volume_m3: float | None = None,
    ) -> None:
        """Initialise Module 5.

        Args:
            planning_result:     Module 4 result (InventoryPlanningResult).
                When provided, planning_table is read from result.planning_table.
                When None, loaded from data/processed/m4_planning_table.parquet.
            budget_lkr:          Override for BUDGET_LKR (0 = no limit).
            container_volume_m3: Override for CONTAINER_VOLUME_M3 (m3).
            moq:                 Override for MOQ (units per SKU).
            avg_part_volume_m3:  Override for AVG_PART_VOLUME_M3 (m3 per unit).
        """
        self._planning_result = planning_result
        if budget_lkr is not None:
            self.BUDGET_LKR = budget_lkr
        if container_volume_m3 is not None:
            self.CONTAINER_VOLUME_M3 = container_volume_m3
        if moq is not None:
            self.MOQ = moq
        if avg_part_volume_m3 is not None:
            self.AVG_PART_VOLUME_M3 = avg_part_volume_m3

        logger.info(
            f"ProcurementOptimization init | "
            f"budget={self.BUDGET_LKR:,.0f} LKR | "
            f"container={self.CONTAINER_VOLUME_M3:.1f} m3 | "
            f"MOQ={self.MOQ} | "
            f"avg_part_vol={self.AVG_PART_VOLUME_M3:.4f} m3"
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> ProcurementOptimizationResult:
        """Execute all four sub-components and return a typed result.

        Returns:
            ProcurementOptimizationResult with all DataFrames plus metadata.

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "budget_lkr": self.BUDGET_LKR,
            "container_volume_m3": self.CONTAINER_VOLUME_M3,
            "avg_part_volume_m3": self.AVG_PART_VOLUME_M3,
            "moq": self.MOQ,
            "review_period_months": self.REVIEW_PERIOD_MONTHS,
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 5: PROCUREMENT OPTIMIZATION")
        logger.info("=" * 60)

        # Load upstream data
        logger.info("Loading planning table ...")
        planning = self._load_planning_table()
        metadata["planning_table_rows"] = len(planning)
        metadata["planning_table_skus"] = int(planning["part_no"].nunique())
        logger.info(
            f"  planning_table: {planning.shape} | "
            f"{planning['signal_to_reorder'].sum():,} signaling SKUs"
        )

        # Sub-component 1: net procurement need
        logger.info("Sub-component 1/4: net procurement need")
        t1 = time.perf_counter()
        net_need = self._calc_net_need(planning)
        metadata["n_signaling"] = len(net_need)
        metadata["n_positive_need"] = int((net_need["net_need_qty"] > 0).sum())
        logger.info(f"  -> {net_need.shape} in {time.perf_counter() - t1:.2f}s")

        # Sub-component 2: raw ROQ
        logger.info("Sub-component 2/4: raw ROQ calculation")
        t2 = time.perf_counter()
        raw_roq = self._calc_raw_roq(net_need, planning)
        metadata["n_roq_rows"] = len(raw_roq)
        logger.info(f"  -> {raw_roq.shape} in {time.perf_counter() - t2:.2f}s")

        # Sub-component 3: constraint application
        logger.info("Sub-component 3/4: constraint application (MOQ → Budget → Container)")
        t3 = time.perf_counter()
        constrained, constraint_summary = self._apply_constraints(raw_roq, planning)
        if constraint_summary.get("budget_constraint_applied") is False and self.BUDGET_LKR > 0:
            metadata["warnings"].append(
                "Budget constraint skipped: no unit_value_lkr in planning table. "
                "TODO: add cost/price data to enable budget allocation."
            )
        logger.info(f"  -> {constrained.shape} in {time.perf_counter() - t3:.2f}s")

        # Sub-component 4: import recommendation
        logger.info("Sub-component 4/4: import recommendation")
        t4 = time.perf_counter()
        recommendation = self._build_import_recommendation(constrained, planning)
        metadata["n_recommendations"] = len(recommendation)
        metadata["n_to_order"] = int((recommendation["order_qty"] > 0).sum())
        logger.info(f"  -> {recommendation.shape} in {time.perf_counter() - t4:.2f}s")

        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info("MODULE 5 COMPLETE")
        logger.info(f"  Net need           : {net_need.shape}")
        logger.info(f"  Constrained order  : {constrained.shape}")
        logger.info(f"  Import recommend.  : {recommendation.shape}")
        logger.info(f"  SKUs to order      : {metadata['n_to_order']:,}")
        logger.info(
            f"  Container fill     : {constraint_summary.get('container_fill_pct', 0):.1f}%"
        )
        logger.info(f"  Total time         : {elapsed:.2f}s")
        if metadata["warnings"]:
            for w in metadata["warnings"]:
                logger.warning(f"  WARN: {w}")
        logger.info("=" * 60)

        return ProcurementOptimizationResult(
            net_need=net_need,
            constrained_order=constrained,
            import_recommendation=recommendation,
            constraint_summary=constraint_summary,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Data loader
    # ------------------------------------------------------------------

    def _load_planning_table(self) -> pd.DataFrame:
        """Load the Module 4 planning table from in-memory result or parquet.

        Business meaning: the planning table is the single upstream source of
        truth for reorder signals, urgency scores, and demand parameters
        produced by Module 4.  It is consumed here to drive procurement decisions.

        Returns:
            DataFrame with [part_no, mean_monthly_demand, horizon_demand,
            net_position, signal_to_reorder, urgency_score, demand_class,
            rol, stock_qty, pipeline_qty, backorder_qty, ...].

        Raises:
            FileNotFoundError: If no in-memory result and parquet is missing.
        """
        if self._planning_result is not None:
            logger.debug("  Using in-memory Module 4 planning_table")
            return self._planning_result.planning_table.copy()
        if not _PLANNING_TABLE_PATH.exists():
            raise FileNotFoundError(
                f"m4_planning_table.parquet not found at {_PLANNING_TABLE_PATH}. "
                "Run Module 4 first: python -m scripts.run_module 4 --save"
            )
        df = pd.read_parquet(_PLANNING_TABLE_PATH)
        logger.debug(f"  Loaded m4_planning_table.parquet: {df.shape}")
        return df

    # ------------------------------------------------------------------
    # Sub-component 1: net procurement need
    # ------------------------------------------------------------------

    def _calc_net_need(self, planning: pd.DataFrame) -> pd.DataFrame:
        """Compute per-SKU net procurement need for all signaling SKUs.

        Business meaning: the net need is the gap between expected demand over
        the planning horizon (lead time 3m + review period 1m = 4 months) and
        the current net inventory position (on-hand + pipeline − backorders).
        Only SKUs that have triggered the reorder signal (signal_to_reorder=True)
        are processed.

        Formula:
          net_need_qty = max(0, horizon_demand − net_position)

        Rationale classification:
          negative_stock        — net_position < 0 (stockout with open backorders)
          pipeline_insufficient — net_position >= 0, pipeline_qty > 0, still below ROL
                                  (in-transit stock reduces but does not eliminate gap)
          below_rol             — net_position >= 0, no pipeline, classic ROL trigger

        Args:
            planning: Module 4 planning table with [signal_to_reorder,
                      horizon_demand, net_position, pipeline_qty, rol].

        Returns:
            DataFrame with columns:
                part_no        (str)   — SKU identifier
                net_need_qty   (float) — units to procure (>= 0)
                horizon_demand (float) — expected demand over 4-month horizon
                net_position   (float) — current net inventory position
                rationale      (str)   — reason for reorder trigger
        """
        sig = planning[planning["signal_to_reorder"]].copy()

        if sig.empty:
            logger.warning("No SKUs with signal_to_reorder=True — net need DataFrame is empty.")
            return pd.DataFrame(
                columns=["part_no", "net_need_qty", "horizon_demand", "net_position", "rationale"]
            )

        # Net need: gap between horizon demand and current net position
        sig["net_need_qty"] = (
            sig["horizon_demand"] - sig["net_position"]
        ).clip(lower=0.0).round(4)

        # Rationale: explain why the reorder was triggered
        has_pipeline = "pipeline_qty" in sig.columns

        def _classify_rationale(row: pd.Series) -> str:
            """Business meaning: categorises the primary driver of the reorder signal."""
            net_pos = float(row["net_position"])
            if net_pos < 0:
                return "negative_stock"
            if has_pipeline and float(row.get("pipeline_qty", 0.0)) > 0:
                return "pipeline_insufficient"
            return "below_rol"

        sig["rationale"] = sig.apply(_classify_rationale, axis=1)

        result = sig[
            ["part_no", "net_need_qty", "horizon_demand", "net_position", "rationale"]
        ].copy().reset_index(drop=True)

        n_zero = int((result["net_need_qty"] == 0).sum())
        if n_zero > 0:
            logger.debug(
                f"  {n_zero:,} signaling SKUs have net_need_qty=0 "
                "(horizon_demand <= net_position — existing stock covers horizon demand)"
            )

        rationale_counts = result["rationale"].value_counts().to_dict()
        logger.info(
            f"  Net need: {len(result):,} signaling SKUs | "
            f"{(result['net_need_qty'] > 0).sum():,} with positive need | "
            f"total units needed = {result['net_need_qty'].sum():,.0f} | "
            f"rationale: {rationale_counts}"
        )
        return result

    # ------------------------------------------------------------------
    # Sub-component 2: raw ROQ
    # ------------------------------------------------------------------

    def _calc_raw_roq(
        self,
        net_need: pd.DataFrame,
        planning: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute the raw (pre-constraint) reorder quantity per SKU.

        Business meaning: the ROQ determines how many units to order per shipment
        to minimise total inventory cost (ordering cost + holding cost).  The EOQ
        formula is the theoretically optimal solution when unit cost is known.
        Without cost data, a cover-period heuristic is used: order at least
        2 × REVIEW_PERIOD_MONTHS of mean demand, which covers one full review cycle
        with buffer.  ROQ is always at least net_need_qty — we never order less
        than what the planning position requires.

        EOQ path (when unit_value_lkr is available in planning table):
          EOQ  = sqrt(2 × D_annual × ordering_cost / (unit_value × holding_rate))
          ROQ  = max(net_need_qty, EOQ)

        Cover-period fallback (no cost data):
          ROQ = max(net_need_qty, mean_monthly_demand × REVIEW_PERIOD_MONTHS × 2)

        Args:
            net_need: Net need DataFrame with [part_no, net_need_qty].
            planning: Planning table with [part_no, mean_monthly_demand, ...].

        Returns:
            DataFrame with columns:
                part_no      (str)   — SKU identifier
                raw_roq      (float) — reorder quantity (>= 1)
                eoq          (float) — EOQ value (0 when cost data unavailable)
                net_need_qty (float) — net need quantity from sub-component 1
        """
        # Only process SKUs with positive net need
        active = net_need[net_need["net_need_qty"] > 0].merge(
            planning[["part_no", "mean_monthly_demand"]],
            on="part_no",
            how="left",
        )

        if active.empty:
            logger.warning("No SKUs with positive net_need_qty — ROQ table is empty.")
            return pd.DataFrame(columns=["part_no", "raw_roq", "eoq", "net_need_qty"])

        # Check for usable unit cost column in planning table
        has_cost = (
            "unit_value_lkr" in planning.columns
            and (planning["unit_value_lkr"].notna() & (planning["unit_value_lkr"] > 0)).any()
        )

        if has_cost:
            # EOQ path: join cost, compute Economic Order Quantity
            active = active.merge(
                planning[["part_no", "unit_value_lkr"]],
                on="part_no",
                how="left",
            )
            active["unit_value_lkr"] = active["unit_value_lkr"].fillna(0.0)

            d_annual = active["mean_monthly_demand"].clip(lower=0.0) * 12.0
            holding = (active["unit_value_lkr"] * DEFAULT_HOLDING_COST_RATE).clip(lower=0.0)

            eoq_vals = np.where(
                holding >= 1.0,
                np.sqrt(
                    (2.0 * d_annual * DEFAULT_ORDERING_COST)
                    / holding.clip(lower=1.0)
                ),
                0.0,
            )
            active["eoq"] = np.round(eoq_vals, 4)
            active["raw_roq"] = np.maximum(
                active["net_need_qty"].values, active["eoq"].values
            ).round(4)

            logger.info(
                f"  ROQ (EOQ path): {len(active):,} SKUs | "
                f"mean EOQ = {active['eoq'].mean():.2f} | "
                f"mean ROQ = {active['raw_roq'].mean():.2f}"
            )

        else:
            # Cover-period fallback: no unit cost data available
            cover_months = self.REVIEW_PERIOD_MONTHS * 2
            logger.warning(
                "  No unit_value_lkr in planning table — "
                f"using cover-period fallback: ROQ = max(net_need, mean × {cover_months} months). "
                "TODO: add cost/price data to enable EOQ-based ROQ calculation."
            )
            active["eoq"] = 0.0
            cover_qty = (
                active["mean_monthly_demand"] * float(cover_months)
            ).clip(lower=0.0).round(4)
            active["raw_roq"] = np.maximum(
                active["net_need_qty"].values, cover_qty.values
            ).round(4)

            logger.info(
                f"  ROQ (cover-period fallback): {len(active):,} SKUs | "
                f"mean ROQ = {active['raw_roq'].mean():.2f} | "
                f"total ROQ units = {active['raw_roq'].sum():,.0f}"
            )

        # Clip: minimum 1 unit per active order line
        active["raw_roq"] = active["raw_roq"].clip(lower=1.0)

        return active[["part_no", "raw_roq", "eoq", "net_need_qty"]].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 3: constraint application
    # ------------------------------------------------------------------

    def _apply_constraints(
        self,
        raw_roq: pd.DataFrame,
        planning: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Apply MOQ, budget, and container constraints in priority order.

        Business meaning: procurement decisions are bounded by three real-world
        constraints applied in priority order:

          (a) MOQ — minimum order quantity per SKU (supplier contract term).
              Raises any raw_roq below MOQ to the MOQ floor.  Always applied.

          (b) Budget — total LKR spend ceiling.  Most urgent SKUs are funded first
              (greedy by urgency_score ascending).  SKIPPED when unit_value_lkr is
              not present in the planning table; a TODO warning is logged.

          (c) Container capacity — physical volume ceiling (CONTAINER_VOLUME_M3).
              Greedy fill by urgency: most urgent SKUs receive their full quantity;
              the first SKU that would overflow the container gets a floor-truncated
              partial quantity; all subsequent SKUs receive order_qty = 0
              (binding_constraint = "container").

        Container greedy-fill detail:
          1. Sort all non-zero-order SKUs by urgency_score ascending (most negative first).
          2. Accumulate volume: volume_i = order_qty_i × AVG_PART_VOLUME_M3.
          3. Cumulative volume at item n_full: last item fully within capacity.
          4. Item n_full+1: receives floor((remaining_m3) / AVG_PART_VOLUME_M3) units.
          5. Items n_full+2 onwards: order_qty = 0, binding_constraint = "container".

        Args:
            raw_roq: From _calc_raw_roq with [part_no, raw_roq, eoq, net_need_qty].
            planning: Module 4 planning table with [part_no, urgency_score, ...].

        Returns:
            Tuple of:
              - constrained DataFrame [part_no, raw_roq, constrained_qty,
                                       binding_constraint]
              - constraint_summary dict with scalar KPIs
        """
        # Base frame: join urgency for prioritisation
        df = raw_roq[["part_no", "raw_roq", "net_need_qty"]].merge(
            planning[["part_no", "urgency_score"]],
            on="part_no",
            how="left",
        )
        df["urgency_score"] = df["urgency_score"].fillna(0.0)
        df["order_qty"] = df["raw_roq"].copy()
        df["binding_constraint"] = "none"

        # ── (a) MOQ ──────────────────────────────────────────────────
        pre_moq = df["order_qty"].copy()
        df["order_qty"] = df["order_qty"].clip(lower=float(self.MOQ)).round(4)
        n_moq_applied = int((df["order_qty"] > pre_moq).sum())
        logger.info(
            f"  (a) MOQ={self.MOQ}: {n_moq_applied:,} SKUs raised to floor"
        )

        # ── (b) Budget constraint ────────────────────────────────────
        has_cost = (
            "unit_value_lkr" in planning.columns
            and (planning["unit_value_lkr"].notna() & (planning["unit_value_lkr"] > 0)).any()
        )
        budget_used = 0.0
        n_budget_skipped = 0
        budget_applied = False

        if self.BUDGET_LKR > 0 and has_cost:
            budget_applied = True
            cost_lookup = planning.set_index("part_no")["unit_value_lkr"]
            df["_uv"] = df["part_no"].map(cost_lookup).fillna(0.0)

            # Sort most urgent first for greedy allocation
            df = df.sort_values("urgency_score", ascending=True).reset_index(drop=True)
            cum_cost = (df["order_qty"] * df["_uv"]).cumsum()
            over_mask = cum_cost > self.BUDGET_LKR

            if over_mask.any():
                first_over = int(over_mask.idxmax())
                df.loc[first_over:, "order_qty"] = 0.0
                df.loc[first_over:, "binding_constraint"] = "budget"
                n_budget_skipped = int(len(df) - first_over)

            budget_used = float(
                (df.loc[df["binding_constraint"] != "budget", "order_qty"]
                 * df.loc[df["binding_constraint"] != "budget", "_uv"]).sum()
            )
            df = df.drop(columns=["_uv"])
            logger.info(
                f"  (b) Budget: {n_budget_skipped:,} SKUs cut | "
                f"LKR {budget_used:,.0f} of {self.BUDGET_LKR:,.0f} allocated"
            )

        elif self.BUDGET_LKR > 0 and not has_cost:
            logger.warning(
                "  (b) Budget constraint SKIPPED: unit_value_lkr not found in planning "
                "table.  All SKUs proceed to container constraint unconstrained by budget. "
                "TODO: add cost/price data to activate this constraint."
            )
        else:
            logger.info("  (b) Budget constraint disabled (BUDGET_LKR = 0)")

        # ── (c) Container constraint: greedy fill by urgency ─────────
        n_container_constrained = 0
        total_vol_needed = float((df["order_qty"] * self.AVG_PART_VOLUME_M3).sum())
        total_vol_allocated = total_vol_needed

        if self.CONTAINER_VOLUME_M3 > 0:
            # Work on SKUs that still have a non-zero order quantity
            active_mask = df["order_qty"] > 0
            active_df = (
                df[active_mask]
                .sort_values("urgency_score", ascending=True)
                .reset_index(drop=True)
                .copy()
            )

            if not active_df.empty:
                vol_per_unit = self.AVG_PART_VOLUME_M3
                volumes = (active_df["order_qty"] * vol_per_unit).values
                cum_vol = np.cumsum(volumes)
                total_active_vol = float(cum_vol[-1])
                container_pct_needed = (total_active_vol / self.CONTAINER_VOLUME_M3) * 100.0

                logger.info(
                    f"  (c) Container: {total_active_vol:.2f} m3 needed | "
                    f"{self.CONTAINER_VOLUME_M3:.1f} m3 capacity | "
                    f"{container_pct_needed:.1f}% required"
                )

                if total_active_vol > self.CONTAINER_VOLUME_M3:
                    # Number of SKUs whose cumulative volume fits entirely
                    fits_mask = cum_vol <= self.CONTAINER_VOLUME_M3
                    n_full_fit = int(fits_mask.sum())

                    # Remaining volume after the last fully-fitting SKU
                    prev_cumvol = float(cum_vol[n_full_fit - 1]) if n_full_fit > 0 else 0.0
                    remaining_vol = self.CONTAINER_VOLUME_M3 - prev_cumvol

                    # Partial quantity for the overflow SKU at position n_full_fit
                    if n_full_fit < len(active_df):
                        partial_qty = math.floor(remaining_vol / vol_per_unit)
                        active_df.at[n_full_fit, "order_qty"] = max(0.0, float(partial_qty))
                        active_df.at[n_full_fit, "binding_constraint"] = "container"

                        # All subsequent SKUs: drop
                        if n_full_fit + 1 < len(active_df):
                            active_df.loc[n_full_fit + 1 :, "order_qty"] = 0.0
                            active_df.loc[
                                n_full_fit + 1 :, "binding_constraint"
                            ] = "container"

                    n_container_constrained = int(len(active_df) - n_full_fit)
                    n_dropped = max(0, n_container_constrained - 1)
                    logger.info(
                        f"  (c) Container applied: {n_full_fit:,} SKUs fit fully | "
                        f"1 partial (qty={max(0, math.floor(remaining_vol / vol_per_unit))}) | "
                        f"{n_dropped:,} SKUs dropped"
                    )

                    # Write updates back to df using part_no as key
                    order_qty_map = active_df.set_index("part_no")["order_qty"].to_dict()
                    binding_map = (
                        active_df.set_index("part_no")["binding_constraint"].to_dict()
                    )

                    new_qty = df["part_no"].map(order_qty_map)
                    new_binding = df["part_no"].map(binding_map)

                    qty_update_mask = new_qty.notna()
                    binding_update_mask = new_binding.notna()

                    df.loc[qty_update_mask, "order_qty"] = new_qty[qty_update_mask].values
                    df.loc[binding_update_mask, "binding_constraint"] = (
                        new_binding[binding_update_mask].values
                    )

                else:
                    logger.info(
                        f"  (c) Container: {container_pct_needed:.1f}% fill — "
                        "all SKUs fit, no reduction applied"
                    )

            total_vol_allocated = float((df["order_qty"] * self.AVG_PART_VOLUME_M3).sum())

        # Final constrained_qty
        df["constrained_qty"] = df["order_qty"].round(4)

        # ── Constraint summary ──────────────────────────────────────
        actual_container_pct = (
            (total_vol_allocated / self.CONTAINER_VOLUME_M3) * 100.0
            if self.CONTAINER_VOLUME_M3 > 0 else 0.0
        )
        n_to_order = int((df["constrained_qty"] > 0).sum())
        n_skipped = int((df["constrained_qty"] == 0).sum())

        constraint_summary: dict[str, Any] = {
            "budget_used_lkr": round(budget_used, 2),
            "budget_limit_lkr": self.BUDGET_LKR,
            "budget_constraint_applied": budget_applied,
            "budget_cost_data_available": has_cost,
            "container_vol_needed_m3": round(total_vol_needed, 4),
            "container_vol_allocated_m3": round(total_vol_allocated, 4),
            "container_fill_pct": round(actual_container_pct, 2),
            "n_moq_applied": n_moq_applied,
            "n_budget_skipped": n_budget_skipped,
            "n_container_constrained": n_container_constrained,
            "n_skipped_total": n_skipped,
            "n_to_order": n_to_order,
            "total_units_to_order": float(df["constrained_qty"].sum()),
        }

        logger.info(
            f"  Constraints complete: to_order={n_to_order:,} | "
            f"total_units={constraint_summary['total_units_to_order']:,.0f} | "
            f"container={actual_container_pct:.1f}% | "
            f"skipped={n_skipped:,}"
        )

        return (
            df[["part_no", "raw_roq", "constrained_qty", "binding_constraint"]].reset_index(
                drop=True
            ),
            constraint_summary,
        )

    # ------------------------------------------------------------------
    # Sub-component 4: import recommendation
    # ------------------------------------------------------------------

    def _build_import_recommendation(
        self,
        constrained: pd.DataFrame,
        planning: pd.DataFrame,
    ) -> pd.DataFrame:
        """Build the final import recommendation with priority tiers.

        Business meaning: the import recommendation is the actionable document
        sent to the procurement team.  Priority tiers ensure that the most
        critical spare parts are ordered first when capacity is limited.

        Priority tier logic (based on urgency_score from Module 4):
          1 = Critical — urgency_score <  0  (already below ROL — order immediately)
          2 = High     — 0 <= urgency_score <  2  (approaching ROL within 2 months)
          3 = Medium   — 2 <= urgency_score <  6  (comfortable but needs planning)
          4 = Low      — urgency_score >= 6  (ample stock coverage)

        Note on priority distribution: all signaling SKUs (signal_to_reorder=True)
        have urgency_score <= 0 by definition, so procurement rows will be
        predominantly Priority 1.  Priority 2–4 appear only when non-signaling
        SKUs are included in future extensions of this module.

        Sort order: priority ascending, then urgency_score ascending
        (most critical first within each tier).

        Args:
            constrained: Constrained order DataFrame [part_no, raw_roq,
                         constrained_qty, binding_constraint].
            planning:    Module 4 planning table for urgency_score, demand_class,
                         mean_monthly_demand.

        Returns:
            DataFrame with columns:
                part_no              (str)   — SKU identifier
                order_qty            (float) — final quantity to order
                priority             (int)   — 1=Critical, 2=High, 3=Medium, 4=Low
                urgency_score        (float) — from Module 4
                demand_class         (str)   — ABC-XYZ-FSN code
                mean_monthly_demand  (float) — average monthly demand
                binding_constraint   (str)   — tightest applied constraint
            Sorted: priority asc, urgency_score asc.
        """
        rec = constrained.merge(
            planning[["part_no", "urgency_score", "demand_class", "mean_monthly_demand"]],
            on="part_no",
            how="left",
        )

        def _assign_priority(score: float) -> int:
            """Business meaning: maps urgency_score to a 1–4 procurement priority tier."""
            if score < 0.0:
                return 1   # Critical
            if score < 2.0:
                return 2   # High
            if score < 6.0:
                return 3   # Medium
            return 4       # Low

        rec["priority"] = (
            rec["urgency_score"].fillna(0.0).apply(_assign_priority).astype(int)
        )

        # Rename constrained_qty → order_qty for final output clarity
        rec = rec.rename(columns={"constrained_qty": "order_qty"})

        # Sort: priority asc, urgency_score asc (most critical first)
        rec = rec.sort_values(
            ["priority", "urgency_score"], ascending=[True, True]
        ).reset_index(drop=True)

        col_order = [
            "part_no",
            "order_qty",
            "priority",
            "urgency_score",
            "demand_class",
            "mean_monthly_demand",
            "binding_constraint",
        ]
        rec = rec[[c for c in col_order if c in rec.columns]]

        priority_dist = rec["priority"].value_counts().sort_index().to_dict()
        n_to_order = int((rec["order_qty"] > 0).sum())
        logger.info(
            f"  Recommendation: {len(rec):,} rows | "
            f"to_order={n_to_order:,} | "
            f"priority distribution: {priority_dist}"
        )
        return rec
