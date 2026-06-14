"""Stage 4: Orders EDA — spare-parts purchase orders and returns analysis.

Business context:
  These are DOMESTIC orders from Yamaha distributor warehouse to spare-parts
  dealers (NOT the India import orders that have the 3-month lead time).
  Lead times here are warehouse-to-dealer (typically 0-3 days).

Business rules applied:
  - Sales Document prefix "4" → purchase order (PO)
  - Sales Document prefix "6" → return
  - Dealer scoping: only rows where Sold-To Party Name exactly matches a
    Dealer Name in dealers.xlsx are Yamaha dealer orders; all others excluded.
    This is the authoritative match — code-based matching is not used because
    non-Yamaha customers also appear in the system with codes outside the
    dealer master.
  - dealer_type: "MC" (motorcycle spare parts) or "OBM" (outboard motor) —
    taken from the Type column in dealers.xlsx.
  - lead_time_days = Goods Issue Date − Created On (in days)
  - lost_qty = Confirmed Quantity − Order Quantity  (negative = short-shipped)
  - Fully rejected line: Confirmed Quantity == 0 → excluded from fill-rate
    analysis, logged to rejection report.

Inputs:
  data/raw/orders.xlsx
  data/raw/dealers.xlsx

Outputs:
  data/interim/orders_clean.parquet
  data/interim/orders_rejection_log.parquet
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

# ── Paths ──────────────────────────────────────────────────────
_ORDERS_RAW      = DATA_RAW / "orders.xlsx"
_DEALERS_RAW     = DATA_RAW / "dealers.xlsx"
_CLEAN_PARQUET   = DATA_INTERIM / "orders_clean.parquet"
_REJECT_PARQUET  = DATA_INTERIM / "orders_rejection_log.parquet"
_OUTPUT_EXCEL    = DATA_OUTPUTS / "stage04_orders_eda.xlsx"


# ══════════════════════════════════════════════════════════════
# 1. Loading & cleaning
# ══════════════════════════════════════════════════════════════

def load_dealers() -> pd.DataFrame:
    """Load dealers.xlsx and return a DataFrame with Dealer Name, Dealer Code, Type.

    Business rule: Dealer Name is the join key for orders (Sold-To Party Name)
    and sales (Payer).  Type distinguishes MC (motorcycle) from OBM (outboard motor).
    """
    df = pd.read_excel(_DEALERS_RAW, dtype=str)
    for col in ["Dealer Name", "Dealer Code", "Type"]:
        df[col] = df[col].fillna("").str.strip()
    df = df[df["Dealer Name"] != ""].copy()
    logger.info(
        f"Dealer master: {len(df)} dealers  "
        f"(MC={( df['Type']=='MC').sum()}, OBM={(df['Type']=='OBM').sum()})"
    )
    return df[["Dealer Name", "Dealer Code", "Type", "Province", "RM", "ASE"]].copy()


def load_orders(dealers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load orders.xlsx, apply business rules, return (clean_df, rejection_log).

    Steps:
      1. Parse date columns.
      2. Classify documents: PO (prefix 4) vs Return (prefix 6).
      3. Scope to Yamaha dealers by matching Sold-To Party Name → Dealer Name.
         Rows with no match are non-Yamaha customers — excluded entirely.
      4. Attach dealer_type (MC / OBM) from dealers.xlsx.
      5. Compute lead_time_days, lost_qty.
      6. Split fully-rejected lines into rejection_log.

    Returns:
        clean_df:       Lines with Confirmed Quantity > 0 (receivable).
        rejection_log:  Lines with Confirmed Quantity == 0 (fully rejected).
    """
    logger.info("Loading orders.xlsx …")
    raw = pd.read_excel(
        _ORDERS_RAW,
        dtype={"Sales Document": str, "Sold-to Party": str, "Material": str},
        parse_dates=["Created On", "Goods Issue Date", "Document Date"],
    )
    logger.info(f"  Raw rows: {len(raw):,}  columns: {raw.shape[1]}")

    # ── Document type classification ────────────────────────────
    raw["doc_type"] = raw["Sales Document"].str[0].map({"4": "PO", "6": "Return"})
    raw = raw[raw["doc_type"].notna()].copy()
    logger.info(f"  After doc-type filter (4/6 only): {len(raw):,}")

    # ── Dealer scoping via name match ───────────────────────────
    # Authoritative match: Sold-To Party Name must equal a Dealer Name in
    # dealers.xlsx.  Code-based matching is not used — non-Yamaha customers
    # have codes in the same numeric space as dealers.
    raw["Sold-To Party Name"] = raw["Sold-To Party Name"].astype(str).str.strip()
    name_to_type = dealers.set_index("Dealer Name")["Type"].to_dict()
    name_to_code = dealers.set_index("Dealer Name")["Dealer Code"].to_dict()

    before = len(raw)
    raw["dealer_type"] = raw["Sold-To Party Name"].map(name_to_type)
    raw["Dealer Code"]  = raw["Sold-To Party Name"].map(name_to_code)
    raw = raw[raw["dealer_type"].notna()].copy()
    logger.info(
        f"  After dealer-name scoping: {len(raw):,} "
        f"(excluded {before - len(raw):,} non-Yamaha lines)"
    )
    mc_lines  = (raw["dealer_type"] == "MC").sum()
    obm_lines = (raw["dealer_type"] == "OBM").sum()
    logger.info(f"    MC lines: {mc_lines:,}  OBM lines: {obm_lines:,}")

    # ── Derived columns ─────────────────────────────────────────
    raw["lead_time_days"] = (raw["Goods Issue Date"] - raw["Created On"]).dt.days
    raw["lost_qty"]       = raw["Confirmed Quantity (Item)"] - raw["Order Quantity (Item)"]
    raw["fill_rate"]      = (
        raw["Confirmed Quantity (Item)"] / raw["Order Quantity (Item)"].replace(0, np.nan)
    ).clip(0, 1)
    raw["Year_Month"]     = raw["Created On"].dt.to_period("M")
    raw["Year_Month_str"] = raw["Created On"].dt.strftime("%Y-%m")

    # ── Split fully rejected ────────────────────────────────────
    rejected_mask   = raw["Confirmed Quantity (Item)"] == 0
    rejection_log   = raw[rejected_mask].copy()
    clean_df        = raw[~rejected_mask].copy()

    logger.info(f"  Clean lines (conf > 0): {len(clean_df):,}")
    logger.info(f"    MC={( clean_df['dealer_type']=='MC').sum():,}  OBM={(clean_df['dealer_type']=='OBM').sum():,}")
    logger.info(f"  Fully rejected lines  : {len(rejection_log):,}")
    return clean_df, rejection_log


# ══════════════════════════════════════════════════════════════
# 2. EDA computations
# ══════════════════════════════════════════════════════════════

def summary_kpis(clean: pd.DataFrame, rejected: pd.DataFrame) -> dict:
    """Compute top-level KPIs for the header sheet."""
    po      = clean[clean["doc_type"] == "PO"]
    returns = clean[clean["doc_type"] == "Return"]
    total_ordered   = po["Order Quantity (Item)"].sum()
    total_confirmed = po["Confirmed Quantity (Item)"].sum()
    period = (
        f"{clean['Created On'].min().date()} → {clean['Created On'].max().date()}"
        if "Created On" in clean.columns and len(clean) > 0
        else "N/A"
    )
    return {
        "Period"               : period,
        "Total Order Lines"    : len(clean),
        "PO Lines"             : len(po),
        "Return Lines"         : len(returns),
        "Fully Rejected Lines" : len(rejected),
        "Unique Dealers"       : clean["Sold-to Party"].nunique(),
        "Unique Materials"     : clean["Material"].nunique(),
        f"Total PO Value ({CURRENCY})": round(po["Net Value (Item)"].sum()),
        "Overall Fill Rate %"  : round(total_confirmed / max(total_ordered, 1) * 100, 2),
        "Median Lead Time (days)": float(clean["lead_time_days"].median()),
        "Avg Lead Time (days)" : round(float(clean["lead_time_days"].mean()), 1),
    }


def monthly_trend(clean: pd.DataFrame) -> pd.DataFrame:
    """Monthly order volume, value, returns, and fill rate."""
    po  = clean[clean["doc_type"] == "PO"]
    ret = clean[clean["doc_type"] == "Return"]

    po_monthly = (
        po.groupby("Year_Month_str").agg(
            po_lines      = ("Sales Document", "count"),
            po_qty        = ("Order Quantity (Item)", "sum"),
            confirmed_qty = ("Confirmed Quantity (Item)", "sum"),
            po_value      = ("Net Value (Item)", "sum"),
        )
        .reset_index()
    )
    po_monthly["fill_rate_%"] = (
        po_monthly["confirmed_qty"] / po_monthly["po_qty"].replace(0, np.nan) * 100
    ).round(2)

    ret_monthly = (
        ret.groupby("Year_Month_str")
        .agg(return_lines=("Sales Document", "count"), return_value=("Net Value (Item)", "sum"))
        .reset_index()
    )

    monthly = po_monthly.merge(ret_monthly, on="Year_Month_str", how="left").fillna(0)
    monthly.rename(columns={"Year_Month_str": "Month"}, inplace=True)
    return monthly.sort_values("Month").reset_index(drop=True)


def lead_time_analysis(clean: pd.DataFrame) -> pd.DataFrame:
    """Lead time percentile distribution by month."""
    po = clean[(clean["doc_type"] == "PO") & (clean["lead_time_days"] >= 0)]
    pct = (
        po.groupby("Year_Month_str")["lead_time_days"]
        .describe(percentiles=[0.25, 0.5, 0.75, 0.90, 0.95])
        .round(1)
        .reset_index()
        .rename(columns={"Year_Month_str": "Month"})
    )
    return pct.sort_values("Month").reset_index(drop=True)


def short_ship_by_material(clean: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    """Top materials by short-shipped quantity (lost_qty most negative)."""
    po = clean[(clean["doc_type"] == "PO") & (clean["lost_qty"] < 0)]
    agg = (
        po.groupby(["Material", "Material Description"])
        .agg(
            total_ordered   = ("Order Quantity (Item)", "sum"),
            total_confirmed = ("Confirmed Quantity (Item)", "sum"),
            total_lost      = ("lost_qty", "sum"),
            occurrences     = ("lost_qty", "count"),
        )
        .reset_index()
    )
    agg["fill_rate_%"] = (agg["total_confirmed"] / agg["total_ordered"].replace(0, np.nan) * 100).round(2)
    agg["total_lost"]  = agg["total_lost"].abs()
    return agg.sort_values("total_lost", ascending=False).head(top_n).reset_index(drop=True)


def fill_rate_by_dealer(clean: pd.DataFrame, dealer_names: pd.DataFrame) -> pd.DataFrame:
    """Fill rate and order stats per dealer (grouped by Sold-To Party Name)."""
    po = clean[clean["doc_type"] == "PO"]
    agg = (
        po.groupby("Sold-To Party Name").agg(
            dealer_code     = ("Dealer Code", "first"),
            dealer_type     = ("dealer_type", "first"),
            po_lines        = ("Sales Document", "count"),
            total_ordered   = ("Order Quantity (Item)", "sum"),
            total_confirmed = ("Confirmed Quantity (Item)", "sum"),
            total_value     = ("Net Value (Item)", "sum"),
        )
        .reset_index()
        .rename(columns={"Sold-To Party Name": "Dealer Name"})
    )
    agg["fill_rate_%"] = (agg["total_confirmed"] / agg["total_ordered"].replace(0, np.nan) * 100).round(2)
    return agg.sort_values("total_value", ascending=False).reset_index(drop=True)


def returns_analysis(clean: pd.DataFrame) -> pd.DataFrame:
    """Return lines grouped by reason and dealer."""
    returns = clean[clean["doc_type"] == "Return"]
    agg = (
        returns.groupby(["Sold-To Party Name", "Order Reason Description"])
        .agg(
            return_lines = ("Sales Document", "count"),
            return_qty   = ("Order Quantity (Item)", "sum"),
            return_value = ("Net Value (Item)", "sum"),
        )
        .reset_index()
    )
    return agg.sort_values("return_value", ascending=False).reset_index(drop=True)


def top_materials_by_value(clean: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """Top N materials by total confirmed value."""
    po = clean[clean["doc_type"] == "PO"]
    agg = (
        po.groupby(["Material", "Material Description"]).agg(
            total_lines     = ("Sales Document", "count"),
            total_qty       = ("Confirmed Quantity (Item)", "sum"),
            total_value     = ("Net Value (Item)", "sum"),
        )
        .reset_index()
    )
    agg["value_share_%"] = (agg["total_value"] / agg["total_value"].sum() * 100).round(2)
    return agg.sort_values("total_value", ascending=False).head(top_n).reset_index(drop=True)


def orders_received_analysis(clean: pd.DataFrame, rejected: pd.DataFrame) -> dict[str, int]:
    """Breakdown of PO *documents* by fulfillment status.

    Combines clean lines (Confirmed > 0) and fully-rejected lines (Confirmed = 0)
    to classify each unique Sales Document:
      - fully_filled:  ALL lines have Confirmed Qty >= Order Qty
      - partial_fill:  at least one line is short or zero, but not all zero
      - complete_zero: ALL lines have Confirmed Qty = 0 (entire PO rejected)

    Business meaning: document-level view tells ops how many orders were
    completely blocked vs partially fulfilled vs cleanly received.
    """
    all_po = pd.concat(
        [clean[clean["doc_type"] == "PO"], rejected[rejected["doc_type"] == "PO"]],
        ignore_index=True,
    )
    if all_po.empty:
        return {"total_documents": 0, "fully_filled": 0, "partial_fill": 0, "complete_zero": 0}

    doc_status = all_po.groupby("Sales Document").apply(
        lambda g: (
            "complete_zero" if (g["Confirmed Quantity (Item)"] == 0).all()
            else "fully_filled" if (g["Confirmed Quantity (Item)"] >= g["Order Quantity (Item)"]).all()
            else "partial_fill"
        )
    )
    counts = doc_status.value_counts()
    return {
        "total_documents": int(len(doc_status)),
        "fully_filled":    int(counts.get("fully_filled",  0)),
        "partial_fill":    int(counts.get("partial_fill",  0)),
        "complete_zero":   int(counts.get("complete_zero", 0)),
    }


def rejection_reasons_summary(rejected: pd.DataFrame) -> pd.DataFrame:
    """Fully-rejected PO lines grouped by rejection reason with counts and share."""
    po_rej = rejected[rejected["doc_type"] == "PO"] if "doc_type" in rejected.columns else rejected
    reason_col = "Reason for Rejection"
    if po_rej.empty or reason_col not in po_rej.columns:
        return pd.DataFrame(columns=[reason_col, "rejected_lines", "rejected_qty", "share_%"])
    agg = (
        po_rej.groupby(reason_col)
        .agg(
            rejected_lines = ("Sales Document", "count"),
            rejected_qty   = ("Order Quantity (Item)", "sum"),
        )
        .reset_index()
    )
    agg["share_%"] = (agg["rejected_lines"] / agg["rejected_lines"].sum() * 100).round(2)
    return agg.sort_values("rejected_lines", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 3. Excel report
# ══════════════════════════════════════════════════════════════

def _wb_fmts(wb: xlsxwriter.Workbook) -> dict:
    return {
        "title":   wb.add_format({"bold": True, "font_size": 14, "font_color": "#003087"}),
        "sub":     wb.add_format({"italic": True, "font_color": "#555555"}),
        "hdr":     wb.add_format({"bold": True, "bg_color": "#003087", "font_color": "white", "border": 1, "align": "center"}),
        "hdr_red": wb.add_format({"bold": True, "bg_color": "#B71C1C", "font_color": "white", "border": 1, "align": "center"}),
        "num":     wb.add_format({"num_format": "#,##0",    "border": 1}),
        "num2":    wb.add_format({"num_format": "#,##0.00", "border": 1}),
        "pct":     wb.add_format({"num_format": "0.00",     "border": 1}),
        "cell":    wb.add_format({"border": 1}),
        "warn":    wb.add_format({"bold": True, "font_color": "#B71C1C", "border": 1}),
        "ok":      wb.add_format({"font_color": "#1B7E24", "border": 1}),
        "label":   wb.add_format({"bold": True, "bg_color": "#E3F2FD", "border": 1}),
    }


def _write_kv(ws: Any, row: int, col: int, key: str, val: Any, fmts: dict) -> None:
    ws.write(row, col,     key, fmts["label"])
    if isinstance(val, float):
        ws.write(row, col + 1, val, fmts["num2"])
    elif isinstance(val, int):
        ws.write(row, col + 1, val, fmts["num"])
    else:
        ws.write(row, col + 1, str(val), fmts["cell"])


def _write_df(ws: Any, df: pd.DataFrame, fmts: dict, hdr_fmt: str = "hdr", row0: int = 0) -> None:
    for c, col in enumerate(df.columns):
        ws.write(row0, c, col, fmts[hdr_fmt])
    for r, (_, row) in enumerate(df.iterrows()):
        for c, val in enumerate(row):
            if isinstance(val, (int, np.integer)):
                ws.write(row0 + r + 1, c, int(val), fmts["num"])
            elif isinstance(val, (float, np.floating)):
                ws.write(row0 + r + 1, c, round(float(val), 2), fmts["num2"])
            else:
                ws.write(row0 + r + 1, c, str(val) if pd.notna(val) else "", fmts["cell"])


def write_excel_report(
    kpis:              dict,
    monthly:           pd.DataFrame,
    lt_df:             pd.DataFrame,
    shortship:         pd.DataFrame,
    dealer_fr:         pd.DataFrame,
    returns_df:        pd.DataFrame,
    top_materials:     pd.DataFrame,
    rejection_log:     pd.DataFrame,
    orders_received:   dict[str, int] | None = None,
    rejection_reasons: pd.DataFrame | None   = None,
) -> None:
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb   = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))
    fmts = _wb_fmts(wb)

    # ── Sheet 1: Summary KPIs ─────────────────────────────────
    ws = wb.add_worksheet("Summary")
    ws.set_column("A:A", 30)
    ws.set_column("B:B", 28)
    ws.write("A1", "Stage 4 — Orders EDA: Spare Parts Purchase Orders", fmts["title"])
    ws.write("A2", "Domestic warehouse-to-dealer orders (not India import lead time)", fmts["sub"])
    for r, (k, v) in enumerate(kpis.items()):
        _write_kv(ws, r + 4, 0, k, v, fmts)

    # ── Sheet 2: Monthly Trend ─────────────────────────────────
    ws = wb.add_worksheet("Monthly Trend")
    ws.set_column("A:A", 12)
    ws.set_column("B:I", 18)
    ws.write("A1", "Monthly Order Trend", fmts["title"])
    _write_df(ws, monthly, fmts, row0=2)

    n = len(monthly)
    chart = wb.add_chart({"type": "column"})
    chart.add_series({
        "name":       "PO Lines",
        "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
        "values":     ["Monthly Trend", 3, 1, 2 + n, 1],
        "fill":       {"color": "#003087"},
    })
    chart2 = wb.add_chart({"type": "line"})
    chart2.add_series({
        "name":       "Fill Rate %",
        "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
        "values":     ["Monthly Trend", 3, 5, 2 + n, 5],
        "line":       {"color": "#FF6F00", "width": 2},
        "y2_axis":    True,
    })
    chart.combine(chart2)
    chart.set_title({"name": "Monthly PO Lines & Fill Rate"})
    chart.set_y_axis({"name": "Order Lines"})
    chart.set_y2_axis({"name": "Fill Rate %", "min": 0, "max": 100})
    chart.set_size({"width": 720, "height": 340})
    ws.insert_chart("K3", chart)

    # ── Sheet 3: Lead Time ────────────────────────────────────
    ws = wb.add_worksheet("Lead Time")
    ws.set_column("A:I", 14)
    ws.write("A1", "Lead Time (days): Goods Issue Date − Created On", fmts["title"])
    ws.write("A2", "Domestic warehouse → dealer; NOT the 3-month India import lead time", fmts["sub"])
    _write_df(ws, lt_df, fmts, row0=3)

    # ── Sheet 4: Short-Ship Analysis ──────────────────────────
    ws = wb.add_worksheet("Short-Ship")
    ws.set_column("A:A", 22)
    ws.set_column("B:B", 45)
    ws.set_column("C:G", 16)
    ws.write("A1", f"Top {len(shortship)} Most Short-Shipped Materials", fmts["title"])
    ws.write("A2", "lost_qty = |Confirmed − Ordered|  (lines where Confirmed < Ordered)", fmts["sub"])
    _write_df(ws, shortship, fmts, row0=3)

    # ── Sheet 5: Fill Rate by Dealer ──────────────────────────
    ws = wb.add_worksheet("Fill Rate by Dealer")
    ws.set_column("A:A", 12)
    ws.set_column("B:B", 35)
    ws.set_column("C:G", 16)
    ws.write("A1", "Fill Rate & Order Volume by Dealer", fmts["title"])
    _write_df(ws, dealer_fr, fmts, row0=2)

    # ── Sheet 6: Returns ──────────────────────────────────────
    ws = wb.add_worksheet("Returns")
    ws.set_column("A:A", 35)
    ws.set_column("B:B", 35)
    ws.set_column("C:E", 16)
    ws.write("A1", "Return Orders (Sales Document prefix 6)", fmts["title"])
    ws.write("A2", "Grouped by dealer and return reason", fmts["sub"])
    _write_df(ws, returns_df, fmts, row0=3)

    # ── Sheet 7: Top Materials ────────────────────────────────
    ws = wb.add_worksheet("Top Materials")
    ws.set_column("A:A", 22)
    ws.set_column("B:B", 50)
    ws.set_column("C:F", 16)
    ws.write("A1", "Top Materials by Confirmed PO Value", fmts["title"])
    _write_df(ws, top_materials, fmts, row0=2)

    # ── Sheet 8: Rejection Log ────────────────────────────────
    ws = wb.add_worksheet("Rejection Log")
    ws.set_column("A:A", 16)
    ws.set_column("B:B", 22)
    ws.set_column("C:C", 45)
    ws.set_column("D:H", 16)
    ws.write("A1", "Fully Rejected Lines (Confirmed Quantity = 0)", fmts["title"])
    ws.write("A2", "These lines are excluded from fill-rate and demand analysis.", fmts["sub"])

    rej_cols = [
        "Sales Document", "Sold-to Party", "Sold-To Party Name",
        "Material", "Material Description",
        "Order Quantity (Item)", "Year_Month_str",
        "Reason for Rejection",
    ]
    rej_display = rejection_log[[c for c in rej_cols if c in rejection_log.columns]].copy()
    _write_df(ws, rej_display, fmts, hdr_fmt="hdr_red", row0=3)

    # ── Sheet 9: Orders Received Breakdown ────────────────────
    if orders_received:
        ws = wb.add_worksheet("Orders Received")
        ws.set_column("A:A", 42)
        ws.set_column("B:B", 18)
        ws.write("A1", "Order Fulfillment Breakdown — by Purchase Order Document", fmts["title"])
        ws.write("A2", "Each Sales Document classified by the fill status of ALL its lines combined.", fmts["sub"])
        labels = {
            "total_documents": "Total PO Documents",
            "fully_filled":    "Fully Filled (all lines confirmed ≥ ordered)",
            "partial_fill":    "Partially Filled (some lines short or zero)",
            "complete_zero":   "Complete Zero (all lines confirmed = 0)",
        }
        for r, (key, lbl) in enumerate(labels.items()):
            _write_kv(ws, r + 4, 0, lbl, orders_received.get(key, 0), fmts)
        # Derived fulfillment rate
        total = orders_received.get("total_documents", 1) or 1
        filled = orders_received.get("fully_filled", 0)
        _write_kv(ws, len(labels) + 4, 0, "Fulfillment Rate (fully filled) %",
                  round(filled / total * 100, 2), fmts)

    # ── Sheet 10: Rejection Reasons ───────────────────────────
    if rejection_reasons is not None and not rejection_reasons.empty:
        ws = wb.add_worksheet("Rejection Reasons")
        ws.set_column("A:A", 50)
        ws.set_column("B:D", 18)
        ws.write("A1", "Rejection Reasons — Fully-Rejected PO Lines Grouped by Reason", fmts["title"])
        _write_df(ws, rejection_reasons, fmts, hdr_fmt="hdr_red", row0=3)

    wb.close()
    logger.info(f"Excel report written: {_OUTPUT_EXCEL}")


# ══════════════════════════════════════════════════════════════
# 4. Main entry point
# ══════════════════════════════════════════════════════════════

def run(refresh: bool = False) -> None:
    """Execute Stage 4: Orders EDA."""
    if not refresh and _CLEAN_PARQUET.exists():
        logger.info("Orders clean parquet exists. Pass refresh=True to recompute.")
        return

    logger.info("=" * 55)
    logger.info("STAGE 4 — ORDERS EDA")
    logger.info("=" * 55)

    dealer_master = load_dealers()
    clean, rejected = load_orders(dealer_master)

    # Save parquets
    _CLEAN_PARQUET.parent.mkdir(parents=True, exist_ok=True)

    # Coerce object columns before saving
    for col in clean.select_dtypes(include="object").columns:
        clean[col] = clean[col].astype(str).replace("nan", pd.NA)
    for col in rejected.select_dtypes(include="object").columns:
        rejected[col] = rejected[col].astype(str).replace("nan", pd.NA)

    # Convert Period columns to string
    if "Year_Month" in clean.columns:
        clean["Year_Month"] = clean["Year_Month"].astype(str)
    if "Year_Month" in rejected.columns:
        rejected["Year_Month"] = rejected["Year_Month"].astype(str)

    clean.to_parquet(_CLEAN_PARQUET, index=False)
    rejected.to_parquet(_REJECT_PARQUET, index=False)
    logger.info(f"Parquets saved: {_CLEAN_PARQUET.name}, {_REJECT_PARQUET.name}")

    # EDA
    kpis          = summary_kpis(clean, rejected)
    monthly       = monthly_trend(clean)
    lt_df         = lead_time_analysis(clean)
    shortship     = short_ship_by_material(clean)
    dealer_fr     = fill_rate_by_dealer(clean, dealer_master)
    returns_df    = returns_analysis(clean)
    top_mats      = top_materials_by_value(clean)
    ord_received  = orders_received_analysis(clean, rejected)
    rej_reasons   = rejection_reasons_summary(rejected)

    # Merge document-level breakdown into KPIs for the Summary sheet
    kpis["Total PO Documents"]        = ord_received["total_documents"]
    kpis["Fully Filled Orders"]       = ord_received["fully_filled"]
    kpis["Partially Filled Orders"]   = ord_received["partial_fill"]
    kpis["Complete Zero Orders"]      = ord_received["complete_zero"]

    write_excel_report(
        kpis, monthly, lt_df, shortship, dealer_fr, returns_df, top_mats, rejected,
        orders_received=ord_received, rejection_reasons=rej_reasons,
    )

    # Summary log
    logger.info("=" * 55)
    logger.info("STAGE 4 SUMMARY")
    logger.info("=" * 55)
    for k, v in kpis.items():
        logger.info(f"  {k:<35}: {v}")

    logger.info("  Top 5 short-shipped materials:")
    for _, row in shortship.head(5).iterrows():
        logger.info(f"    {row['Material']:<22} {row['Material Description'][:35]:<35}  lost: {int(row['total_lost']):,}")

    logger.info("  Monthly fill rate range: "
                f"{monthly['fill_rate_%'].min():.1f}% – {monthly['fill_rate_%'].max():.1f}%")
    logger.info(
        f"  Order fulfillment: {ord_received['fully_filled']} fully-filled / "
        f"{ord_received['partial_fill']} partial / "
        f"{ord_received['complete_zero']} complete-zero  "
        f"(of {ord_received['total_documents']} PO documents)"
    )
    logger.info(f"  Rejection reasons: {len(rej_reasons)} distinct reasons")
    logger.info("Stage 4 complete.")
