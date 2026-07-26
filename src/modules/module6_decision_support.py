"""Module 6: Decision Support.

Sub-components:
  1. Fill Rate              -- expected % of demand met from current stock + pipeline
  2. Purchase Recommendations -- final actionable list with priority, risk, action
  3. Stockout Risk          -- probability of stockout before next order arrives
  4. Overstock Risk         -- parts with excess inventory relative to demand

Inputs:
  data/processed/m4_planning_table.parquet
  data/processed/m5_import_recommendation.parquet

Outputs (returned in DecisionSupportResult, saved via run_module.py):
  recommendations: [part_no, order_qty, priority, urgency_score, demand_class,
                    abc, mean_monthly_demand, binding_constraint,
                    risk_level, action, fill_rate_pct]
  stockout_risk:   [part_no, risk_pct, days_until_stockout, urgency_score]
  overstock_risk:  [part_no, excess_qty, months_cover, demand_class]
  fill_rate:       [part_no, fill_rate_pct, stock_qty, monthly_demand, horizon_demand]
  summary:         KPI dict for dashboard header
  metadata:        run diagnostics
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_PROCESSED

if TYPE_CHECKING:
    from src.modules.module4_inventory_planning import InventoryPlanningResult
    from src.modules.module5_procurement_optimization import ProcurementOptimizationResult

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_PLANNING_TABLE_PATH = DATA_PROCESSED / "m4_planning_table.parquet"
_IMPORT_REC_PATH = DATA_PROCESSED / "m5_import_recommendation.parquet"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DecisionSupportResult:
    """Typed container for all Module 6 outputs.

    Attributes:
        recommendations: Actionable purchase list with priority, risk label, and action.
        stockout_risk:   All SKUs ranked by P(stockout) before next order.
        overstock_risk:  SKUs with inventory exceeding OVERSTOCK_THRESHOLD_MONTHS of demand.
        fill_rate:       Expected fill-rate per SKU over the 4-month planning horizon.
        summary:         Scalar KPIs for the dashboard header cards.
        metadata:        Run diagnostics (counts, timings, warnings).
    """

    recommendations: pd.DataFrame
    stockout_risk: pd.DataFrame
    overstock_risk: pd.DataFrame
    fill_rate: pd.DataFrame
    summary: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class DecisionSupport:
    """Module 6: Decision Support.

    Converts Module 5 procurement recommendations into an actionable dashboard
    with risk signals (stockout / overstock) and fill-rate projections per SKU.

    All class attributes are tunable at instantiation for interactive use via
    the Module 6 dashboard.  Do not subclass; pass override values at init.

    Sub-component execution order (dependencies):
      1. _calc_fill_rate()         -- uses m4 planning table
      2. _build_recommendations()  -- uses m5 recommendation + fill_rate output
      3. _calc_stockout_risk()     -- uses m4 planning table
      4. _calc_overstock_risk()    -- uses m4 planning table
      5. _build_summary()          -- aggregates outputs from 1-4
    """

    #: Flag SKU as high-stockout-risk if P(stockout) > 30%
    STOCKOUT_RISK_THRESHOLD: float = 0.30

    #: Flag SKU as overstock if net_position covers > 12 months of mean demand
    OVERSTOCK_THRESHOLD_MONTHS: float = 12.0

    #: Planning horizon: lead time (3m) + review period (1m)
    HORIZON_MONTHS: float = 4.0

    #: Average part volume used for container utilisation estimate (m3/unit)
    AVG_PART_VOLUME_M3: float = 0.002

    #: Standard 20-ft container volume (m3)
    CONTAINER_VOLUME_M3: float = 28.0

    def __init__(
        self,
        procurement_result: "ProcurementOptimizationResult | None" = None,
        planning_result: "InventoryPlanningResult | None" = None,
        stockout_threshold: float | None = None,
        overstock_months: float | None = None,
        horizon_months: float | None = None,
    ) -> None:
        """Initialise Module 6.

        Args:
            procurement_result: Module 5 result (reads parquet when None).
            planning_result:    Module 4 result (reads parquet when None).
            stockout_threshold: Override for STOCKOUT_RISK_THRESHOLD [0, 1].
            overstock_months:   Override for OVERSTOCK_THRESHOLD_MONTHS.
            horizon_months:     Override for HORIZON_MONTHS.
        """
        self._procurement_result = procurement_result
        self._planning_result = planning_result
        if stockout_threshold is not None:
            self.STOCKOUT_RISK_THRESHOLD = stockout_threshold
        if overstock_months is not None:
            self.OVERSTOCK_THRESHOLD_MONTHS = overstock_months
        if horizon_months is not None:
            self.HORIZON_MONTHS = horizon_months

        logger.info(
            f"DecisionSupport init | "
            f"stockout_threshold={self.STOCKOUT_RISK_THRESHOLD:.0%} | "
            f"overstock_months={self.OVERSTOCK_THRESHOLD_MONTHS:.0f} | "
            f"horizon_months={self.HORIZON_MONTHS:.0f}"
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> DecisionSupportResult:
        """Execute all sub-components in dependency order and return a typed result.

        Returns:
            DecisionSupportResult with all DataFrames plus KPI summary and metadata.

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "stockout_threshold": self.STOCKOUT_RISK_THRESHOLD,
            "overstock_months": self.OVERSTOCK_THRESHOLD_MONTHS,
            "horizon_months": self.HORIZON_MONTHS,
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 6: DECISION SUPPORT")
        logger.info("=" * 60)

        # Load upstream data
        logger.info("Loading upstream parquets ...")
        planning = self._load_planning_table()
        import_rec = self._load_import_recommendation()
        metadata["planning_rows"] = len(planning)
        metadata["import_rec_rows"] = len(import_rec)
        logger.info(f"  planning_table: {planning.shape} | import_rec: {import_rec.shape}")

        # Sub-component 1: fill rate (computed first — used by recommendations)
        logger.info("Sub-component 1/4: fill rate")
        t1 = time.perf_counter()
        fill_rate = self._calc_fill_rate(planning)
        metadata["n_fill_rate_skus"] = len(fill_rate)
        logger.info(f"  -> {fill_rate.shape} in {time.perf_counter() - t1:.2f}s")

        # Sub-component 2: purchase recommendations
        logger.info("Sub-component 2/4: purchase recommendations")
        t2 = time.perf_counter()
        recommendations = self._build_recommendations(import_rec, fill_rate)
        metadata["n_recommendations"] = len(recommendations)
        metadata["n_to_order"] = int((recommendations["order_qty"] > 0).sum())
        logger.info(f"  -> {recommendations.shape} in {time.perf_counter() - t2:.2f}s")

        # Sub-component 3: stockout risk
        logger.info("Sub-component 3/4: stockout risk")
        t3 = time.perf_counter()
        stockout_risk = self._calc_stockout_risk(planning)
        metadata["n_high_stockout_risk"] = int(
            (stockout_risk["risk_pct"] > self.STOCKOUT_RISK_THRESHOLD * 100).sum()
        )
        logger.info(f"  -> {stockout_risk.shape} in {time.perf_counter() - t3:.2f}s")

        # Sub-component 4: overstock risk
        logger.info("Sub-component 4/4: overstock risk")
        t4 = time.perf_counter()
        overstock_risk = self._calc_overstock_risk(planning)
        metadata["n_overstock"] = len(overstock_risk)
        logger.info(f"  -> {overstock_risk.shape} in {time.perf_counter() - t4:.2f}s")

        # Build summary KPIs
        summary = self._build_summary(recommendations, stockout_risk, overstock_risk, fill_rate)

        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info("MODULE 6 COMPLETE")
        logger.info(f"  Recommendations   : {recommendations.shape}")
        logger.info(f"  Stockout risk     : {stockout_risk.shape}")
        logger.info(f"  Overstock risk    : {overstock_risk.shape}")
        logger.info(f"  Fill rate         : {fill_rate.shape}")
        logger.info(f"  Total time        : {elapsed:.2f}s")
        if metadata["warnings"]:
            for w in metadata["warnings"]:
                logger.warning(f"  WARN: {w}")
        logger.info("=" * 60)

        return DecisionSupportResult(
            recommendations=recommendations,
            stockout_risk=stockout_risk,
            overstock_risk=overstock_risk,
            fill_rate=fill_rate,
            summary=summary,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Data loaders
    # ------------------------------------------------------------------

    def _load_planning_table(self) -> pd.DataFrame:
        """Load Module 4 planning table from in-memory result or parquet.

        Business meaning: the planning table is the single source of truth for
        inventory positions, ROL values, urgency scores, and demand parameters
        produced by Module 4.

        Returns:
            DataFrame with [part_no, demand_class, mean_monthly_demand, rol,
            stock_qty, pipeline_qty, backorder_qty, net_position,
            signal_to_reorder, urgency_score, horizon_demand, ...].

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

    def _load_import_recommendation(self) -> pd.DataFrame:
        """Load Module 5 import recommendation from in-memory result or parquet.

        Business meaning: the import recommendation is the constrained list of
        SKUs to order, with quantities, procurement priorities, and urgency scores
        as produced by Module 5 after MOQ / budget / container constraint application.

        Returns:
            DataFrame with [part_no, order_qty, priority, urgency_score,
            demand_class, mean_monthly_demand, binding_constraint].

        Raises:
            FileNotFoundError: If no in-memory result and parquet is missing.
        """
        if self._procurement_result is not None:
            logger.debug("  Using in-memory Module 5 import_recommendation")
            return self._procurement_result.import_recommendation.copy()
        if not _IMPORT_REC_PATH.exists():
            raise FileNotFoundError(
                f"m5_import_recommendation.parquet not found at {_IMPORT_REC_PATH}. "
                "Run Module 5 first: python -m scripts.run_module 5 --save"
            )
        df = pd.read_parquet(_IMPORT_REC_PATH)
        logger.debug(f"  Loaded m5_import_recommendation.parquet: {df.shape}")
        return df

    # ------------------------------------------------------------------
    # Sub-component 1: fill rate
    # ------------------------------------------------------------------

    def _calc_fill_rate(self, planning: pd.DataFrame) -> pd.DataFrame:
        """Compute expected fill rate per SKU from current stock and pipeline.

        Business meaning: fill rate measures what percentage of expected demand
        over the next planning horizon (lead time 3m + review period 1m = 4m)
        can be met from the current net inventory position.  A fill rate below
        100% signals stockout risk within the next order cycle.

        Zero-demand SKUs (mean_monthly_demand = 0) are assigned fill_rate_pct = 100
        because there is no demand to miss — no shortfall is possible.

        Formula:
          horizon_demand = mean_monthly_demand × HORIZON_MONTHS
          fill_rate_pct  = clip(net_position / horizon_demand, 0, 1) × 100

        Summary fill rate = weighted average of fill_rate_pct by monthly_demand.

        Args:
            planning: Module 4 planning table with [part_no, net_position,
                      mean_monthly_demand, stock_qty].

        Returns:
            DataFrame with columns:
                part_no         (str)   — SKU identifier
                fill_rate_pct   (float) — fill rate in percent [0, 100]
                stock_qty       (float) — on-hand stock quantity
                monthly_demand  (float) — mean monthly demand
                horizon_demand  (float) — total demand over HORIZON_MONTHS
            Sorted by fill_rate_pct ascending (worst coverage first).
        """
        df = planning[
            ["part_no", "net_position", "mean_monthly_demand", "stock_qty"]
        ].copy()

        horizon_demand = (df["mean_monthly_demand"] * self.HORIZON_MONTHS).clip(lower=0.0)

        # fill rate: clip to [0, 1] then scale to percent
        # SKUs with zero horizon_demand get 100% fill rate (no demand to miss)
        fill = np.where(
            horizon_demand > 0,
            np.clip(df["net_position"].values / horizon_demand.values, 0.0, 1.0) * 100.0,
            100.0,
        )

        df["fill_rate_pct"] = np.round(fill, 2)
        df["horizon_demand"] = horizon_demand.round(4)
        df = df.rename(columns={"mean_monthly_demand": "monthly_demand"})

        n_below_50 = int((df["fill_rate_pct"] < 50).sum())
        n_zero = int((df["fill_rate_pct"] == 0).sum())
        logger.info(
            f"  Fill rate: {len(df):,} SKUs | "
            f"mean={df['fill_rate_pct'].mean():.1f}% | "
            f"<50%: {n_below_50:,} | at 0%: {n_zero:,}"
        )

        return (
            df[["part_no", "fill_rate_pct", "stock_qty", "monthly_demand", "horizon_demand"]]
            .sort_values("fill_rate_pct", ascending=True)
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # Sub-component 2: purchase recommendations
    # ------------------------------------------------------------------

    def _build_recommendations(
        self,
        import_rec: pd.DataFrame,
        fill_rate: pd.DataFrame,
    ) -> pd.DataFrame:
        """Build final actionable purchase recommendation list.

        Business meaning: the purchase recommendation is the operational document
        sent to the procurement team.  This sub-component adds human-readable
        risk labels, action directives, and fill-rate context to the Module 5
        import recommendation.  Only SKUs with order_qty > 0 are retained.

        Risk level mapping:
          Priority 1 (urgency_score <  0)  → Critical
          Priority 2 (0 <= score < 2)      → High
          Priority 3 (2 <= score < 6)      → Medium
          Priority 4 (score >= 6)          → Low

        Action directive mapping:
          Priority 1 or 2 → "Order Now"
          Priority 3       → "Plan Order"
          Priority 4       → "Monitor"

        ABC extraction: first character of demand_class when it is 'A', 'B', or 'C';
        otherwise 'Unknown'.  This enables client-side filtering by ABC tier.

        Args:
            import_rec: Module 5 import_recommendation DataFrame [part_no, order_qty,
                        priority, urgency_score, demand_class, mean_monthly_demand,
                        binding_constraint].
            fill_rate: Fill rate DataFrame (output of _calc_fill_rate) providing
                       fill_rate_pct per part_no.

        Returns:
            DataFrame with columns:
                part_no, order_qty, priority, urgency_score, demand_class,
                abc, mean_monthly_demand, binding_constraint,
                risk_level, action, fill_rate_pct
            Only rows where order_qty > 0.
            Sorted: priority asc, urgency_score asc (most critical first).
        """
        rec = import_rec.copy()

        # Retain only actionable SKUs
        rec = rec[rec["order_qty"] > 0].copy()
        if rec.empty:
            logger.warning("  No SKUs with order_qty > 0 in import_recommendation.")
            return pd.DataFrame(
                columns=[
                    "part_no", "order_qty", "priority", "urgency_score",
                    "demand_class", "abc", "mean_monthly_demand",
                    "binding_constraint", "risk_level", "action", "fill_rate_pct",
                ]
            )

        _RISK_LABELS: dict[int, str] = {1: "Critical", 2: "High", 3: "Medium", 4: "Low"}
        _ACTIONS: dict[int, str] = {1: "Order Now", 2: "Order Now", 3: "Plan Order", 4: "Monitor"}

        def _risk_level(p: int) -> str:
            """Business meaning: maps procurement priority tier to a human-readable risk label."""
            return _RISK_LABELS.get(p, "Low")

        def _action(p: int) -> str:
            """Business meaning: maps procurement priority to an actionable directive for planners."""
            return _ACTIONS.get(p, "Monitor")

        rec["risk_level"] = rec["priority"].apply(_risk_level)
        rec["action"] = rec["priority"].apply(_action)

        # ABC: extract first character of demand_class when it is a valid ABC letter
        first_char = rec["demand_class"].str[0]
        rec["abc"] = first_char.where(first_char.isin(["A", "B", "C"]), other="Unknown")

        # Join fill_rate_pct from sub-component 1
        fr_lookup = fill_rate.set_index("part_no")["fill_rate_pct"]
        rec["fill_rate_pct"] = rec["part_no"].map(fr_lookup).fillna(0.0).round(2)

        # Sort: most critical first
        rec = rec.sort_values(["priority", "urgency_score"], ascending=[True, True]).reset_index(
            drop=True
        )

        col_order = [
            "part_no", "order_qty", "priority", "urgency_score",
            "demand_class", "abc", "mean_monthly_demand",
            "binding_constraint", "risk_level", "action", "fill_rate_pct",
        ]
        rec = rec[[c for c in col_order if c in rec.columns]]

        priority_dist = rec["priority"].value_counts().sort_index().to_dict()
        logger.info(
            f"  Recommendations: {len(rec):,} SKUs (order_qty>0) | "
            f"priority dist: {priority_dist}"
        )
        return rec

    # ------------------------------------------------------------------
    # Sub-component 3: stockout risk
    # ------------------------------------------------------------------

    def _calc_stockout_risk(self, planning: pd.DataFrame) -> pd.DataFrame:
        """Compute probability of stockout before next order for all SKUs.

        Business meaning: the sigmoid of the negative urgency score maps the
        Module 4 urgency signal to a continuous probability.  A SKU that is
        deeply below its ROL (urgency_score << 0) has risk ≈ 100%; one well
        above its ROL (urgency_score >> 0) has risk ≈ 0%.

        Formula:
          risk_pct            = 1 / (1 + exp(urgency_score)) × 100
          days_until_stockout = urgency_score × 30
                                (negative ⇒ already below ROL / in stockout)

        The urgency_score is clamped to ±500 to prevent float overflow in exp().

        Args:
            planning: Module 4 planning table with [part_no, urgency_score,
                      mean_monthly_demand].

        Returns:
            DataFrame with columns:
                part_no             (str)   — SKU identifier
                risk_pct            (float) — P(stockout) in percent [0, 100]
                days_until_stockout (float) — days until projected stockout
                urgency_score       (float) — from Module 4 (negative = below ROL)
            Sorted by risk_pct descending.
        """
        df = planning[["part_no", "urgency_score", "mean_monthly_demand"]].copy()
        df["urgency_score"] = df["urgency_score"].fillna(0.0)

        # Clamp to avoid float64 overflow in np.exp()
        u_clamped = df["urgency_score"].clip(-500.0, 500.0).values
        df["risk_pct"] = np.round((1.0 / (1.0 + np.exp(u_clamped))) * 100.0, 2)
        df["days_until_stockout"] = (df["urgency_score"] * 30.0).round(1)

        df = df[["part_no", "risk_pct", "days_until_stockout", "urgency_score"]]
        df = df.sort_values("risk_pct", ascending=False).reset_index(drop=True)

        threshold_pct = self.STOCKOUT_RISK_THRESHOLD * 100.0
        high_risk = int((df["risk_pct"] > threshold_pct).sum())
        logger.info(
            f"  Stockout risk: {len(df):,} SKUs | "
            f"high risk (>{threshold_pct:.0f}%): {high_risk:,}"
        )
        return df

    # ------------------------------------------------------------------
    # Sub-component 4: overstock risk
    # ------------------------------------------------------------------

    def _calc_overstock_risk(self, planning: pd.DataFrame) -> pd.DataFrame:
        """Identify SKUs with excess inventory relative to demand.

        Business meaning: overstock ties up working capital and warehouse space.
        A SKU is flagged when its current net inventory position covers more than
        OVERSTOCK_THRESHOLD_MONTHS (default 12) months of mean monthly demand.
        Excess quantity is the stock above the 12-month threshold level.

        Zero-demand SKUs with positive stock (true dead stock) are also flagged —
        their months_cover is capped at 9999.9 for display purposes.

        Formulas:
          months_cover = net_position / mean_monthly_demand
          is_overstock = months_cover > OVERSTOCK_THRESHOLD_MONTHS AND net_position > 0
          excess_qty   = max(0, net_position - mean_monthly_demand × OVERSTOCK_THRESHOLD_MONTHS)

        Args:
            planning: Module 4 planning table with [part_no, net_position,
                      mean_monthly_demand, demand_class].

        Returns:
            DataFrame with columns:
                part_no      (str)   — SKU identifier
                excess_qty   (float) — units above the 12-month threshold
                months_cover (float) — months of stock coverage (capped at 9999.9)
                demand_class (str)   — ABC-XYZ-FSN combined class
            Only rows where is_overstock=True, sorted by months_cover descending.
        """
        df = planning[
            ["part_no", "net_position", "mean_monthly_demand", "demand_class"]
        ].copy()

        # Months of coverage — inf when demand=0 and stock>0, 0 when stock=0 and demand=0
        with np.errstate(divide="ignore", invalid="ignore"):
            df["months_cover"] = np.where(
                df["mean_monthly_demand"] > 0,
                df["net_position"].values / df["mean_monthly_demand"].values,
                np.where(df["net_position"].values > 0, np.inf, 0.0),
            )

        # Excess quantity above the threshold
        threshold_qty = df["mean_monthly_demand"] * self.OVERSTOCK_THRESHOLD_MONTHS
        df["excess_qty"] = (df["net_position"] - threshold_qty).clip(lower=0.0).round(4)

        # Overstock flag
        df["is_overstock"] = (
            (df["months_cover"] > self.OVERSTOCK_THRESHOLD_MONTHS)
            & (df["net_position"] > 0)
        )

        overstock = df[df["is_overstock"]].copy()
        # Cap display value for zero-demand dead-stock SKUs
        overstock["months_cover"] = overstock["months_cover"].clip(upper=9999.9).round(2)

        result = (
            overstock[["part_no", "excess_qty", "months_cover", "demand_class"]]
            .sort_values("months_cover", ascending=False)
            .reset_index(drop=True)
        )

        if not result.empty:
            logger.info(
                f"  Overstock risk: {len(result):,} SKUs flagged | "
                f"max months_cover = {result['months_cover'].max():.1f} | "
                f"total excess_qty = {result['excess_qty'].sum():,.0f}"
            )
        else:
            logger.info("  Overstock risk: 0 SKUs flagged")

        return result

    # ------------------------------------------------------------------
    # Summary KPIs
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        recommendations: pd.DataFrame,
        stockout_risk: pd.DataFrame,
        overstock_risk: pd.DataFrame,
        fill_rate: pd.DataFrame,
    ) -> dict[str, Any]:
        """Build scalar KPI dictionary for the dashboard header cards.

        Business meaning: the summary gives the inventory planner an at-a-glance
        health status of the entire spare-parts portfolio — how many SKUs need
        ordering, how urgent the situation is, and how well current stock can
        satisfy demand.

        Container utilisation is estimated from the total order quantity using
        the default AVG_PART_VOLUME_M3 (0.002 m3/unit) and CONTAINER_VOLUME_M3
        (28 m3).  This is an approximation; the authoritative figure is in the
        Module 5 constraint_summary.

        Args:
            recommendations: Purchase recommendations DataFrame.
            stockout_risk:   Stockout risk DataFrame.
            overstock_risk:  Overstock risk DataFrame.
            fill_rate:       Fill rate DataFrame.

        Returns:
            dict with keys:
                total_skus_to_order     (int)   — SKUs with order_qty > 0
                critical_count          (int)   — priority=1 SKUs
                high_count              (int)   — priority=2 SKUs
                stockout_risk_count     (int)   — SKUs with risk_pct > 30%
                overstock_count         (int)   — SKUs flagged as overstock
                weighted_fill_rate_pct  (float) — demand-weighted mean fill rate
                container_utilization_pct (float) — estimated container fill %
        """
        total_to_order = int(len(recommendations))  # already filtered to order_qty > 0
        critical_count = int((recommendations["priority"] == 1).sum()) if not recommendations.empty else 0
        high_count = int((recommendations["priority"] == 2).sum()) if not recommendations.empty else 0

        threshold_pct = self.STOCKOUT_RISK_THRESHOLD * 100.0
        stockout_count = (
            int((stockout_risk["risk_pct"] > threshold_pct).sum())
            if not stockout_risk.empty else 0
        )
        overstock_count = len(overstock_risk)

        # Demand-weighted fill rate (weight by monthly_demand so high-volume SKUs count more)
        if not fill_rate.empty:
            total_demand = float(fill_rate["monthly_demand"].sum())
            if total_demand > 0:
                weighted_fr = float(
                    (fill_rate["fill_rate_pct"] * fill_rate["monthly_demand"]).sum()
                    / total_demand
                )
            else:
                weighted_fr = float(fill_rate["fill_rate_pct"].mean())
        else:
            weighted_fr = 0.0

        # Container utilisation estimate
        if not recommendations.empty:
            total_units = float(recommendations["order_qty"].sum())
            vol_needed = total_units * self.AVG_PART_VOLUME_M3
            container_pct = min(
                (vol_needed / self.CONTAINER_VOLUME_M3) * 100.0, 9999.9
            )
        else:
            container_pct = 0.0

        summary: dict[str, Any] = {
            "total_skus_to_order": total_to_order,
            "critical_count": critical_count,
            "high_count": high_count,
            "stockout_risk_count": stockout_count,
            "overstock_count": overstock_count,
            "weighted_fill_rate_pct": round(weighted_fr, 2),
            "container_utilization_pct": round(container_pct, 2),
        }

        logger.info(
            f"  Summary | to_order={total_to_order:,} | critical={critical_count:,} | "
            f"high={high_count:,} | stockout_risk={stockout_count:,} | "
            f"overstock={overstock_count:,} | fill_rate={weighted_fr:.1f}% | "
            f"container={container_pct:.1f}%"
        )
        return summary
