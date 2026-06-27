"""Stage 4: Orders EDA — Yamaha spare-parts dealer orders analysis.

Business context:
  Scope: DOMESTIC orders from Yamaha distributor warehouse to spare-parts dealers.
  Dealer match: inner join on Sold-to Party = Dealer Code (Active dealers only).
  Non-dealer transactions are excluded entirely.

  Two dealer types (from dealers.xlsx Type column):
    MC  — Motorcycle spare-parts dealers  → 4 sub-categories
    OBM — Outboard Motor spare-parts dealers → single block

  MC sub-categories (by Material Description, evaluated in priority order):
    Tyre        : contains "KATANA TYRE"   (KATANA-branded tyres)
    Battery     : contains "KARATE BATTERY" (KARATE-branded batteries)
    Lubricant   : contains "YAMALUBE"       (engine/gear oils)
    Spare Parts : everything else in MC

  Document scope (SD Document Category):
    C → Orders  (doc_type = "PO")     — demand analysis
    H → Returns (doc_type = "Return") — return rate analysis

Inputs:
  data/raw/orders.xlsx
  data/raw/dealers.xlsx

Outputs:
  data/interim/orders_clean.parquet        (C + H, confirmed > 0)
  data/interim/orders_rejection_log.parquet (PO conf=0 + pure Cancelled Orders conf=0)
  data/outputs/stage04_orders_eda.xlsx
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
import xlsxwriter
from loguru import logger

from src.config.constants import CURRENCY
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────────
_ORDERS_RAW = DATA_RAW / "orders.xlsx"
_DEALERS_RAW = DATA_RAW / "dealers.xlsx"
_CLEAN_PARQUET = DATA_INTERIM / "orders_clean.parquet"
_REJECT_PARQUET = DATA_INTERIM / "orders_rejection_log.parquet"
_OUTPUT_EXCEL = DATA_OUTPUTS / "stage04_orders_eda.xlsx"

# ── MC sub-category keywords (case-insensitive, evaluated in priority order) ──
_KW_TYRE = "KATANA TYRE"
_KW_BATTERY = "KARATE BATTERY"
_KW_LUBE = "YAMALUBE"

# ── Excel theme ────────────────────────────────────────────────────────────────
_BLUE = "#003087"  # Yamaha blue
_RED = "#B71C1C"
_GREEN = "#1B5E20"
_AMBER = "#E65100"
_PURPLE = "#4A148C"
_TEAL = "#006064"
_GREY = "#455A64"

_CAT_COLORS = {
    "Lubricant": "#FF6F00",
    "Battery": "#1565C0",
    "Tyre": "#2E7D32",
    "Spare Parts": "#4A148C",
    "OBM": "#006064",
}


# ══════════════════════════════════════════════════════════════════════════════
# 1. Load & clean
# ══════════════════════════════════════════════════════════════════════════════


def load_dealers() -> pd.DataFrame:
    """Load dealers.xlsx — Active dealers only.

    Returns columns: Dealer Code, Dealer Name, Province, District, ASE, RM, Type.
    Business meaning: authoritative dealer master; join key is Dealer Code = Sold-to Party.
    """
    df = pd.read_excel(_DEALERS_RAW, dtype=str)
    df.columns = df.columns.str.strip()
    for col in df.select_dtypes("object").columns:
        df[col] = df[col].fillna("").str.strip()

    before = len(df)
    df = df[df["Status (Active/ Inactive)"].str.upper() == "ACTIVE"].copy()
    logger.info(
        f"Dealer master: {before} total → {len(df)} Active  "
        f"(MC={( df['Type']=='MC').sum()}, OBM={(df['Type']=='OBM').sum()})"
    )
    keep = ["Dealer Code", "Dealer Name", "Province", "District", "ASE", "RM", "Type"]
    return df[[c for c in keep if c in df.columns]].copy()


def _assign_mc_category(desc: pd.Series) -> pd.Series:
    """Classify MC Material Description into Tyre / Battery / Lubricant / Spare Parts.

    Priority order: Tyre → Battery → Lubricant → Spare Parts.
    Case-insensitive keyword match on Material Description.
    """
    upper = desc.str.upper().fillna("")
    cat = pd.Series("Spare Parts", index=desc.index)
    cat = cat.where(~upper.str.contains(_KW_LUBE, na=False), "Lubricant")
    cat = cat.where(~upper.str.contains(_KW_BATTERY, na=False), "Battery")
    cat = cat.where(~upper.str.contains(_KW_TYRE, na=False), "Tyre")
    return cat


def load_orders(dealers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load orders.xlsx, scope to Yamaha dealers, classify documents and MC categories.

    Steps:
      1. Parse date columns.
      2. Filter to SD Document Category C (Orders) and H (Returns).
      3. Inner-join on Sold-to Party = Dealer Code (Active only).
         Rows not matched are non-Yamaha customers — excluded entirely.
      4. Attach Province / District / ASE / RM from dealer master.
      5. Classify dealer_type (MC / OBM) and mc_category (MC sub-category).
      6. Compute fill_rate, lost_qty, lead_time_days, Year_Month_str.
      7. Split fully-rejected lines (Confirmed = 0) into rejection_log.

    Returns:
        clean_df      : Lines with Confirmed Quantity > 0 (doc_type PO or Return).
        rejection_log : Lines with Confirmed Quantity = 0 from Orders (C) only.
    """
    logger.info("Loading orders.xlsx …")
    raw = pd.read_excel(
        _ORDERS_RAW,
        dtype={"Sales Document": str, "Sold-to Party": str, "Material": str},
        parse_dates=["Created On", "Goods Issue Date", "Document Date"],
    )
    logger.info(f"  Raw rows: {len(raw):,}  columns: {raw.shape[1]}")

    # ── Document scope: C = Order, H = Return ─────────────────────────────────
    raw["doc_type"] = raw["SD Document Category"].map({"C": "PO", "H": "Return"})
    raw = raw[raw["doc_type"].notna()].copy()
    logger.info(
        f"  After SD Category filter (C/H only): {len(raw):,}  "
        f"(Orders={( raw['doc_type']=='PO').sum():,}, "
        f"Returns={(raw['doc_type']=='Return').sum():,})"
    )

    # ── Reclassify cancelled C-orders: SD Cat C + non-blank Rejection Reason → Return ──
    # Business rule: C-order with a filled Rejection Reason Description was cancelled before
    # fulfilment — treat as Return Order; excluded from order-value and fill-rate calculations.
    rr_col = "Rejection Reason Description"
    raw["return_type"] = ""
    if rr_col in raw.columns:
        h_mask = raw["doc_type"] == "Return"
        rr_filled = raw[rr_col].fillna("").str.strip()
        cancelled_mask = (raw["doc_type"] == "PO") & (rr_filled != "")
        raw.loc[h_mask, "return_type"] = "Return Order"
        raw.loc[cancelled_mask, "return_type"] = "Cancelled Order"
        if cancelled_mask.any():
            raw.loc[cancelled_mask, "doc_type"] = "Return"
            logger.info(
                f"  Reclassified {int(cancelled_mask.sum()):,} C-orders "
                f"(non-blank Rejection Reason Description) → Return (Cancelled Order)"
            )
        logger.info(
            f"  After cancellation reclassification: "
            f"PO={( raw['doc_type']=='PO').sum():,}  "
            f"Return={( raw['doc_type']=='Return').sum():,}"
        )
    else:
        raw.loc[raw["doc_type"] == "Return", "return_type"] = "Return Order"
        logger.warning(
            "  'Rejection Reason Description' column not found — "
            "cancelled C-order reclassification skipped"
        )

    # ── Unified return_reason column (H orders + cancelled C) ────────────────
    # H Return Orders: actual return reason lives in Order Reason Description.
    # Cancelled C-orders: actual cancellation reason lives in Rejection Reason Description.
    # Do NOT use Order Reason Description for cancelled C-orders — it records why
    # the original order was placed ("Parts Dealer Sales"), not why it was cancelled.
    raw["return_reason"] = ""
    h_mask_rr = raw["return_type"] == "Return Order"
    c_mask_rr = raw["return_type"] == "Cancelled Order"
    if "Order Reason Description" in raw.columns:
        raw.loc[h_mask_rr, "return_reason"] = (
            raw.loc[h_mask_rr, "Order Reason Description"].fillna("").str.strip()
        )
    if "Rejection Reason Description" in raw.columns:
        raw.loc[c_mask_rr, "return_reason"] = (
            raw.loc[c_mask_rr, "Rejection Reason Description"].fillna("").str.strip()
        )

    # ── Dealer scoping: inner join on Sold-to Party = Dealer Code ─────────────
    raw["Sold-to Party"] = raw["Sold-to Party"].astype(str).str.strip().str.lstrip("0")
    dealers_map = dealers.copy()
    dealers_map["Dealer Code"] = dealers_map["Dealer Code"].astype(str).str.strip().str.lstrip("0")

    before = len(raw)
    raw = raw.merge(
        dealers_map.rename(columns={"Type": "dealer_type"}),
        left_on="Sold-to Party",
        right_on="Dealer Code",
        how="inner",
        suffixes=("", "_dealer"),
    )
    logger.info(
        f"  After dealer-code scoping: {len(raw):,} "
        f"(excluded {before - len(raw):,} non-Yamaha lines)"
    )
    if len(raw) == 0:
        raise ValueError(
            "No orders matched any dealer after joining on Sold-to Party = Dealer Code. "
            "Check that the Dealer Code column in dealers.xlsx matches the Sold-to Party "
            "format in orders.xlsx (both stripped of leading zeros)."
        )
    mc_lines = (raw["dealer_type"] == "MC").sum()
    obm_lines = (raw["dealer_type"] == "OBM").sum()
    logger.info(f"    MC lines: {mc_lines:,}  OBM lines: {obm_lines:,}")

    # ── MC sub-category ────────────────────────────────────────────────────────
    mc_mask = raw["dealer_type"] == "MC"
    raw["mc_category"] = "N/A"
    raw.loc[mc_mask, "mc_category"] = _assign_mc_category(raw.loc[mc_mask, "Material Description"])

    # ── Derived columns ────────────────────────────────────────────────────────
    raw["lead_time_days"] = (raw["Goods Issue Date"] - raw["Created On"]).dt.days
    raw["lost_qty"] = raw["Confirmed Quantity (Item)"] - raw["Order Quantity (Item)"]
    raw["fill_rate"] = (
        raw["Confirmed Quantity (Item)"] / raw["Order Quantity (Item)"].replace(0, np.nan)
    ).clip(0, 1)
    # confirmed_value = what was actually confirmed/delivered, used for all value metrics.
    # Net Value (Item) = Order Qty × Net Price (ordered value, only kept for fill-rate denominator).
    raw["confirmed_value"] = raw["Net Price"] * raw["Confirmed Quantity (Item)"]
    raw["Year_Month"] = raw["Created On"].dt.to_period("M")
    raw["Year_Month_str"] = raw["Created On"].dt.strftime("%Y-%m")

    # ── Split fully rejected lines out of clean_df ────────────────────────────
    # PO with conf=0: fully undelivered order lines.
    # Cancelled Order with conf=0: order was cancelled before any qty was confirmed
    # (pure cancellation — no goods to return). Both go to the rejection log.
    rejected_mask = (raw["Confirmed Quantity (Item)"] == 0) & (
        (raw["doc_type"] == "PO") | (raw["return_type"] == "Cancelled Order")
    )
    rejection_log = raw[rejected_mask].copy()
    clean_df = raw[~rejected_mask].copy()

    logger.info(f"  Clean lines (conf > 0 or Returns): {len(clean_df):,}")
    logger.info(
        f"    MC={( clean_df['dealer_type']=='MC').sum():,}  "
        f"OBM={(clean_df['dealer_type']=='OBM').sum():,}"
    )
    logger.info(f"  Fully rejected PO lines: {len(rejection_log):,}")

    # MC category breakdown
    mc_clean = clean_df[clean_df["dealer_type"] == "MC"]
    for cat, cnt in mc_clean["mc_category"].value_counts().items():
        logger.info(f"    MC/{cat}: {cnt:,} lines")

    return clean_df, rejection_log


# ══════════════════════════════════════════════════════════════════════════════
# 2. Analysis functions — all work on any subset of clean_df
# ══════════════════════════════════════════════════════════════════════════════


def summary_kpis(clean: pd.DataFrame, rejected: pd.DataFrame) -> dict:
    """Top-level KPIs for the Summary sheet."""
    po = clean[clean["doc_type"] == "PO"]
    ret = clean[clean["doc_type"] == "Return"]
    total_ordered = po["Order Quantity (Item)"].sum()
    total_confirmed = po["Confirmed Quantity (Item)"].sum()
    period = (
        f"{clean['Created On'].min().date()} → {clean['Created On'].max().date()}"
        if len(clean) > 0
        else "N/A"
    )
    total_po_value = po["confirmed_value"].sum()
    return_value = float((ret["Confirmed Quantity (Item)"] * ret["Net Price"]).sum())
    gross_value = total_po_value + return_value
    return_rate_pct = return_value / max(gross_value, 1) * 100

    # Category mix (MC)
    mc = po[po["dealer_type"] == "MC"]
    mc_total_val = mc["confirmed_value"].sum() or 1
    cat_mix = {
        cat: round(grp["confirmed_value"].sum() / mc_total_val * 100, 1)
        for cat, grp in mc.groupby("mc_category")
    }

    return {
        "Period": period,
        "Total Order Lines (PO)": int(len(po)),
        "Total Return Lines": int(len(ret)),
        "Fully Rejected PO Lines": int(len(rejected)),
        "Unique Active Dealers": int(clean["Dealer Code"].nunique()),
        "Unique Materials": int(clean["Material"].nunique()),
        f"Total PO Value ({CURRENCY})": round(total_po_value),
        f"Total Return Value ({CURRENCY})": round(return_value),
        "Return Rate % (value)": round(return_rate_pct, 2),
        "Overall Fill Rate %": round(total_confirmed / max(total_ordered, 1) * 100, 2),
        "Avg Lead Time (days)": round(float(po["lead_time_days"].mean()), 1),
        "MC Lubricant Share %": cat_mix.get("Lubricant", 0),
        "MC Battery Share %": cat_mix.get("Battery", 0),
        "MC Tyre Share %": cat_mix.get("Tyre", 0),
        "MC Spare Parts Share %": cat_mix.get("Spare Parts", 0),
    }


def category_mix(po: pd.DataFrame) -> pd.DataFrame:
    """Revenue and volume share of each MC sub-category + OBM."""

    def _segment(df: pd.DataFrame, label: str) -> dict:
        return {
            "Category": label,
            "Order Lines": len(df),
            "Order Qty": df["Order Quantity (Item)"].sum(),
            "Confirmed Qty": df["Confirmed Quantity (Item)"].sum(),
            f"Value ({CURRENCY})": round(df["confirmed_value"].sum()),
            "Fill Rate %": round(
                df["Confirmed Quantity (Item)"].sum()
                / max(df["Order Quantity (Item)"].sum(), 1)
                * 100,
                2,
            ),
        }

    rows = []
    mc = po[po["dealer_type"] == "MC"]
    for cat in ["Lubricant", "Battery", "Tyre", "Spare Parts"]:
        rows.append(_segment(mc[mc["mc_category"] == cat], f"MC – {cat}"))
    rows.append(_segment(po[po["dealer_type"] == "OBM"], "OBM"))
    df = pd.DataFrame(rows)
    total_val = df[f"Value ({CURRENCY})"].sum() or 1
    df["Value Share %"] = (df[f"Value ({CURRENCY})"] / total_val * 100).round(2)
    return df


def part_analysis(po: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """Top parts by confirmed value — Material + Description together.

    Business meaning: identifies fast-moving parts for buffer stock decisions.
    """
    agg = (
        po.groupby(["Material", "Material Description"])
        .agg(
            order_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            total_value_lkr=("confirmed_value", "sum"),
            short_qty=("lost_qty", lambda x: abs(x[x < 0].sum())),
        )
        .reset_index()
    )
    agg["fill_rate_%"] = (agg["confirmed_qty"] / agg["order_qty"].replace(0, np.nan) * 100).round(2)
    total = agg["total_value_lkr"].sum() or 1
    agg["value_share_%"] = (agg["total_value_lkr"] / total * 100).round(2)
    agg = agg.sort_values("total_value_lkr", ascending=False).head(top_n)
    agg["cumul_share_%"] = agg["value_share_%"].cumsum().round(2)
    return agg.reset_index(drop=True)


def dealer_performance(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Dealer-level performance: value, fill rate, return rate, order frequency.

    Business meaning: identifies top dealers and those with service-level issues.
    """
    order_agg = (
        po.groupby(
            ["Dealer Code", "Dealer Name", "Province", "District", "RM", "ASE", "dealer_type"]
        )
        .agg(
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value_lkr=("confirmed_value", "sum"),
            active_months=("Year_Month_str", "nunique"),
        )
        .reset_index()
    )
    order_agg["fill_rate_%"] = (
        order_agg["confirmed_qty"] / order_agg["order_qty"].replace(0, np.nan) * 100
    ).round(2)
    order_agg["avg_monthly_value_lkr"] = (
        order_agg["order_value_lkr"] / order_agg["active_months"].replace(0, np.nan)
    ).round(0)

    # Return rate
    ret_agg = (
        ret.groupby("Dealer Code")
        .agg(
            return_lines=("Sales Document", "count"),
            return_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    merged = order_agg.merge(ret_agg, on="Dealer Code", how="left").fillna(0)
    merged["return_rate_%"] = (
        merged["return_value_lkr"] / merged["order_value_lkr"].replace(0, np.nan) * 100
    ).round(2)

    # Dealer tier: A = top 80% cumulative value, B = next 15%, C = bottom 5%
    merged = merged.sort_values("order_value_lkr", ascending=False).reset_index(drop=True)
    total = merged["order_value_lkr"].sum() or 1
    merged["value_share_%"] = (merged["order_value_lkr"] / total * 100).round(2)
    merged["cumul_%"] = merged["value_share_%"].cumsum().round(2)
    merged["dealer_tier"] = merged["cumul_%"].apply(
        lambda x: "A" if x <= 80 else ("B" if x <= 95 else "C")
    )
    return merged.drop(columns=["cumul_%"])


def rm_performance(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """RM-level aggregation: dealers managed, total value, fill rate, return rate."""
    o = (
        po.groupby("RM")
        .agg(
            unique_dealers=("Dealer Code", "nunique"),
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    o["fill_rate_%"] = (o["confirmed_qty"] / o["order_qty"].replace(0, np.nan) * 100).round(2)
    r = (
        ret.groupby("RM")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
    )
    merged = o.merge(r, on="RM", how="left").fillna(0)
    merged["return_rate_%"] = (
        merged["return_value_lkr"] / merged["order_value_lkr"].replace(0, np.nan) * 100
    ).round(2)
    total = merged["order_value_lkr"].sum() or 1
    merged["value_share_%"] = (merged["order_value_lkr"] / total * 100).round(2)
    return merged.sort_values("order_value_lkr", ascending=False).reset_index(drop=True)


def ase_performance(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """ASE-level aggregation: similar to RM but at ASE granularity."""
    o = (
        po.groupby(["ASE", "RM", "Province"])
        .agg(
            unique_dealers=("Dealer Code", "nunique"),
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    o["fill_rate_%"] = (o["confirmed_qty"] / o["order_qty"].replace(0, np.nan) * 100).round(2)
    r = (
        ret.groupby("ASE")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
    )
    merged = o.merge(r, on="ASE", how="left").fillna(0)
    merged["return_rate_%"] = (
        merged["return_value_lkr"] / merged["order_value_lkr"].replace(0, np.nan) * 100
    ).round(2)
    return merged.sort_values("order_value_lkr", ascending=False).reset_index(drop=True)


def district_performance(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """District-level order value, fill rate, and return rate."""
    o = (
        po.groupby(["Province", "District"])
        .agg(
            unique_dealers=("Dealer Code", "nunique"),
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    o["fill_rate_%"] = (o["confirmed_qty"] / o["order_qty"].replace(0, np.nan) * 100).round(2)
    r = (
        ret.groupby("District")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
    )
    merged = o.merge(r, on="District", how="left").fillna(0)
    total = merged["order_value_lkr"].sum() or 1
    merged["value_share_%"] = (merged["order_value_lkr"] / total * 100).round(2)
    return merged.sort_values("order_value_lkr", ascending=False).reset_index(drop=True)


def province_performance(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Province-level aggregation — highest geographic summary level."""
    o = (
        po.groupby("Province")
        .agg(
            unique_dealers=("Dealer Code", "nunique"),
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    o["fill_rate_%"] = (o["confirmed_qty"] / o["order_qty"].replace(0, np.nan) * 100).round(2)
    r = (
        ret.groupby("Province")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
    )
    merged = o.merge(r, on="Province", how="left").fillna(0)
    total = merged["order_value_lkr"].sum() or 1
    merged["value_share_%"] = (merged["order_value_lkr"] / total * 100).round(2)
    merged["return_rate_%"] = (
        merged["return_value_lkr"] / merged["order_value_lkr"].replace(0, np.nan) * 100
    ).round(2)
    return merged.sort_values("order_value_lkr", ascending=False).reset_index(drop=True)


def monthly_trend(clean: pd.DataFrame) -> pd.DataFrame:
    """Monthly order value, lines, fill rate, return lines — all segments combined."""
    po = clean[clean["doc_type"] == "PO"]
    ret = clean[clean["doc_type"] == "Return"]

    po_m = (
        po.groupby("Year_Month_str")
        .agg(
            po_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            order_value=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    po_m["fill_rate_%"] = (
        po_m["confirmed_qty"] / po_m["order_qty"].replace(0, np.nan) * 100
    ).round(2)

    ret_m = (
        ret.groupby("Year_Month_str")
        .agg(
            return_lines=("Sales Document", "count"),
            return_value=("confirmed_value", "sum"),
        )
        .reset_index()
    )
    monthly = po_m.merge(ret_m, on="Year_Month_str", how="left").fillna(0)
    monthly.rename(columns={"Year_Month_str": "Month"}, inplace=True)
    return monthly.sort_values("Month").reset_index(drop=True)


def monthly_trend_by_category(po: pd.DataFrame) -> pd.DataFrame:
    """Monthly value split by MC sub-category + OBM — for trend comparison."""
    po = po.copy()
    po["segment"] = po.apply(
        lambda r: r["mc_category"] if r["dealer_type"] == "MC" else "OBM", axis=1
    )
    agg = (
        po.groupby(["Year_Month_str", "segment"])
        .agg(
            order_value_lkr=("confirmed_value", "sum"),
            po_lines=("Sales Document", "count"),
        )
        .reset_index()
        .rename(columns={"Year_Month_str": "Month"})
        .sort_values(["Month", "segment"])
        .reset_index(drop=True)
    )
    return agg


def fill_rate_trend(po: pd.DataFrame) -> pd.DataFrame:
    """Monthly fill rate by MC sub-category and OBM — service level tracking."""
    po = po.copy()
    po["segment"] = po.apply(
        lambda r: r["mc_category"] if r["dealer_type"] == "MC" else "OBM", axis=1
    )
    agg = (
        po.groupby(["Year_Month_str", "segment"])
        .agg(
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        )
        .reset_index()
    )
    agg["fill_rate_%"] = (agg["confirmed_qty"] / agg["order_qty"].replace(0, np.nan) * 100).round(2)
    return (
        agg[["Year_Month_str", "segment", "fill_rate_%"]]
        .rename(columns={"Year_Month_str": "Month"})
        .sort_values(["Month", "segment"])
        .reset_index(drop=True)
    )


def returns_analysis(ret: pd.DataFrame) -> pd.DataFrame:
    """Returns grouped by dealer, category, and reason — return root-cause analysis."""
    if ret.empty:
        return pd.DataFrame()
    ret = ret.copy()
    ret["segment"] = ret.apply(
        lambda r: r["mc_category"] if r["dealer_type"] == "MC" else "OBM", axis=1
    )
    # Use unified return_reason column produced by load_orders(); fall back to source columns
    reason_col = "return_reason"
    if reason_col not in ret.columns or ret[reason_col].fillna("").eq("").all():
        for _c in [
            "Order Reason Description",
            "Rejection Reason Description",
            "Reason for Rejection",
        ]:
            if _c in ret.columns:
                reason_col = _c
                break

    agg = (
        ret.groupby(["Province", "District", "Dealer Name", "segment", reason_col])
        .agg(
            return_lines=("Sales Document", "count"),
            return_qty=("Order Quantity (Item)", "sum"),
            return_value_lkr=("confirmed_value", "sum"),
        )
        .reset_index()
        .sort_values("return_value_lkr", ascending=False)
        .reset_index(drop=True)
    )
    total = agg["return_value_lkr"].sum() or 1
    agg["value_share_%"] = (agg["return_value_lkr"] / total * 100).round(2)
    return agg


def short_ship_analysis(po: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """Top materials by short-shipped quantity — supply gap analysis."""
    short = po[po["lost_qty"] < 0].copy()
    if short.empty:
        return pd.DataFrame()
    agg = (
        short.groupby(["Material", "Material Description"])
        .agg(
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            short_qty=("lost_qty", lambda x: abs(x.sum())),
            occurrences=("lost_qty", "count"),
            affected_dealers=("Dealer Code", "nunique"),
        )
        .reset_index()
    )
    agg["fill_rate_%"] = (agg["confirmed_qty"] / agg["order_qty"].replace(0, np.nan) * 100).round(2)
    return agg.sort_values("short_qty", ascending=False).head(top_n).reset_index(drop=True)


def rejection_reasons_summary(rejected: pd.DataFrame) -> pd.DataFrame:
    """Fully-rejected PO lines by rejection reason."""
    if rejected.empty or "Reason for Rejection" not in rejected.columns:
        return pd.DataFrame(
            columns=["Reason for Rejection", "rejected_lines", "rejected_qty", "share_%"]
        )
    agg = (
        rejected.groupby("Reason for Rejection")
        .agg(
            rejected_lines=("Sales Document", "count"),
            rejected_qty=("Order Quantity (Item)", "sum"),
        )
        .reset_index()
    )
    total = agg["rejected_lines"].sum() or 1
    agg["share_%"] = (agg["rejected_lines"] / total * 100).round(2)
    return agg.sort_values("rejected_lines", ascending=False).reset_index(drop=True)


def orders_received_breakdown(clean: pd.DataFrame, rejected: pd.DataFrame) -> dict[str, int]:
    """Document-level fulfillment classification across all PO documents."""
    all_po = pd.concat(
        [clean[clean["doc_type"] == "PO"], rejected[rejected["doc_type"] == "PO"]],
        ignore_index=True,
    )
    if all_po.empty:
        return {"total_documents": 0, "fully_filled": 0, "partial_fill": 0, "complete_zero": 0}

    doc_status = all_po.groupby("Sales Document").apply(
        lambda g: (
            "complete_zero"
            if (g["Confirmed Quantity (Item)"] == 0).all()
            else "fully_filled"
            if (g["Confirmed Quantity (Item)"] >= g["Order Quantity (Item)"]).all()
            else "partial_fill"
        )
    )
    counts = doc_status.value_counts()
    return {
        "total_documents": int(len(doc_status)),
        "fully_filled": int(counts.get("fully_filled", 0)),
        "partial_fill": int(counts.get("partial_fill", 0)),
        "complete_zero": int(counts.get("complete_zero", 0)),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2b. Business insight analysis functions
# ══════════════════════════════════════════════════════════════════════════════


def dealer_health_scorecard(po: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Composite dealer health score (0-100) combining value, fill, returns, frequency, YoY growth.

    Business meaning: single-view ranking of dealer health for account management.
    Score components:
        30 pts — order value share (quintile-based)
        25 pts — fill rate band (>98 / 95-98 / 90-95 / <90)
        20 pts — return rate band (<0.5% / 0.5-2% / 2-5% / >5%)
        15 pts — order frequency (active_months / total_months * 15)
        10 pts — YoY revenue growth (positive / flat / decline)
    Tiers: A ≥70, B 45-69, C <45
    """
    if po.empty:
        return pd.DataFrame()

    # Determine dataset date window
    min_month = po["Year_Month_str"].min()
    max_month = po["Year_Month_str"].max()
    try:
        total_months = (pd.Period(max_month, "M") - pd.Period(min_month, "M")).n + 1
    except Exception:
        total_months = 24
    total_months = max(total_months, 1)

    # Order value aggregation
    order_agg = (
        po.groupby(["Dealer Code", "Dealer Name", "Province", "RM", "ASE"])
        .agg(
            order_value_lkr=("confirmed_value", "sum"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            active_months=("Year_Month_str", "nunique"),
        )
        .reset_index()
    )
    order_agg["fill_rate_pct"] = (
        order_agg["confirmed_qty"] / order_agg["order_qty"].replace(0, np.nan) * 100
    ).fillna(0)

    # Return rate
    if not ret.empty:
        ret_agg = (
            ret.groupby("Dealer Code")["confirmed_value"]
            .sum()
            .reset_index()
            .rename(columns={"confirmed_value": "return_value_lkr"})
        )
        order_agg = order_agg.merge(ret_agg, on="Dealer Code", how="left")
        order_agg["return_value_lkr"] = order_agg["return_value_lkr"].fillna(0)
    else:
        order_agg["return_value_lkr"] = 0.0

    order_agg["return_rate_pct"] = (
        order_agg["return_value_lkr"] / order_agg["order_value_lkr"].replace(0, np.nan) * 100
    ).fillna(0)

    # YoY growth: compare 2024 vs 2025 (or first vs last year in data)
    years = sorted(po["Created On"].dt.year.unique())
    if len(years) >= 2:
        yr_first, yr_last = years[0], years[-1]
        val_first = (
            po[po["Created On"].dt.year == yr_first].groupby("Dealer Code")["confirmed_value"].sum()
        )
        val_last = (
            po[po["Created On"].dt.year == yr_last].groupby("Dealer Code")["confirmed_value"].sum()
        )
        yoy = (val_last - val_first).reindex(order_agg["Dealer Code"].values)
        order_agg["yoy_growth_lkr"] = yoy.values
    else:
        order_agg["yoy_growth_lkr"] = 0.0
    order_agg["yoy_growth_lkr"] = order_agg["yoy_growth_lkr"].fillna(0)

    # ── Scoring ───────────────────────────────────────────────────────────────
    # Value score (30 pts) — quintile rank
    order_agg["_val_quintile"] = (
        pd.qcut(order_agg["order_value_lkr"], q=5, labels=False, duplicates="drop")
        .fillna(0)
        .astype(int)
    )
    max_q = order_agg["_val_quintile"].max() or 1
    order_agg["value_score"] = (order_agg["_val_quintile"] / max_q * 30).round(1)

    # Fill score (25 pts)
    def _fill_score(fr: float) -> float:
        if fr >= 98:
            return 25.0
        elif fr >= 95:
            return 18.0
        elif fr >= 90:
            return 10.0
        return 0.0

    order_agg["fill_score"] = order_agg["fill_rate_pct"].apply(_fill_score)

    # Return score (20 pts)
    def _return_score(rr: float) -> float:
        if rr < 0.5:
            return 20.0
        elif rr < 2.0:
            return 12.0
        elif rr < 5.0:
            return 5.0
        return 0.0

    order_agg["return_score"] = order_agg["return_rate_pct"].apply(_return_score)

    # Frequency score (15 pts)
    order_agg["frequency_score"] = (
        (order_agg["active_months"] / total_months * 15).clip(0, 15).round(1)
    )

    # Growth score (10 pts)
    order_agg["growth_score"] = order_agg["yoy_growth_lkr"].apply(
        lambda x: 10.0 if x > 0 else (5.0 if x == 0 else 0.0)
    )

    order_agg["health_score"] = (
        order_agg["value_score"]
        + order_agg["fill_score"]
        + order_agg["return_score"]
        + order_agg["frequency_score"]
        + order_agg["growth_score"]
    ).round(1)

    order_agg["score_tier"] = order_agg["health_score"].apply(
        lambda s: "A" if s >= 70 else ("B" if s >= 45 else "C")
    )

    logger.info(
        f"Dealer health scorecard: "
        f"A={( order_agg['score_tier']=='A').sum()} "
        f"B={( order_agg['score_tier']=='B').sum()} "
        f"C={( order_agg['score_tier']=='C').sum()}"
    )

    keep = [
        "Dealer Code",
        "Dealer Name",
        "Province",
        "RM",
        "ASE",
        "health_score",
        "score_tier",
        "value_score",
        "fill_score",
        "return_score",
        "frequency_score",
        "growth_score",
        "order_value_lkr",
        "fill_rate_pct",
        "return_rate_pct",
        "active_months",
    ]
    return (
        order_agg[[c for c in keep if c in order_agg.columns]]
        .sort_values("health_score", ascending=False)
        .reset_index(drop=True)
    )


def yoy_growth_analysis(po: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year revenue and fill-rate comparison (2024 vs 2025) by segment.

    Business meaning: shows which product/dealer segments are growing vs contracting,
    guiding inventory investment and supplier negotiation priorities.
    """

    def _year_agg(df: pd.DataFrame, year: int) -> pd.Series:
        sub = df[df["Created On"].dt.year == year]
        return pd.Series(
            {
                f"value_{year}": sub["confirmed_value"].sum(),
                f"lines_{year}": len(sub),
                f"fill_rate_{year}": (
                    sub["Confirmed Quantity (Item)"].sum()
                    / max(sub["Order Quantity (Item)"].sum(), 1)
                    * 100
                ),
            }
        )

    years = sorted(po["Created On"].dt.year.unique())
    yr_a = years[0] if len(years) >= 1 else 2024
    yr_b = years[1] if len(years) >= 2 else 2025

    segments: list[tuple[str, pd.DataFrame]] = [
        ("MC-Lubricant", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Lubricant")]),
        ("MC-Battery", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Battery")]),
        ("MC-Tyre", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Tyre")]),
        ("MC-Spare Parts", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Spare Parts")]),
        ("OBM", po[po["dealer_type"] == "OBM"]),
        ("Total", po),
    ]

    rows = []
    for label, seg in segments:
        a = _year_agg(seg, yr_a)
        b = _year_agg(seg, yr_b)
        val_a = a[f"value_{yr_a}"]
        val_b = b[f"value_{yr_b}"]
        yoy_pct = (val_b - val_a) / max(val_a, 1) * 100 if val_a else 0.0
        rows.append(
            {
                "segment": label,
                f"value_{yr_a}": round(val_a),
                f"value_{yr_b}": round(val_b),
                "yoy_growth_pct": round(yoy_pct, 2),
                f"lines_{yr_a}": int(a[f"lines_{yr_a}"]),
                f"lines_{yr_b}": int(b[f"lines_{yr_b}"]),
                f"fill_rate_{yr_a}": round(float(a[f"fill_rate_{yr_a}"]), 2),
                f"fill_rate_{yr_b}": round(float(b[f"fill_rate_{yr_b}"]), 2),
            }
        )

    df = pd.DataFrame(rows)
    logger.info(
        "YoY growth: "
        + "  ".join(
            f"{r['segment']} {r['yoy_growth_pct']:+.1f}%"
            for _, r in df[df["segment"] != "Total"].iterrows()
        )
    )
    return df


def dealer_ordering_behavior(po: pd.DataFrame) -> pd.DataFrame:
    """Ordering pattern metrics per dealer: frequency, consistency, recency, dormancy.

    Business meaning: identifies dealers who are ordering infrequently or have gone silent,
    enabling proactive account management before stock accumulates.
    dormant_flag = True when last order is more than 90 days before dataset end date.
    """
    if po.empty:
        return pd.DataFrame()

    dataset_end = po["Created On"].max()

    agg = (
        po.groupby(["Dealer Code", "Dealer Name", "Province", "RM", "ASE"])
        .agg(
            order_value_lkr=("confirmed_value", "sum"),
            order_count=("Sales Document", "nunique"),
            active_months=("Year_Month_str", "nunique"),
            first_order_date=("Created On", "min"),
            last_order_date=("Created On", "max"),
        )
        .reset_index()
    )

    agg["avg_order_value_lkr"] = (
        agg["order_value_lkr"] / agg["order_count"].replace(0, np.nan)
    ).round(0)

    # avg days between orders approximation
    agg["avg_days_between_orders"] = (
        agg["active_months"] / agg["order_count"].replace(0, np.nan) * 30
    ).round(1)

    # Consistency: active months out of 24-month window
    agg["order_consistency_pct"] = (agg["active_months"] / 24 * 100).clip(0, 100).round(1)

    # Dormancy: last order more than 90 days before dataset end
    agg["days_since_last_order"] = (dataset_end - agg["last_order_date"]).dt.days
    agg["dormant_flag"] = agg["days_since_last_order"] > 90

    agg["first_order_date"] = agg["first_order_date"].dt.strftime("%Y-%m-%d")
    agg["last_order_date"] = agg["last_order_date"].dt.strftime("%Y-%m-%d")

    dormant_count = agg["dormant_flag"].sum()
    logger.info(
        f"Dealer ordering behavior: {len(agg)} dealers  " f"dormant (>90d silent): {dormant_count}"
    )

    keep = [
        "Dealer Code",
        "Dealer Name",
        "Province",
        "RM",
        "ASE",
        "order_value_lkr",
        "order_count",
        "active_months",
        "avg_order_value_lkr",
        "avg_days_between_orders",
        "order_consistency_pct",
        "first_order_date",
        "last_order_date",
        "days_since_last_order",
        "dormant_flag",
    ]
    return (
        agg[[c for c in keep if c in agg.columns]]
        .sort_values("order_value_lkr", ascending=False)
        .reset_index(drop=True)
    )


def pareto_analysis(po: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Pareto (80/20) analysis for materials and dealers by order value.

    Business meaning: confirms which SKUs and dealers drive 80% of spend,
    used to focus inventory investment (A-class) vs rationalise (C-class).
    Returns dict with keys 'part_pareto' and 'dealer_pareto'.
    """

    def _pareto(df: pd.DataFrame, group_col: str, name_col: str | None = None) -> pd.DataFrame:
        cols = [group_col] if name_col is None else [group_col, name_col]
        # avoid duplicate column names when group_col == name_col
        cols = list(dict.fromkeys(cols))
        agg = (
            df.groupby(cols)["confirmed_value"]
            .sum()
            .reset_index()
            .rename(columns={"confirmed_value": "total_value_lkr"})
            .sort_values("total_value_lkr", ascending=False)
            .reset_index(drop=True)
        )
        total = agg["total_value_lkr"].sum() or 1
        agg["value_share_pct"] = (agg["total_value_lkr"] / total * 100).round(3)
        agg["cumul_share_pct"] = agg["value_share_pct"].cumsum().round(3)
        agg["pareto_class"] = agg["cumul_share_pct"].apply(
            lambda x: "A" if x <= 80 else ("B" if x <= 95 else "C")
        )
        return agg

    part_pareto = _pareto(po, "Material", "Material Description")
    dealer_pareto = _pareto(po, "Dealer Code", "Dealer Name")

    a_parts = (part_pareto["pareto_class"] == "A").sum()
    a_dealers = (dealer_pareto["pareto_class"] == "A").sum()
    total_parts = len(part_pareto)
    total_dealers = len(dealer_pareto)
    logger.info(
        f"Pareto: {a_parts}/{total_parts} SKUs → 80% of value  |  "
        f"{a_dealers}/{total_dealers} dealers → 80% of value"
    )
    return {"part_pareto": part_pareto, "dealer_pareto": dealer_pareto}


def fill_rate_band_distribution(po: pd.DataFrame) -> pd.DataFrame:
    """Count of dealers in each fill-rate band, broken out by segment.

    Business meaning: shows what proportion of the dealer base is receiving acceptable
    service levels vs experiencing chronic under-supply.
    Bands: >98%, 95-98%, 90-95%, <90% (dealer-level fill rate).
    """
    if po.empty:
        return pd.DataFrame()

    # Compute dealer-level fill rate per segment
    segments: list[tuple[str, pd.DataFrame]] = [
        ("MC-Lubricant", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Lubricant")]),
        ("MC-Battery", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Battery")]),
        ("MC-Tyre", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Tyre")]),
        ("MC-Spare Parts", po[(po["dealer_type"] == "MC") & (po["mc_category"] == "Spare Parts")]),
        ("OBM", po[po["dealer_type"] == "OBM"]),
    ]

    rows = []
    for label, seg in segments:
        if seg.empty:
            rows.append(
                {
                    "segment": label,
                    "dealers_above_98pct": 0,
                    "dealers_95_98pct": 0,
                    "dealers_90_95pct": 0,
                    "dealers_below_90pct": 0,
                    "total_dealers": 0,
                }
            )
            continue

        dealer_fr = seg.groupby("Dealer Code").agg(
            oq=("Order Quantity (Item)", "sum"),
            cq=("Confirmed Quantity (Item)", "sum"),
        )
        dealer_fr["fr_pct"] = (dealer_fr["cq"] / dealer_fr["oq"].replace(0, np.nan) * 100).fillna(0)

        rows.append(
            {
                "segment": label,
                "dealers_above_98pct": int((dealer_fr["fr_pct"] > 98).sum()),
                "dealers_95_98pct": int(
                    ((dealer_fr["fr_pct"] >= 95) & (dealer_fr["fr_pct"] <= 98)).sum()
                ),
                "dealers_90_95pct": int(
                    ((dealer_fr["fr_pct"] >= 90) & (dealer_fr["fr_pct"] < 95)).sum()
                ),
                "dealers_below_90pct": int((dealer_fr["fr_pct"] < 90).sum()),
                "total_dealers": int(len(dealer_fr)),
            }
        )

    return pd.DataFrame(rows)


def supply_gap_calendar(po: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Month × material heatmap of short-shipped quantity for the top-N short-shipped SKUs.

    Business meaning: reveals whether supply gaps are structural (year-round) or seasonal,
    guiding buffer-stock sizing and supplier negotiation timing.
    Rows = Year_Month_str, Columns = top N materials by total short qty.
    Values = short qty on that month (0 when no gap).
    """
    short = po[po["lost_qty"] < 0].copy()
    if short.empty:
        logger.warning("supply_gap_calendar: no short-shipped lines found.")
        return pd.DataFrame()

    # Identify top N materials by total short qty
    mat_totals = (
        short.groupby("Material")["lost_qty"]
        .apply(lambda x: abs(x.sum()))
        .sort_values(ascending=False)
        .head(top_n)
    )
    top_mats = mat_totals.index.tolist()

    # Filter and pivot
    short_top = short[short["Material"].isin(top_mats)].copy()
    short_top["short_qty"] = short_top["lost_qty"].abs()

    pivot = (
        short_top.groupby(["Year_Month_str", "Material"])["short_qty"]
        .sum()
        .unstack(fill_value=0)
        .reindex(columns=top_mats, fill_value=0)
        .reset_index()
        .rename(columns={"Year_Month_str": "Month"})
        .sort_values("Month")
        .reset_index(drop=True)
    )

    logger.info(
        f"Supply gap calendar: {len(pivot)} months × {len(top_mats)} top short-shipped materials"
    )
    return pivot


def category_cross_analysis(mc_po: pd.DataFrame) -> pd.DataFrame:
    """Identify MC dealers who order across multiple sub-categories.

    Business meaning: multi-category dealers are higher-value, lower-churn accounts
    worth priority service; single-category dealers are cross-sell opportunities.
    Input must already be filtered to MC POs only.
    """
    if mc_po.empty:
        return pd.DataFrame()

    cats = ["Lubricant", "Battery", "Tyre", "Spare Parts"]

    def _dealer_cats(g: pd.DataFrame) -> pd.Series:
        ordered = set(g["mc_category"].unique())
        cat_list = [c for c in cats if c in ordered]
        return pd.Series(
            {
                "categories_ordered": ", ".join(cat_list) if cat_list else "",
                "category_count": len(cat_list),
                "orders_lubricant": "Lubricant" in ordered,
                "orders_battery": "Battery" in ordered,
                "orders_tyre": "Tyre" in ordered,
                "orders_spare_parts": "Spare Parts" in ordered,
            }
        )

    cat_df = (
        mc_po.groupby(["Dealer Code", "Dealer Name", "Province", "RM"])
        .apply(_dealer_cats)
        .reset_index()
    )

    multi = (cat_df["category_count"] > 1).sum()
    logger.info(
        f"Category cross-analysis: {len(cat_df)} MC dealers  "
        f"multi-category={multi}  single-category={len(cat_df) - multi}"
    )

    return cat_df.sort_values("category_count", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Excel report
# ══════════════════════════════════════════════════════════════════════════════


def _fmts(wb: xlsxwriter.Workbook) -> dict:  # type: ignore[type-arg]
    def _f(**kw: Any) -> Any:
        return wb.add_format({"border": 1, **kw})

    return {
        "title": wb.add_format({"bold": True, "font_size": 14, "font_color": _BLUE}),
        "sub": wb.add_format({"italic": True, "font_color": "#555555"}),
        "hdr": _f(bold=True, bg_color=_BLUE, font_color="white", align="center"),
        "hdr_r": _f(bold=True, bg_color=_RED, font_color="white", align="center"),
        "hdr_g": _f(bold=True, bg_color=_GREEN, font_color="white", align="center"),
        "hdr_a": _f(bold=True, bg_color=_AMBER, font_color="white", align="center"),
        "hdr_p": _f(bold=True, bg_color=_PURPLE, font_color="white", align="center"),
        "hdr_t": _f(bold=True, bg_color=_TEAL, font_color="white", align="center"),
        "num": _f(num_format="#,##0"),
        "num2": _f(num_format="#,##0.00"),
        "pct": _f(num_format="0.00"),
        "cell": _f(),
        "label": _f(bold=True, bg_color="#E3F2FD"),
    }


def _kv(ws: Any, row: int, col: int, k: str, v: Any, f: dict) -> None:  # type: ignore[type-arg]
    ws.write(row, col, k, f["label"])
    if isinstance(v, float):
        ws.write(row, col + 1, v, f["num2"])
    elif isinstance(v, int):
        ws.write(row, col + 1, v, f["num"])
    else:
        ws.write(row, col + 1, str(v), f["cell"])


def _df(ws: Any, df: pd.DataFrame, f: dict, hdr: str = "hdr", row0: int = 0) -> None:  # type: ignore[type-arg]
    for c, col in enumerate(df.columns):
        ws.write(row0, c, col, f[hdr])
    for r, (_, row) in enumerate(df.iterrows()):
        for c, val in enumerate(row):
            if isinstance(val, int | np.integer):
                ws.write(row0 + r + 1, c, int(val), f["num"])
            elif isinstance(val, float | np.floating):
                ws.write(row0 + r + 1, c, round(float(val), 2), f["num2"])
            else:
                ws.write(row0 + r + 1, c, str(val) if pd.notna(val) else "", f["cell"])


def _seg_sheet(
    wb: xlsxwriter.Workbook,
    f: dict,
    name: str,
    po_seg: pd.DataFrame,
    ret_seg: pd.DataFrame,
    hdr_key: str = "hdr",
) -> None:
    """Write a two-section sheet: Top Parts + Dealer Performance for one segment."""
    parts = part_analysis(po_seg)
    dealers = dealer_performance(po_seg, ret_seg)

    ws = wb.add_worksheet(name[:31])
    ws.set_column("A:A", 18)
    ws.set_column("B:B", 50)
    ws.set_column("C:K", 18)

    ws.write("A1", f"{name} — Part Analysis (Top 50 by Value)", f["title"])
    _df(ws, parts, f, hdr=hdr_key, row0=2)

    start = len(parts) + 5
    ws.write(start, 0, f"{name} — Dealer Performance", f["title"])
    _df(ws, dealers, f, hdr=hdr_key, row0=start + 2)


def write_excel_report(
    kpis: dict,
    cat_mix_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    monthly_cat: pd.DataFrame,
    fill_trend: pd.DataFrame,
    rm_df: pd.DataFrame,
    ase_df: pd.DataFrame,
    district_df: pd.DataFrame,
    province_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    shortship_df: pd.DataFrame,
    rej_reasons: pd.DataFrame,
    rejection_log: pd.DataFrame,
    ord_recv: dict,
    po_mc_lube: pd.DataFrame,
    po_mc_batt: pd.DataFrame,
    po_mc_tyre: pd.DataFrame,
    po_mc_spare: pd.DataFrame,
    ret_mc: pd.DataFrame,
    po_obm: pd.DataFrame,
    ret_obm: pd.DataFrame,
    # ── Business insight sheets ────────────────────────────────────────────
    health_df: pd.DataFrame | None = None,
    yoy_df: pd.DataFrame | None = None,
    behavior_df: pd.DataFrame | None = None,
    part_pareto: pd.DataFrame | None = None,
    dealer_pareto: pd.DataFrame | None = None,
    fill_bands_df: pd.DataFrame | None = None,
    gap_calendar: pd.DataFrame | None = None,
    cross_df: pd.DataFrame | None = None,
) -> None:
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))
    f = _fmts(wb)

    # ── Sheet 1: Summary ──────────────────────────────────────────────────────
    ws = wb.add_worksheet("Summary")
    ws.set_column("A:A", 36)
    ws.set_column("B:B", 28)
    ws.write("A1", "Stage 4 — Orders EDA: Yamaha Spare Parts Dealer Orders", f["title"])
    ws.write(
        "A2",
        "Active dealers only · MC: Lubricant, Battery, Tyre, Spare Parts · OBM single block",
        f["sub"],
    )
    for r, (k, v) in enumerate(kpis.items()):
        _kv(ws, r + 4, 0, k, v, f)

    # Order fulfillment breakdown
    r0 = len(kpis) + 6
    ws.write(r0, 0, "Order Fulfillment (document level)", f["title"])
    labels = {
        "total_documents": "Total PO Documents",
        "fully_filled": "Fully Filled",
        "partial_fill": "Partially Filled",
        "complete_zero": "Complete Zero",
    }
    for i, (k, lbl) in enumerate(labels.items()):
        _kv(ws, r0 + 2 + i, 0, lbl, ord_recv.get(k, 0), f)
    total = ord_recv.get("total_documents", 1) or 1
    _kv(
        ws,
        r0 + 2 + len(labels),
        0,
        "Fulfillment Rate %",
        round(ord_recv.get("fully_filled", 0) / total * 100, 2),
        f,
    )

    # Category mix table
    r0 = r0 + len(labels) + 5
    ws.write(r0, 0, "Category Mix", f["title"])
    _df(ws, cat_mix_df, f, row0=r0 + 2)

    # ── Sheet 2: Monthly Trend ────────────────────────────────────────────────
    ws = wb.add_worksheet("Monthly Trend")
    ws.set_column("A:A", 12)
    ws.set_column("B:I", 18)
    ws.write("A1", "Monthly Order Trend — All Segments", f["title"])
    _df(ws, monthly_df, f, row0=2)

    n = len(monthly_df)
    ch = wb.add_chart({"type": "column"})
    ch.add_series(
        {
            "name": "Order Value",
            "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
            "values": ["Monthly Trend", 3, 4, 2 + n, 4],
            "fill": {"color": _BLUE},
        }
    )
    ch2 = wb.add_chart({"type": "line"})
    ch2.add_series(
        {
            "name": "Fill Rate %",
            "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
            "values": ["Monthly Trend", 3, 6, 2 + n, 6],
            "line": {"color": _AMBER, "width": 2},
            "y2_axis": True,
        }
    )
    ch.combine(ch2)
    ch.set_title({"name": "Monthly PO Value & Fill Rate"})
    ch.set_y2_axis({"name": "Fill Rate %", "min": 0, "max": 100})
    ch.set_size({"width": 720, "height": 320})
    ws.insert_chart("K3", ch)

    # Category trend below
    ws.write(n + 6, 0, "Monthly Value by Category", f["title"])
    _df(ws, monthly_cat, f, row0=n + 8)

    # Fill rate trend below that
    start = n + 8 + len(monthly_cat) + 4
    ws.write(start, 0, "Fill Rate % by Category (Monthly)", f["title"])
    _df(ws, fill_trend, f, row0=start + 2)

    # ── Sheet 3: MC Lubricant ─────────────────────────────────────────────────
    _seg_sheet(wb, f, "MC – Lubricant", po_mc_lube, ret_mc, hdr_key="hdr_a")

    # ── Sheet 4: MC Battery ───────────────────────────────────────────────────
    _seg_sheet(wb, f, "MC – Battery", po_mc_batt, ret_mc, hdr_key="hdr")

    # ── Sheet 5: MC Tyre ──────────────────────────────────────────────────────
    _seg_sheet(wb, f, "MC – Tyre", po_mc_tyre, ret_mc, hdr_key="hdr_g")

    # ── Sheet 6: MC Spare Parts ───────────────────────────────────────────────
    _seg_sheet(wb, f, "MC – Spare Parts", po_mc_spare, ret_mc, hdr_key="hdr_p")

    # ── Sheet 7: OBM ─────────────────────────────────────────────────────────
    _seg_sheet(wb, f, "OBM", po_obm, ret_obm, hdr_key="hdr_t")

    # ── Sheet 8: RM Performance ───────────────────────────────────────────────
    ws = wb.add_worksheet("RM Performance")
    ws.set_column("A:A", 28)
    ws.set_column("B:J", 18)
    ws.write("A1", "Regional Manager (RM) Performance — All Segments", f["title"])
    _df(ws, rm_df, f, row0=2)

    # ── Sheet 9: ASE Performance ──────────────────────────────────────────────
    ws = wb.add_worksheet("ASE Performance")
    ws.set_column("A:A", 28)
    ws.set_column("B:K", 18)
    ws.write("A1", "Area Sales Executive (ASE) Performance — All Segments", f["title"])
    _df(ws, ase_df, f, row0=2)

    # ── Sheet 10: Province Performance ────────────────────────────────────────
    ws = wb.add_worksheet("Province Performance")
    ws.set_column("A:A", 22)
    ws.set_column("B:I", 18)
    ws.write("A1", "Province-Level Performance", f["title"])
    _df(ws, province_df, f, row0=2)

    # ── Sheet 11: District Performance ────────────────────────────────────────
    ws = wb.add_worksheet("District Performance")
    ws.set_column("A:A", 20)
    ws.set_column("B:J", 18)
    ws.write("A1", "District-Level Performance", f["title"])
    _df(ws, district_df, f, row0=2)

    # ── Sheet 12: Returns Analysis ────────────────────────────────────────────
    ws = wb.add_worksheet("Returns Analysis")
    ws.set_column("A:F", 22)
    ws.set_column("G:J", 18)
    ws.write("A1", "Returns (SD Category H) — By Dealer, Segment & Reason", f["title"])
    ws.write("A2", "Return Rate = return value / order value per dealer", f["sub"])
    _df(ws, returns_df, f, hdr="hdr_r", row0=3)

    # ── Sheet 13: Short-Ship ──────────────────────────────────────────────────
    ws = wb.add_worksheet("Short-Ship")
    ws.set_column("A:A", 22)
    ws.set_column("B:B", 50)
    ws.set_column("C:H", 18)
    ws.write("A1", f"Top {len(shortship_df)} Short-Shipped Materials", f["title"])
    ws.write("A2", "short_qty = |Confirmed − Ordered| on lines where Confirmed < Ordered", f["sub"])
    _df(ws, shortship_df, f, hdr="hdr_r", row0=3)

    # ── Sheet 14: Rejection Reasons ───────────────────────────────────────────
    if not rej_reasons.empty:
        ws = wb.add_worksheet("Rejection Reasons")
        ws.set_column("A:A", 50)
        ws.set_column("B:D", 18)
        ws.write("A1", "Rejection Reasons — Fully-Rejected PO Lines", f["title"])
        _df(ws, rej_reasons, f, hdr="hdr_r", row0=3)

    # ── Sheet 15: Rejection Log ───────────────────────────────────────────────
    ws = wb.add_worksheet("Rejection Log")
    ws.set_column("A:C", 22)
    ws.set_column("D:D", 50)
    ws.set_column("E:J", 16)
    ws.write("A1", "Fully Rejected PO Lines (Confirmed Quantity = 0)", f["title"])
    rej_cols = [
        "Sales Document",
        "Dealer Code",
        "Dealer Name",
        "Material",
        "Material Description",
        "Order Quantity (Item)",
        "Year_Month_str",
        "Reason for Rejection",
        "dealer_type",
        "mc_category",
    ]
    rej_disp = rejection_log[[c for c in rej_cols if c in rejection_log.columns]].copy()
    _df(ws, rej_disp, f, hdr="hdr_r", row0=3)

    # ── Sheet 16: Dealer Health Scorecard ────────────────────────────────────
    if health_df is not None and not health_df.empty:
        ws = wb.add_worksheet("Dealer Health Score")
        ws.set_column("A:A", 18)
        ws.set_column("B:B", 30)
        ws.set_column("C:F", 20)
        ws.set_column("G:P", 16)
        ws.write("A1", "Dealer Health Scorecard (0–100 composite)", f["title"])
        ws.write(
            "A2",
            "A≥70 · B 45-69 · C<45 — value(30) fill(25) return(20) freq(15) YoY(10)",
            f["sub"],
        )
        _df(ws, health_df, f, hdr="hdr_g", row0=3)

    # ── Sheet 17: YoY Growth ──────────────────────────────────────────────────
    if yoy_df is not None and not yoy_df.empty:
        ws = wb.add_worksheet("YoY Growth")
        ws.set_column("A:A", 20)
        ws.set_column("B:I", 18)
        ws.write("A1", "Year-on-Year Revenue & Fill Rate Comparison by Segment", f["title"])
        _df(ws, yoy_df, f, hdr="hdr", row0=2)

    # ── Sheet 18: Dealer Behavior ─────────────────────────────────────────────
    if behavior_df is not None and not behavior_df.empty:
        ws = wb.add_worksheet("Dealer Behavior")
        ws.set_column("A:A", 18)
        ws.set_column("B:B", 30)
        ws.set_column("C:O", 20)
        ws.write("A1", "Dealer Ordering Behaviour — Frequency, Consistency & Dormancy", f["title"])
        ws.write("A2", "dormant_flag = last order >90 days before dataset end", f["sub"])
        _df(ws, behavior_df, f, hdr="hdr_a", row0=3)

    # ── Sheet 19: Part Pareto ─────────────────────────────────────────────────
    if part_pareto is not None and not part_pareto.empty:
        ws = wb.add_worksheet("Part Pareto")
        ws.set_column("A:A", 22)
        ws.set_column("B:B", 50)
        ws.set_column("C:F", 18)
        ws.write(
            "A1", "Material Pareto Analysis — A: 0-80% value · B: 80-95% · C: 95-100%", f["title"]
        )
        _df(ws, part_pareto, f, hdr="hdr_p", row0=2)

    # ── Sheet 20: Dealer Pareto ───────────────────────────────────────────────
    if dealer_pareto is not None and not dealer_pareto.empty:
        ws = wb.add_worksheet("Dealer Pareto")
        ws.set_column("A:A", 18)
        ws.set_column("B:B", 30)
        ws.set_column("C:F", 18)
        ws.write(
            "A1", "Dealer Pareto Analysis — A: 0-80% value · B: 80-95% · C: 95-100%", f["title"]
        )
        _df(ws, dealer_pareto, f, hdr="hdr_p", row0=2)

    # ── Sheet 21: Fill Rate Bands ─────────────────────────────────────────────
    if fill_bands_df is not None and not fill_bands_df.empty:
        ws = wb.add_worksheet("Fill Rate Bands")
        ws.set_column("A:A", 20)
        ws.set_column("B:G", 20)
        ws.write("A1", "Fill Rate Band Distribution — Dealer Count per Segment", f["title"])
        ws.write(
            "A2",
            "Counts dealers (not lines) per fill-rate band: >98% / 95-98% / 90-95% / <90%",
            f["sub"],
        )
        _df(ws, fill_bands_df, f, hdr="hdr_g", row0=3)

    # ── Sheet 22: Supply Gap Calendar ─────────────────────────────────────────
    if gap_calendar is not None and not gap_calendar.empty:
        ws = wb.add_worksheet("Supply Gap Calendar")
        ws.set_column("A:A", 12)
        # Set width 12 for all material columns
        n_mat_cols = len(gap_calendar.columns) - 1  # subtract Month column
        if n_mat_cols > 0:
            ws.set_column(1, n_mat_cols, 12)
        ws.write(
            "A1", "Supply Gap Heatmap — Short-Shipped Qty by Month × Top-20 Materials", f["title"]
        )
        ws.write(
            "A2", "Values = |Confirmed − Ordered| where under-confirmed · 0 = no gap", f["sub"]
        )
        _df(ws, gap_calendar, f, hdr="hdr_r", row0=3)

    # ── Sheet 23: Category Cross-Analysis ─────────────────────────────────────
    if cross_df is not None and not cross_df.empty:
        ws = wb.add_worksheet("Category Cross")
        ws.set_column("A:A", 18)
        ws.set_column("B:B", 30)
        ws.set_column("C:K", 20)
        ws.write(
            "A1", "MC Dealer Category Cross-Analysis — Multi-Category Ordering Patterns", f["title"]
        )
        ws.write(
            "A2",
            "Multi-category = lower churn, higher LTV · single-category = cross-sell opportunity",
            f["sub"],
        )
        _df(ws, cross_df, f, hdr="hdr_t", row0=3)

    wb.close()
    logger.info(f"Excel report written → {_OUTPUT_EXCEL}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def run(refresh: bool = False) -> None:
    """Execute Stage 4: Orders EDA with full MC/OBM split and geographic analysis."""
    if not refresh and _CLEAN_PARQUET.exists():
        logger.info("orders_clean.parquet exists — pass refresh=True to recompute.")
        return

    logger.info("=" * 60)
    logger.info("STAGE 4 — ORDERS EDA  (MC/OBM + MC sub-categories)")
    logger.info("=" * 60)

    dealer_master = load_dealers()
    clean, rejected = load_orders(dealer_master)

    # ── Save parquets ─────────────────────────────────────────────────────────
    _CLEAN_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    for df in (clean, rejected):
        for col in df.select_dtypes("object").columns:
            df[col] = df[col].astype(str).replace("nan", pd.NA)
        if "Year_Month" in df.columns:
            df["Year_Month"] = df["Year_Month"].astype(str)

    clean.to_parquet(_CLEAN_PARQUET, index=False)
    rejected.to_parquet(_REJECT_PARQUET, index=False)
    logger.info(f"Parquets saved: {_CLEAN_PARQUET.name}, {_REJECT_PARQUET.name}")

    # ── Segment slices ────────────────────────────────────────────────────────
    po = clean[clean["doc_type"] == "PO"]
    ret = clean[clean["doc_type"] == "Return"]
    mc = po[po["dealer_type"] == "MC"]
    obm = po[po["dealer_type"] == "OBM"]
    ret_mc = ret[ret["dealer_type"] == "MC"]
    ret_obm = ret[ret["dealer_type"] == "OBM"]

    po_mc_lube = mc[mc["mc_category"] == "Lubricant"]
    po_mc_batt = mc[mc["mc_category"] == "Battery"]
    po_mc_tyre = mc[mc["mc_category"] == "Tyre"]
    po_mc_spare = mc[mc["mc_category"] == "Spare Parts"]

    # ── Computations ──────────────────────────────────────────────────────────
    kpis = summary_kpis(clean, rejected)
    cat_mix_df = category_mix(po)
    monthly_df = monthly_trend(clean)
    monthly_cat = monthly_trend_by_category(po)
    fill_trend = fill_rate_trend(po)
    rm_df = rm_performance(po, ret)
    ase_df = ase_performance(po, ret)
    district_df = district_performance(po, ret)
    province_df = province_performance(po, ret)
    returns_df = returns_analysis(ret)
    shortship = short_ship_analysis(po)
    rej_reasons = rejection_reasons_summary(rejected)
    ord_recv = orders_received_breakdown(clean, rejected)

    # Merge order breakdown into KPIs
    kpis["Total PO Documents"] = ord_recv["total_documents"]
    kpis["Fully Filled Orders"] = ord_recv["fully_filled"]
    kpis["Partially Filled Orders"] = ord_recv["partial_fill"]
    kpis["Complete Zero Orders"] = ord_recv["complete_zero"]

    # ── Business insights ──────────────────────────────────────────────────────
    health_df = dealer_health_scorecard(po, ret)
    yoy_df = yoy_growth_analysis(po)
    behavior_df = dealer_ordering_behavior(po)
    pareto = pareto_analysis(po)
    fill_bands_df = fill_rate_band_distribution(po)
    gap_calendar = supply_gap_calendar(po)
    cross_df = category_cross_analysis(po[po["dealer_type"] == "MC"])

    write_excel_report(
        kpis=kpis,
        cat_mix_df=cat_mix_df,
        monthly_df=monthly_df,
        monthly_cat=monthly_cat,
        fill_trend=fill_trend,
        rm_df=rm_df,
        ase_df=ase_df,
        district_df=district_df,
        province_df=province_df,
        returns_df=returns_df,
        shortship_df=shortship,
        rej_reasons=rej_reasons,
        rejection_log=rejected,
        ord_recv=ord_recv,
        po_mc_lube=po_mc_lube,
        po_mc_batt=po_mc_batt,
        po_mc_tyre=po_mc_tyre,
        po_mc_spare=po_mc_spare,
        ret_mc=ret_mc,
        po_obm=obm,
        ret_obm=ret_obm,
        health_df=health_df,
        yoy_df=yoy_df,
        behavior_df=behavior_df,
        part_pareto=pareto["part_pareto"],
        dealer_pareto=pareto["dealer_pareto"],
        fill_bands_df=fill_bands_df,
        gap_calendar=gap_calendar,
        cross_df=cross_df,
    )

    # ── Summary log ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STAGE 4 SUMMARY")
    logger.info("=" * 60)
    for k, v in kpis.items():
        logger.info(f"  {k:<40}: {v}")

    logger.info("  Category mix (value share):")
    for _, row in cat_mix_df.iterrows():
        logger.info(
            f"    {row['Category']:<22} "
            f"{CURRENCY} {row[f'Value ({CURRENCY})']:>14,.0f}  "
            f"({row['Value Share %']:.1f}%)  fill={row['Fill Rate %']:.1f}%"
        )

    logger.info("  Province performance (top 3 by value):")
    for _, row in province_df.head(3).iterrows():
        logger.info(
            f"    {row['Province']:<20} "
            f"{CURRENCY} {row['order_value_lkr']:>14,.0f}  "
            f"fill={row['fill_rate_%']:.1f}%"
        )

    logger.info("Stage 4 complete.")
