"""Stage 13 — Next Shipment Report: purchase order recommendation for the next cycle.

Inputs:
  data/interim/inventory_policy.parquet   — per-SKU policy (Stage 12)

Outputs:
  data/outputs/stage13_shipment_report.xlsx — 7-sheet Excel PO recommendation

Report philosophy:
  The output is a decision-support document for the purchasing manager.  It
  answers three questions:
    1. What MUST we order this cycle?         → Immediate Orders sheet
    2. What SHOULD we order this cycle?       → Full Order List (soon + planned)
    3. What needs a human eye before ordering? → Sanity Review sheet

Recommended order quantity logic:
  recommended_qty = max(net_requirement, roq)
  Rationale: ordering less than net_requirement leaves us below ROL after the
  shipment arrives; ordering the full ROQ is the cost-optimised replenishment.
  Taking the max of both ensures we are above ROL AND do not fragment orders.

Projected coverage after order:
  projected_stock_on_hand  = stock_on_hand + recommended_qty
  projected_coverage_months = projected_stock / avg_monthly_demand
  This tells the buyer how long the ordered stock will last.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_POLICY_PARQUET = DATA_INTERIM / "inventory_policy.parquet"
_OUTPUT_XLSX    = DATA_OUTPUTS / "stage13_shipment_report.xlsx"

# Urgency sort order for the report
_URGENCY_ORDER = {"immediate": 0, "soon": 1, "planned": 2, "none": 3}
_ABC_ORDER     = {"A": 0, "B": 1, "C": 2}

# Columns surfaced in every order sheet
_ORDER_COLS = [
    "material_9", "description", "abc", "xyz", "abc_xyz_fsn", "policy_tier",
    "order_urgency", "stock_status",
    "stock_on_hand", "coverage_months",
    "rol", "roq", "net_requirement", "recommended_qty",
    "order_value_lkr",
    "forecast_m1", "forecast_m2", "forecast_m3", "forecast_lt",
    "safety_stock", "ss_method",
    "projected_stock", "projected_coverage_months",
    "avg_monthly_demand", "unit_value_lkr",
    "method", "active_months",
    "sanity_flag", "sanity_note",
]


# ---------------------------------------------------------------------------
# Order list construction
# ---------------------------------------------------------------------------

def build_order_list(policy_df: pd.DataFrame) -> pd.DataFrame:
    """Build the recommended purchase order from the inventory policy.

    Includes all SKUs with net_requirement > 0, sorted by urgency then ABC class.
    Computes recommended_qty and projected coverage after the order is received.

    Business meaning: recommended_qty = max(net_requirement, roq) ensures we
    both reach the ROL and take the cost-optimised order size in one shipment,
    minimising the number of purchase order lines raised over the year.

    Args:
        policy_df: inventory_policy.parquet from Stage 12.

    Returns:
        DataFrame of SKUs to order with quantities and projections.
    """
    order = policy_df[policy_df["net_requirement"] > 0.0].copy()

    # Recommended qty: max of minimum needed (net_req) and optimal batch (roq)
    order["recommended_qty"] = (
        order[["net_requirement", "roq"]].max(axis=1).round(0)
    )

    # Order value estimate
    order["order_value_lkr"] = (
        order["recommended_qty"] * order["unit_value_lkr"]
    ).round(2)

    # Projected stock after receiving this order
    order["projected_stock"] = (
        order["stock_on_hand"] + order["recommended_qty"]
    ).round(2)

    # Projected coverage after order
    demand_floor = order["avg_monthly_demand"].clip(lower=1e-9)
    order["projected_coverage_months"] = (
        order["projected_stock"] / demand_floor
    ).round(2)

    # Sort: urgency first, then ABC, then net_requirement desc
    order["_urgency_rank"] = order["order_urgency"].map(_URGENCY_ORDER).fillna(3)
    order["_abc_rank"]     = order["abc"].map(_ABC_ORDER).fillna(2)
    order = (
        order
        .sort_values(
            ["_urgency_rank", "_abc_rank", "net_requirement"],
            ascending=[True, True, False],
        )
        .drop(columns=["_urgency_rank", "_abc_rank"])
        .reset_index(drop=True)
    )

    logger.info(
        f"Order list: {len(order):,} SKUs | "
        f"Total recommended qty: {order['recommended_qty'].sum():,.0f} units | "
        f"Total order value: LKR {order['order_value_lkr'].sum():,.0f}"
    )
    return order


def build_excluded_list(policy_df: pd.DataFrame) -> pd.DataFrame:
    """SKUs not included in this cycle's order, with the reason.

    Helps the buyer confirm what is intentionally being deferred and why.

    Args:
        policy_df: inventory_policy.parquet from Stage 12.

    Returns:
        DataFrame of excluded SKUs with exclusion_reason column.
    """
    excl = policy_df[policy_df["net_requirement"] <= 0.0].copy()

    conditions = [
        excl["avg_monthly_demand"] == 0.0,
        excl["stock_status"] == "excess",
        excl["stock_status"] == "ok",
    ]
    reasons = [
        "Non-moving SKU (zero demand)",
        "Excess stock — coverage > 6 months",
        "Adequate stock — coverage ≥ lead time",
    ]
    excl["exclusion_reason"] = np.select(conditions, reasons, default="Stock above ROL")
    return excl[["material_9", "description", "abc", "policy_tier",
                 "stock_on_hand", "coverage_months", "stock_status",
                 "avg_monthly_demand", "exclusion_reason"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Summary analytics
# ---------------------------------------------------------------------------

def summary_kpis(order: pd.DataFrame, policy_df: pd.DataFrame) -> dict:
    """Headline KPIs for the shipment report cover page."""
    total_skus = len(policy_df)
    return {
        "Report Date":                         date.today().isoformat(),
        "Total SKUs in Scope":                 total_skus,
        "SKUs to Order (this cycle)":          len(order),
        "  — Immediate (stockout / critical)": int((order["order_urgency"] == "immediate").sum()),
        "  — Soon (low coverage)":             int((order["order_urgency"] == "soon").sum()),
        "  — Planned (below ROL)":             int((order["order_urgency"] == "planned").sum()),
        "Total Recommended Qty (units)":       float(order["recommended_qty"].sum()),
        "Total Order Value Est. (LKR)":        float(order["order_value_lkr"].sum()),
        "A-class SKUs to Order":               int((order["abc"] == "A").sum()),
        "A-class Order Value (LKR)":           float(order.loc[order["abc"] == "A", "order_value_lkr"].sum()),
        "Sanity-Flagged in Order":             int(order["sanity_flag"].sum()),
        "ML Safety Stock SKUs":                int((order["ss_method"] == "ML-Quantile").sum()),
        "Avg Projected Coverage (months)":     float(order["projected_coverage_months"].mean()),
        "SKUs with Excess Stock (excluded)":   int((policy_df["stock_status"] == "excess").sum()),
        "Non-Moving SKUs (excluded)":          int((policy_df["avg_monthly_demand"] == 0.0).sum()),
    }


def order_by_tier(order: pd.DataFrame) -> pd.DataFrame:
    """Per-tier aggregation of the recommended order."""
    tier_order = ["critical", "managed", "watch", "rationalise"]
    agg = (
        order.groupby("policy_tier", observed=True)
        .agg(
            sku_count=("material_9", "count"),
            immediate_skus=("order_urgency", lambda x: (x == "immediate").sum()),
            total_recommended_qty=("recommended_qty", "sum"),
            total_order_value_lkr=("order_value_lkr", "sum"),
            avg_projected_coverage=("projected_coverage_months", "mean"),
            sanity_flagged=("sanity_flag", "sum"),
        )
        .reset_index()
    )
    agg["policy_tier"] = pd.Categorical(agg["policy_tier"], categories=tier_order, ordered=True)
    return agg.sort_values("policy_tier").reset_index(drop=True)


def order_by_abc(order: pd.DataFrame) -> pd.DataFrame:
    """Per-ABC-class aggregation of the recommended order."""
    agg = (
        order.groupby("abc", observed=True)
        .agg(
            sku_count=("material_9", "count"),
            total_recommended_qty=("recommended_qty", "sum"),
            total_order_value_lkr=("order_value_lkr", "sum"),
            avg_projected_coverage=("projected_coverage_months", "mean"),
        )
        .reset_index()
    )
    agg["abc"] = pd.Categorical(agg["abc"], categories=["A", "B", "C"], ordered=True)
    return agg.sort_values("abc").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------

def _write_excel(
    order: pd.DataFrame,
    policy_df: pd.DataFrame,
    kpis: dict,
    tier_agg: pd.DataFrame,
    abc_agg: pd.DataFrame,
    excl: pd.DataFrame,
) -> None:
    """Write the 7-sheet Excel shipment report."""
    order_cols   = [c for c in _ORDER_COLS if c in order.columns]
    policy_cols  = [c for c in _ORDER_COLS if c in policy_df.columns]

    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book

        # Formats
        hdr_fmt   = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1})
        red_fmt   = wb.add_format({"bg_color": "#FFCCCC", "border": 1})
        amber_fmt = wb.add_format({"bg_color": "#FFF2CC", "border": 1})
        green_fmt = wb.add_format({"bg_color": "#E2EFDA", "border": 1})
        num_fmt   = wb.add_format({"num_format": "#,##0.0"})
        int_fmt   = wb.add_format({"num_format": "#,##0"})

        def _sheet(data: pd.DataFrame, name: str, col_width: int = 18) -> None:
            data.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.set_row(0, 18, hdr_fmt)
            ws.set_column(0, len(data.columns) - 1, col_width)

        # ── Sheet 1: Summary KPIs ───────────────────────────────────────────
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        kpi_df.to_excel(writer, sheet_name="Summary", index=False)
        ws = writer.sheets["Summary"]
        ws.set_row(0, 18, hdr_fmt)
        ws.set_column(0, 0, 46)
        ws.set_column(1, 1, 30)

        # ── Sheet 2: Full order list (all urgency levels) ───────────────────
        _sheet(order[order_cols], "Full Order List")

        # ── Sheet 3: Immediate orders only ─────────────────────────────────
        imm = order[order["order_urgency"] == "immediate"][order_cols].reset_index(drop=True)
        _sheet(imm, "Immediate Orders")

        # Add colour coding for sanity flags in Immediate Orders
        ws = writer.sheets["Immediate Orders"]
        flag_col = order_cols.index("sanity_flag") if "sanity_flag" in order_cols else None
        if flag_col is not None:
            for row_idx, flag in enumerate(imm["sanity_flag"], start=1):
                if flag:
                    ws.set_row(row_idx, None, amber_fmt)

        # ── Sheet 4: Sanity review (items needing manual check before order) ─
        flagged = order[order["sanity_flag"]][order_cols].reset_index(drop=True)
        _sheet(flagged if not flagged.empty else pd.DataFrame(columns=order_cols), "Sanity Review")

        # ── Sheet 5: Order by tier / ABC ────────────────────────────────────
        tier_agg.to_excel(writer, sheet_name="Order by Tier", index=False)
        abc_agg.to_excel(writer, sheet_name="Order by Tier", startrow=len(tier_agg) + 3, index=False)
        ws = writer.sheets["Order by Tier"]
        ws.set_row(0, 18, hdr_fmt)
        ws.set_row(len(tier_agg) + 3, 18, hdr_fmt)
        ws.set_column(0, max(len(tier_agg.columns), len(abc_agg.columns)) - 1, 22)

        # ── Sheet 6: Forecast detail for ordered SKUs ────────────────────────
        fc_cols = [
            "material_9", "description", "abc", "policy_tier",
            "avg_monthly_demand", "forecast_m1", "forecast_m2", "forecast_m3",
            "forecast_lt", "demand_std_monthly", "demand_std_lt",
            "safety_stock", "ss_method", "method",
            "recommended_qty", "order_value_lkr",
        ]
        fc_avail = [c for c in fc_cols if c in order.columns]
        _sheet(order[fc_avail], "Forecast Detail")

        # ── Sheet 7: Excluded SKUs ───────────────────────────────────────────
        _sheet(excl, "Excluded SKUs")

    logger.info(f"Shipment report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 13: generate next shipment (PO) recommendation report."""
    if not refresh and _OUTPUT_XLSX.exists():
        logger.info("stage13_shipment_report.xlsx exists and refresh=False — skipping Stage 13")
        return

    logger.info("Stage 13: Next Shipment Report")

    policy_df = pd.read_parquet(_POLICY_PARQUET)
    logger.info(f"Policy loaded: {len(policy_df):,} SKUs")

    order    = build_order_list(policy_df)
    excl     = build_excluded_list(policy_df)
    kpis     = summary_kpis(order, policy_df)
    tier_agg = order_by_tier(order)
    abc_agg  = order_by_abc(order)

    _write_excel(order, policy_df, kpis, tier_agg, abc_agg, excl)

    logger.info(
        f"Stage 13 complete | "
        f"Order SKUs: {len(order):,} | "
        f"Immediate: {(order['order_urgency']=='immediate').sum():,} | "
        f"Total qty: {order['recommended_qty'].sum():,.0f} units | "
        f"Total value: LKR {order['order_value_lkr'].sum():,.0f}"
    )
