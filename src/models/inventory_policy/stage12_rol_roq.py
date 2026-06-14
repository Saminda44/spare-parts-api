"""Stage 12 — Inventory Policy: ROL, ROQ, and Safety Stock per SKU.

Inputs:
  data/interim/stock_tracker.parquet    — current stock positions (Stage 11)
  data/interim/monthly_demand.parquet   — per-SKU monthly demand (Stage 7)
  data/interim/abc_xyz_fsn.parquet      — per-SKU classification (Stage 9)

Outputs:
  data/interim/inventory_policy.parquet     — per-SKU policy parameters (→ Stage 13)
  data/outputs/stage12_rol_roq.xlsx         — 7-sheet Excel policy report

Policy tiers and service levels:
  critical     99.0 %   z = 2.326   EOQ-based reorder quantity
  managed      97.5 %   z = 1.960   EOQ-based reorder quantity
  watch        95.0 %   z = 1.645   Cover-period ROQ (3 months)
  rationalise  90.0 %   z = 1.282   Cover-period ROQ (1 month)

Safety stock — tiered ML complexity:
  Tier 0 (non-movers):                SS = 0
  Tier 1 (sparse, < 6 active months): SS = classical z × σ_lt  (z-score × lead-time std)
  Tier 2+ (active, ≥ 6 months):       SS = LightGBM quantile regression
    LightGBM predicts the q-th quantile of 3-month forward demand from historical
    rolling windows.  The classical z × σ formula assumes Gaussian demand, which
    is systematically wrong for intermittent spare-parts demand.  The ML model
    learns the empirical tail directly from data and handles zero-inflation and
    heavy tails without distributional assumptions.

Formula summary:
  unit_value_lkr = total_issue_value_lkr / (avg_monthly_demand × active_months)
  safety_stock   = z × demand_std_lt          (sparse) or
                   max(0, q-quantile_pred − forecast_lt) (ML active)
  ROL            = forecast_lt + safety_stock
  EOQ            = √(2 × D_annual × ordering_cost / (unit_value × holding_rate))
  ROQ            = EOQ (critical/managed) | forecast_lt (watch) | avg_demand (rationalise)
  net_req        = max(0, ROL − stock_on_hand)

Sanity check (CLAUDE.md §15): flag any SKU where ROL or ROQ > 3× recent realized demand.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from loguru import logger

from src.config.constants import (
    DEFAULT_HOLDING_COST_RATE,
    DEFAULT_ORDERING_COST,
    LEAD_TIME_DAYS,
    ROL_ROQ_SANITY_MULTIPLIER,
)
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_TRACKER_PARQUET  = DATA_INTERIM / "stock_tracker.parquet"
_DEMAND_PARQUET   = DATA_INTERIM / "monthly_demand.parquet"
_ABC_XYZ_PARQUET  = DATA_INTERIM / "abc_xyz_fsn.parquet"
_POLICY_PARQUET   = DATA_INTERIM / "inventory_policy.parquet"
_OUTPUT_XLSX      = DATA_OUTPUTS / "stage12_rol_roq.xlsx"

_LEAD_TIME_MONTHS: int = LEAD_TIME_DAYS // 30   # 3

# Service level parameters per policy tier
# z-scores from scipy.stats.norm.ppf(service_level) — pre-computed for zero-dep
_TIER_PARAMS: dict[str, tuple[float, float]] = {
    "critical":    (0.990, 2.326),
    "managed":     (0.975, 1.960),
    "watch":       (0.950, 1.645),
    "rationalise": (0.900, 1.282),
}

_POLICY_OUTPUT_COLS: list[str] = [
    "material_9", "description", "abc", "xyz", "fsn", "abc_xyz_fsn",
    "policy_tier", "active_months", "avg_monthly_demand",
    "unit_value_lkr", "service_level", "z_score",
    "forecast_lt", "forecast_m1", "forecast_m2", "forecast_m3",
    "demand_std_monthly", "demand_std_lt",
    "safety_stock", "ss_method", "rol", "roq",
    "stock_on_hand", "coverage_months", "stock_status",
    "net_requirement", "order_urgency",
    "sanity_flag", "sanity_note",
    "method", "total_issue_value_lkr",
]


# ---------------------------------------------------------------------------
# Unit value estimation
# ---------------------------------------------------------------------------

def compute_unit_value(df: pd.DataFrame) -> pd.DataFrame:
    """Estimate per-unit LKR value from historical issue totals.

    Business meaning: unit value drives the holding cost term in EOQ. We derive
    it as total_issue_value_lkr ÷ (avg_monthly_demand × active_months), i.e. the
    average LKR value of one unit historically issued. SKUs with no issue history
    get a unit value of 0 and will receive a cover-period ROQ rather than EOQ.

    Args:
        df: stock tracker DataFrame.

    Returns:
        df with unit_value_lkr column added.
    """
    df = df.copy()
    total_issued_qty = (df["avg_monthly_demand"] * df["active_months"]).clip(lower=1.0)
    df["unit_value_lkr"] = (df["total_issue_value_lkr"] / total_issued_qty).clip(lower=0.0)
    return df


# ---------------------------------------------------------------------------
# Service level lookup
# ---------------------------------------------------------------------------

def _tier_service_level(policy_tier: str) -> tuple[float, float]:
    """Return (service_level, z_score) for a given policy tier."""
    return _TIER_PARAMS.get(policy_tier, _TIER_PARAMS["rationalise"])


# ---------------------------------------------------------------------------
# Safety stock
# ---------------------------------------------------------------------------

def compute_safety_stock(df: pd.DataFrame) -> pd.DataFrame:
    """Compute safety stock per SKU: SS = z × demand_std_lt.

    Business meaning: safety stock absorbs demand variability during the lead
    time so the target service level is met. Higher-tier SKUs carry more safety
    stock (larger z) because a stockout is more costly for critical spare parts.

    Args:
        df: DataFrame with policy_tier and demand_std_lt columns.

    Returns:
        df with service_level, z_score, safety_stock columns added.
    """
    df = df.copy()
    tiers = df["policy_tier"].fillna("rationalise")
    sl   = tiers.map(lambda t: _tier_service_level(t)[0])
    z    = tiers.map(lambda t: _tier_service_level(t)[1])
    df["service_level"] = sl
    df["z_score"]       = z
    df["safety_stock"]  = (z * df["demand_std_lt"].fillna(0.0)).clip(lower=0.0)
    return df


# ---------------------------------------------------------------------------
# ROL
# ---------------------------------------------------------------------------

def compute_rol(df: pd.DataFrame) -> pd.DataFrame:
    """Compute Reorder Level: ROL = forecast_lt + safety_stock.

    Business meaning: ROL is the stock level at which a replenishment order
    must be placed so that, assuming demand proceeds at the forecast rate and
    the lead time is exactly 3 months, stock does not fall below safety stock
    before the order arrives.

    Args:
        df: DataFrame with forecast_lt and safety_stock columns.

    Returns:
        df with rol column added (≥ 0, rounded to nearest unit).
    """
    df = df.copy()
    df["rol"] = (df["forecast_lt"].fillna(0.0) + df["safety_stock"]).clip(lower=0.0)
    df["rol"] = df["rol"].round(2)
    return df


# ---------------------------------------------------------------------------
# ROQ
# ---------------------------------------------------------------------------

def _eoq_scalar(
    d_annual: float,
    unit_value: float,
    ordering_cost: float = DEFAULT_ORDERING_COST,
    holding_rate: float = DEFAULT_HOLDING_COST_RATE,
) -> float:
    """Economic Order Quantity for a single SKU.

    EOQ = √(2 × D × S / H)
    where:
      D = annual demand units
      S = ordering cost (LKR per order)
      H = holding cost per unit per year = unit_value × holding_rate

    Returns 0.0 if demand or unit_value is effectively zero.
    """
    h = unit_value * holding_rate
    if h < 1.0 or d_annual < 0.01:
        return 0.0
    return math.sqrt(2.0 * d_annual * ordering_cost / h)


def compute_roq(df: pd.DataFrame) -> pd.DataFrame:
    """Compute Reorder Quantity per SKU based on policy tier.

    Tier logic:
      critical / managed  → EOQ (minimises total ordering + holding cost)
      watch               → cover-period: forecast_lt (one full lead time of demand)
      rationalise         → cover-period: avg_monthly_demand (one month's demand)
      non-movers          → 0 (no order required)

    EOQ is floored at 1 unit for any active SKU to ensure a meaningful order.
    All ROQ values are rounded to the nearest whole unit.

    Args:
        df: DataFrame with policy_tier, avg_monthly_demand, unit_value_lkr,
            forecast_lt columns.

    Returns:
        df with roq column added.
    """
    df = df.copy()
    roq = np.zeros(len(df), dtype=float)

    for i, row in enumerate(df.itertuples(index=False)):
        tier   = getattr(row, "policy_tier", "rationalise") or "rationalise"
        demand = float(getattr(row, "avg_monthly_demand", 0.0))
        fcast  = float(getattr(row, "forecast_lt", 0.0))
        uv     = float(getattr(row, "unit_value_lkr", 0.0))

        if demand <= 0.0 and fcast <= 0.0:
            roq[i] = 0.0
            continue

        if tier in ("critical", "managed"):
            d_annual   = demand * 12.0
            eoq_val    = _eoq_scalar(d_annual, uv)
            roq[i] = max(1.0, eoq_val)

        elif tier == "watch":
            roq[i] = max(1.0, fcast)

        else:  # rationalise
            roq[i] = max(1.0, demand)

    df["roq"] = np.round(roq, 0)
    return df


# ---------------------------------------------------------------------------
# Net requirement & urgency
# ---------------------------------------------------------------------------

def compute_net_requirement(df: pd.DataFrame) -> pd.DataFrame:
    """Compute net units to order: net_requirement = max(0, ROL − stock_on_hand).

    Business meaning: if current stock already exceeds ROL the net requirement
    is zero (no order needed this cycle). A positive value means the warehouse
    is below its reorder trigger and must place an order.

    Also assigns order_urgency:
      immediate — stock below safety stock or at stockout/critical status
      soon      — stock at low status (below lead-time coverage)
      planned   — stock at ok status but ROL exceeded
      none      — no order needed this cycle

    Args:
        df: DataFrame with rol, stock_on_hand, safety_stock, stock_status.

    Returns:
        df with net_requirement and order_urgency columns.
    """
    df = df.copy()
    df["net_requirement"] = (df["rol"] - df["stock_on_hand"]).clip(lower=0.0).round(2)

    conditions = [
        df["stock_status"].isin({"stockout", "critical"}) & (df["net_requirement"] > 0.0),
        (df["stock_status"] == "low") & (df["net_requirement"] > 0.0),
        df["net_requirement"] > 0.0,
    ]
    df["order_urgency"] = np.select(conditions, ["immediate", "soon", "planned"], default="none")
    return df


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def apply_sanity_checks(df: pd.DataFrame) -> pd.DataFrame:
    """Flag SKUs where ROL or ROQ exceeds 3× recent realized demand.

    Business meaning (CLAUDE.md §15): a ROL or ROQ more than 3× the lead-time
    demand is likely a data quality issue — either the demand history is too
    short to give a reliable std estimate, or the unit_value is missing and
    EOQ is unreliable. Flag these for manual review before the order is placed.

    Args:
        df: DataFrame with rol, roq, forecast_lt, avg_monthly_demand.

    Returns:
        df with sanity_flag (bool) and sanity_note (str) columns.
    """
    df = df.copy()
    threshold = df["forecast_lt"].clip(lower=df["avg_monthly_demand"] * _LEAD_TIME_MONTHS)
    benchmark = threshold * ROL_ROQ_SANITY_MULTIPLIER

    rol_flag = (df["rol"]  > benchmark) & (benchmark > 0.0)
    roq_flag = (df["roq"]  > benchmark) & (benchmark > 0.0)

    notes: list[str] = []
    for r, q in zip(rol_flag, roq_flag):
        parts = []
        if r:
            parts.append("ROL > 3× lead-time demand")
        if q:
            parts.append("ROQ > 3× lead-time demand")
        notes.append("; ".join(parts))

    df["sanity_flag"] = rol_flag | roq_flag
    df["sanity_note"] = notes

    n_flagged = int(df["sanity_flag"].sum())
    if n_flagged > 0:
        logger.warning(f"Sanity check: {n_flagged:,} SKUs flagged (ROL/ROQ > 3× lead-time demand)")
    return df


# ---------------------------------------------------------------------------
# KPI and summary tables
# ---------------------------------------------------------------------------

def summary_kpis(df: pd.DataFrame) -> dict:
    """Headline KPIs for the policy report."""
    ordering = df[df["net_requirement"] > 0.0]
    return {
        "Total SKUs":                      len(df),
        "SKUs Requiring Order (this cycle)": int((df["net_requirement"] > 0.0).sum()),
        "Immediate Orders":                int((df["order_urgency"] == "immediate").sum()),
        "Soon Orders":                     int((df["order_urgency"] == "soon").sum()),
        "Planned Orders":                  int((df["order_urgency"] == "planned").sum()),
        "Total Net Requirement (units)":   float(df["net_requirement"].sum()),
        "Total ROQ Value Est. (LKR)":      float((ordering["roq"] * ordering["unit_value_lkr"]).sum()),
        "Sanity-Flagged SKUs":             int(df["sanity_flag"].sum()),
        "Avg Safety Stock (active)":       float(
            df.loc[df["avg_monthly_demand"] > 0.0, "safety_stock"].mean()
        ) if (df["avg_monthly_demand"] > 0.0).any() else 0.0,
        "Total Safety Stock (units)":      float(df["safety_stock"].sum()),
    }


def policy_by_tier(df: pd.DataFrame) -> pd.DataFrame:
    """Per-tier summary: SKU counts, total ROQ, total net requirement."""
    tier_order = ["critical", "managed", "watch", "rationalise"]
    agg = (
        df.groupby("policy_tier", observed=True)
        .agg(
            sku_count=("material_9", "count"),
            active_skus=("avg_monthly_demand", lambda x: (x > 0).sum()),
            avg_rol=("rol", "mean"),
            avg_roq=("roq", "mean"),
            total_net_requirement=("net_requirement", "sum"),
            skus_needing_order=("net_requirement", lambda x: (x > 0).sum()),
            total_roq_value_lkr=("unit_value_lkr", lambda x: (x * df.loc[x.index, "roq"]).sum()),
        )
        .reset_index()
    )
    agg["policy_tier"] = pd.Categorical(agg["policy_tier"], categories=tier_order, ordered=True)
    return agg.sort_values("policy_tier").reset_index(drop=True)


def top_net_requirements(df: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    """Top N SKUs by net_requirement × unit_value_lkr (highest LKR exposure)."""
    ordering = df[df["net_requirement"] > 0.0].copy()
    ordering["net_req_value_lkr"] = ordering["net_requirement"] * ordering["unit_value_lkr"]
    return (
        ordering
        .sort_values(["order_urgency", "net_req_value_lkr"], ascending=[True, False])
        .head(top_n)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------

def _write_excel(
    df: pd.DataFrame,
    kpis: dict,
    tier_summary: pd.DataFrame,
    top_orders: pd.DataFrame,
    flagged: pd.DataFrame,
) -> None:
    report_cols = [c for c in _POLICY_OUTPUT_COLS if c in df.columns]

    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb  = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1})
        red = wb.add_format({"bg_color": "#FFCCCC"})

        def _sheet(data: pd.DataFrame, name: str) -> None:
            data.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.set_row(0, 18, hdr)
            ws.set_column(0, len(data.columns) - 1, 18)

        # Sheet 1: KPI summary
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        kpi_df.to_excel(writer, sheet_name="Summary KPIs", index=False)
        ws = writer.sheets["Summary KPIs"]
        ws.set_row(0, 18, hdr)
        ws.set_column(0, 0, 44)
        ws.set_column(1, 1, 25)

        # Sheet 2: Policy by tier
        _sheet(tier_summary, "Policy by Tier")

        # Sheet 3: All SKUs policy
        _sheet(df[report_cols], "All SKUs Policy")

        # Sheet 4: Top net requirements (highest urgency orders)
        top_cols = [c for c in report_cols if c in top_orders.columns]
        if "net_req_value_lkr" in top_orders.columns:
            top_cols = top_cols + ["net_req_value_lkr"]
        _sheet(top_orders[top_cols] if not top_orders.empty else pd.DataFrame(columns=top_cols), "Top Orders")

        # Sheet 5: Sanity-flagged SKUs
        flag_cols = [c for c in report_cols if c in flagged.columns] + ["sanity_note"]
        _sheet(flagged[list(dict.fromkeys(flag_cols))] if not flagged.empty else pd.DataFrame(columns=flag_cols), "Sanity Flags")

        # Sheet 6: Immediate-order SKUs
        immediate = df[df["order_urgency"] == "immediate"].copy()
        imm_cols  = [c for c in report_cols if c in immediate.columns]
        _sheet(immediate[imm_cols] if not immediate.empty else pd.DataFrame(columns=imm_cols), "Immediate Orders")

        # Sheet 7: ABC × urgency pivot
        pivot = (
            df.groupby(["abc", "order_urgency"], observed=True)
            .agg(sku_count=("material_9", "count"), total_net_req=("net_requirement", "sum"))
            .reset_index()
        )
        _sheet(pivot, "ABC x Urgency")

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 12: compute ROL, ROQ, safety stock, and net requirements per SKU."""
    if not refresh and _POLICY_PARQUET.exists():
        logger.info("inventory_policy.parquet exists and refresh=False — skipping Stage 12")
        return

    logger.info("Stage 12: ROL / ROQ / Buffer Policy")

    df         = pd.read_parquet(_TRACKER_PARQUET)
    monthly_demand = pd.read_parquet(_DEMAND_PARQUET)
    classified = pd.read_parquet(_ABC_XYZ_PARQUET)
    logger.info(f"Stock tracker loaded: {len(df):,} SKUs")

    # Classical safety stock computations
    df = compute_unit_value(df)
    df = compute_safety_stock(df)   # sets classical z × σ_lt as baseline

    # ML safety stock upgrade for active SKUs (≥ 6 months history)
    from src.models.inventory_policy._ss_model import compute_ml_safety_stock
    logger.info("Upgrading safety stock with ML quantile regression for active SKUs ...")
    df = compute_ml_safety_stock(df, monthly_demand, classified)

    # Downstream policy computations (use updated safety_stock from ML or classical)
    df = compute_rol(df)
    df = compute_roq(df)
    df = compute_net_requirement(df)
    df = apply_sanity_checks(df)

    # Save
    df.to_parquet(_POLICY_PARQUET, index=False)
    logger.info(f"Policy saved → {_POLICY_PARQUET} ({len(df):,} rows)")

    # Reports
    kpis         = summary_kpis(df)
    tier_summary = policy_by_tier(df)
    top_orders   = top_net_requirements(df, top_n=30)
    flagged      = df[df["sanity_flag"]].copy()

    _write_excel(df, kpis, tier_summary, top_orders, flagged)

    ordering_skus = int((df["net_requirement"] > 0.0).sum())
    immediate     = int((df["order_urgency"] == "immediate").sum())
    ml_count      = int((df.get("ss_method", pd.Series(dtype=str)) == "ML-Quantile").sum())
    logger.info(
        f"Stage 12 complete | "
        f"SKUs needing order: {ordering_skus:,} | "
        f"Immediate: {immediate:,} | "
        f"ML safety stock: {ml_count:,} SKUs | "
        f"Sanity flags: {int(df['sanity_flag'].sum()):,} | "
        f"Total net req: {df['net_requirement'].sum():,.0f} units"
    )
