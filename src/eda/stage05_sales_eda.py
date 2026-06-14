"""Stage 5: Spare Parts Sales EDA + Dealer Churn Analysis.

Two channels are analysed:
  - Dealer channel  : sales.xlsx  (Seeduwa PDC warehouse → dealers)
  - Service channel : service.xlsx (Yamaha service centers — Union Place, Matara)

Product categories (from Matl Group prefix):
  AWPYM → Yamaha Spare Parts   (primary spare-parts business)
  AWPKT → Tyres                (Katana tyre range)
  AWPOB → Marine/Outboard Parts
  AWPSU → Suzuki Spare Parts
  AWPMA → Accessories & Filters
  AWLCA, AWLOT, AWLMC → Lubricants
  Other prefixes → Other

Data quality note:
  Lubricant Net Sales total is NEGATIVE (bulk reseller accounting treatment —
  large-volume transfers to distributors like CEB are recorded as cost flows
  rather than revenue). Lubricants are shown as a separate informational row
  and EXCLUDED from gross revenue totals to avoid distorting the picture.

Bill-type classification:
  Positive billings : F2, ZVAT, S1, S2, ZFOC  → sales
  Return billings   : RE, ZRVT, ZSVT, ZRE, ZRSV → returns

Dealer churn definition (rolling from last data date):
  Active   : ≤90 days since last order
  At-Risk  : 91–180 days
  Dormant  : 181–365 days
  Churned  : >365 days

Inputs:
  data/raw/sales.xlsx
  data/raw/service.xlsx

Outputs:
  data/interim/sales_clean.parquet
  data/outputs/stage05_sales_eda.xlsx
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
_SALES_RAW    = DATA_RAW / "sales.xlsx"
_SERVICE_RAW  = DATA_RAW / "service.xlsx"
_DEALERS_RAW  = DATA_RAW / "dealers.xlsx"
_CLEAN_PARQ   = DATA_INTERIM / "sales_clean.parquet"
_OUTPUT_EXCEL = DATA_OUTPUTS / "stage05_sales_eda.xlsx"

# ── Product category mapping (Matl Group prefix → label) ───────
# AWPSU (Suzuki spare parts) is intentionally excluded — mapped to Other.
# AWPMA mixes genuine accessories/filters with Suzuki Alto body parts;
# see "Accessories Sample" sheet in the report for the full breakdown.
_CATEGORY_MAP: dict[str, str] = {
    "AWPYM": "Yamaha Spare Parts",
    "AWPKT": "Tyres",
    "AWPOB": "Marine / Outboard Parts",
    "AWPMA": "Accessories & Filters",
    "AWLCA": "Lubricants",
    "AWLOT": "Lubricants",
    "AWLMC": "Lubricants",
}
_LUBRICANT_CATEGORIES = {"Lubricants"}  # excluded from revenue totals (accounting anomaly)

# ── Bill type sets ──────────────────────────────────────────────
_POS_BILL_TYPES = {"F2", "ZVAT", "S1", "S2", "ZFOC"}
_RET_BILL_TYPES = {"RE", "ZRVT", "ZSVT", "ZRE", "ZRSV"}

# ── Churn thresholds ────────────────────────────────────────────
_CHURN_ACTIVE_DAYS  =  90
_CHURN_AT_RISK_DAYS = 180
_CHURN_DORMANT_DAYS = 365


def _assign_category(matl_group: str) -> str:
    """Map a Matl Group code to its product category."""
    prefix = str(matl_group)[:5]
    return _CATEGORY_MAP.get(prefix, "Other")


# ══════════════════════════════════════════════════════════════
# 1. Loading & cleaning
# ══════════════════════════════════════════════════════════════

def _read_billing_file(path: Any, channel: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    df["Billing Date"] = pd.to_datetime(df["Billing Date"], dayfirst=True, errors="coerce")
    df["Net Sales"]    = pd.to_numeric(df["Net Sales"],    errors="coerce").fillna(0.0)
    df["SlsVolQty"]    = pd.to_numeric(df["SlsVolQty"],    errors="coerce").fillna(0.0)
    df["channel"]      = channel
    for col in ["Payer", "Material", "Matl Group", "Bill. Type"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def load_dealer_map() -> pd.DataFrame:
    """Load dealers.xlsx; return DataFrame with Dealer Name and Type.

    Business rule: Payer in sales.xlsx must exactly match Dealer Name here.
    Type column distinguishes MC (motorcycle) from OBM (outboard motor parts).
    """
    df = pd.read_excel(_DEALERS_RAW, dtype=str)
    for col in ["Dealer Name", "Type"]:
        df[col] = df[col].fillna("").str.strip()
    df = df[df["Dealer Name"] != ""].copy()
    logger.info(
        f"Dealer master: {len(df)} dealers  "
        f"(MC={(df['Type']=='MC').sum()}, OBM={(df['Type']=='OBM').sum()})"
    )
    return df[["Dealer Name", "Type"]].drop_duplicates("Dealer Name")


def load_and_clean(refresh: bool = False) -> pd.DataFrame:
    """Load both billing files, scope to registered Yamaha dealers, tag dealer_type.

    Scoping rule: Payer must exactly match a Dealer Name in dealers.xlsx.
    Unmatched payers (bulk lubricant distributors, institutions, etc.) are excluded.
    dealer_type = 'MC' or 'OBM' from the Type column in dealers.xlsx.

    Returns a combined clean DataFrame covering ALL product categories.
    Use 'product_category' and 'dealer_type' columns to filter for analyses.
    """
    if not refresh and _CLEAN_PARQ.exists():
        logger.info("Loading cached sales_clean.parquet")
        return pd.read_parquet(_CLEAN_PARQ)

    dealer_df = load_dealer_map()
    name_to_type = dealer_df.set_index("Dealer Name")["Type"].to_dict()

    sales   = _read_billing_file(_SALES_RAW,   "Dealer")
    service = _read_billing_file(_SERVICE_RAW, "Service")
    logger.info(f"Loaded: sales={len(sales):,}  service={len(service):,}")

    combined = pd.concat([sales, service], ignore_index=True)
    combined = combined[combined["Billing Date"].notna()].copy()

    # ── Dealer scoping: Payer must be a registered Yamaha dealer name ──
    before = len(combined)
    combined["dealer_type"] = combined["Payer"].map(name_to_type)
    combined = combined[combined["dealer_type"].notna()].copy()
    excluded = before - len(combined)
    logger.info(
        f"Dealer-name scoping: {len(combined):,} rows kept, "
        f"{excluded:,} excluded (non-Yamaha payers)"
    )
    mc_rows  = (combined["dealer_type"] == "MC").sum()
    obm_rows = (combined["dealer_type"] == "OBM").sum()
    logger.info(f"  MC rows: {mc_rows:,}  OBM rows: {obm_rows:,}")

    # ── Product category ────────────────────────────────────────
    combined["product_category"] = combined["Matl Group"].apply(_assign_category)

    # ── Bill type classification ────────────────────────────────
    combined["bill_class"] = combined["Bill. Type"].apply(
        lambda t: "sale" if t in _POS_BILL_TYPES else ("return" if t in _RET_BILL_TYPES else "other")
    )

    combined["Year_Month_str"] = combined["Billing Date"].dt.strftime("%Y-%m")

    # ── Category summary at load time ───────────────────────────
    logger.info("Product category breakdown (positive billings only, MC+OBM combined):")
    pos = combined[combined["bill_class"] == "sale"]
    for cat, grp in pos.groupby("product_category"):
        note = "  ⚠ accounting anomaly — excluded from revenue KPIs" if cat in _LUBRICANT_CATEGORIES else ""
        logger.info(f"  {cat:<30}: {len(grp):>7,} lines  LKR {grp['Net Sales'].sum():>16,.0f}{note}")

    # ── Save parquet ────────────────────────────────────────────
    keep_cols = [
        "channel", "product_category", "dealer_type",
        "Payer", "Material", "Matl Group",
        "Billing Date", "Year_Month_str", "SlsVolQty", "Net Sales",
        "bill_class", "Bill. Type", "Sales Office",
    ]
    if "Material Description" in combined.columns:
        keep_cols.insert(5, "Material Description")
    keep_cols = [c for c in keep_cols if c in combined.columns]
    out = combined[keep_cols].copy()

    for col in out.select_dtypes(include="object").columns:
        out[col] = out[col].astype(str).replace("nan", pd.NA)

    _CLEAN_PARQ.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(_CLEAN_PARQ, index=False)
    logger.info(f"Saved: {_CLEAN_PARQ}")
    return out


# ══════════════════════════════════════════════════════════════
# 2. EDA computations
# ══════════════════════════════════════════════════════════════

def _revenue_df(df: pd.DataFrame, exclude_lubes: bool = True) -> pd.DataFrame:
    """Return sale rows, optionally excluding lubricant accounting anomaly."""
    sales = df[df["bill_class"] == "sale"]
    if exclude_lubes:
        sales = sales[~sales["product_category"].isin(_LUBRICANT_CATEGORIES)]
    return sales


def summary_kpis(df: pd.DataFrame) -> dict:
    """Top-level KPIs — revenue excludes lubricants (accounting anomaly)."""
    sales   = _revenue_df(df, exclude_lubes=True)
    returns = df[(df["bill_class"] == "return") & ~df["product_category"].isin(_LUBRICANT_CATEGORIES)]
    all_sales = df[df["bill_class"] == "sale"]
    lube_lines = all_sales[all_sales["product_category"].isin(_LUBRICANT_CATEGORIES)]
    return {
        "Period"                              : f"{df['Billing Date'].min().date()} → {df['Billing Date'].max().date()}",
        "Total Sale Lines (all categories)"   : len(all_sales),
        "Sale Lines excl. Lubricants"         : len(sales),
        "Return Lines excl. Lubricants"       : len(returns),
        "Lubricant Lines (excl. from revenue)": len(lube_lines),
        "Unique Payers / Dealers"             : df["Payer"].nunique(),
        "Unique Materials (all)"              : df["Material"].nunique(),
        f"Gross Sales excl. Lubricants ({CURRENCY})": round(sales["Net Sales"].sum()),
        f"Returns ({CURRENCY})"               : round(returns["Net Sales"].sum()),
        f"Net Revenue ({CURRENCY})"           : round(sales["Net Sales"].sum() + returns["Net Sales"].sum()),
        "Return Rate %"                       : round(
            abs(returns["Net Sales"].sum()) / max(sales["Net Sales"].sum(), 1) * 100, 2),
        "Dealer Channel Lines"                : int((df["channel"] == "Dealer").sum()),
        "Service Channel Lines"               : int((df["channel"] == "Service").sum()),
        "⚠ Lubricant Net Sales (anomalous)"   : round(lube_lines["Net Sales"].sum()),
    }


def category_mix(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue by product category (lubricants shown separately with anomaly flag)."""
    all_sales = df[df["bill_class"] == "sale"]
    agg = (
        all_sales.groupby("product_category")
        .agg(sale_lines=("Net Sales", "count"),
             gross_sales=("Net Sales", "sum"),
             unique_materials=("Material", "nunique"),
             unique_payers=("Payer", "nunique"))
        .reset_index()
    )
    # Share excludes lubricants from denominator (anomaly)
    revenue_base = agg.loc[~agg["product_category"].isin(_LUBRICANT_CATEGORIES), "gross_sales"].sum()
    agg["value_share_%"] = agg["gross_sales"].apply(
        lambda v: round(v / max(revenue_base, 1) * 100, 2)
    )
    agg["data_note"] = agg["product_category"].apply(
        lambda c: "⚠ Negative total — bulk-reseller accounting anomaly. Excluded from revenue KPIs." if c in _LUBRICANT_CATEGORIES else ""
    )
    return agg.sort_values("gross_sales", ascending=False).reset_index(drop=True)


def monthly_revenue_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly gross sales and returns by channel (lubricants excluded)."""
    sales = _revenue_df(df, exclude_lubes=True)
    rets  = df[(df["bill_class"] == "return") & ~df["product_category"].isin(_LUBRICANT_CATEGORIES)]

    s_mo = (
        sales.groupby(["Year_Month_str", "channel"])
        .agg(sale_lines=("Net Sales", "count"), gross_sales=("Net Sales", "sum"))
        .reset_index()
    )
    r_mo = (
        rets.groupby(["Year_Month_str", "channel"])
        .agg(return_lines=("Net Sales", "count"), returns=("Net Sales", "sum"))
        .reset_index()
    )
    mo = s_mo.merge(r_mo, on=["Year_Month_str", "channel"], how="left").fillna(0)
    mo["net_revenue"]   = mo["gross_sales"] + mo["returns"]
    mo["return_rate_%"] = (mo["returns"].abs() / mo["gross_sales"].replace(0, np.nan) * 100).round(2).fillna(0)
    mo.rename(columns={"Year_Month_str": "Month"}, inplace=True)
    return mo.sort_values(["Month", "channel"]).reset_index(drop=True)


def monthly_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly gross sales split by product category (all categories, lubricants flagged)."""
    all_sales = df[df["bill_class"] == "sale"]
    mo = (
        all_sales.groupby(["Year_Month_str", "product_category"])
        .agg(gross_sales=("Net Sales", "sum"), lines=("Net Sales", "count"))
        .reset_index()
        .rename(columns={"Year_Month_str": "Month"})
    )
    return mo.sort_values(["Month", "product_category"]).reset_index(drop=True)


def top_dealers(df: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    """Top N dealers by gross sales (lubricants excluded)."""
    sales   = _revenue_df(df, exclude_lubes=True)
    returns = df[(df["bill_class"] == "return") & ~df["product_category"].isin(_LUBRICANT_CATEGORIES)]

    s_agg = (
        sales.groupby("Payer")
        .agg(gross_sales=("Net Sales", "sum"), sale_lines=("Net Sales", "count"),
             last_order=("Billing Date", "max"))
        .reset_index()
    )
    r_agg = (
        returns.groupby("Payer")
        .agg(return_value=("Net Sales", "sum"), return_lines=("Net Sales", "count"))
        .reset_index()
    )
    merged = s_agg.merge(r_agg, on="Payer", how="left").fillna(0)
    merged["net_revenue"]   = merged["gross_sales"] + merged["return_value"]
    merged["return_rate_%"] = (merged["return_value"].abs() / merged["gross_sales"].replace(0, np.nan) * 100).round(2).fillna(0)
    return merged.sort_values("gross_sales", ascending=False).head(top_n).reset_index(drop=True)


def top_materials(df: pd.DataFrame, category: str = "Yamaha Spare Parts", top_n: int = 50) -> pd.DataFrame:
    """Top N materials by gross sales for a given product category."""
    sales = df[(df["bill_class"] == "sale") & (df["product_category"] == category)]
    mat_col = "Material Description" if "Material Description" in df.columns else "Material"
    agg = (
        sales.groupby("Material")
        .agg(
            description  = (mat_col, "first"),
            matl_group   = ("Matl Group", "first"),
            total_qty    = ("SlsVolQty", "sum"),
            gross_sales  = ("Net Sales", "sum"),
            sale_lines   = ("Net Sales", "count"),
        )
        .reset_index()
    )
    agg["value_share_%"] = (agg["gross_sales"] / agg["gross_sales"].sum() * 100).round(3)
    return agg.sort_values("gross_sales", ascending=False).head(top_n).reset_index(drop=True)


def accessories_sample(df: pd.DataFrame, top_n: int = 40) -> pd.DataFrame:
    """Top materials in the Accessories & Filters category with sub-group label.

    Business meaning: AWPMA is a mixed bag — oil filters and Yamaha motorcycle
    parts sit alongside Suzuki Alto body panels.  This table surfaces the full
    contents so the category can be re-scoped or renamed if needed.
    """
    sales = df[(df["bill_class"] == "sale") & (df["product_category"] == "Accessories & Filters")]
    mat_col = "Material Description" if "Material Description" in df.columns else "Material"
    agg = (
        sales.groupby(["Matl Group", "Material"])
        .agg(
            description = (mat_col, "first"),
            total_qty   = ("SlsVolQty",  "sum"),
            gross_sales = ("Net Sales",  "sum"),
            lines       = ("Net Sales",  "count"),
        )
        .reset_index()
        .sort_values("gross_sales", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    total = agg["gross_sales"].sum()
    agg["value_share_%"] = (agg["gross_sales"] / max(total, 1) * 100).round(2)
    return agg


def dealer_churn_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Classify each dealer by last-activity recency (all categories).

    Business meaning: identify dealers at risk of disengagement so the sales
    team can intervene before they stop stocking Yamaha parts altogether.
    """
    reference_date = df["Billing Date"].max()
    sales = _revenue_df(df, exclude_lubes=True)

    agg = (
        sales.groupby("Payer")
        .agg(
            first_order  = ("Billing Date", "min"),
            last_order   = ("Billing Date", "max"),
            total_orders = ("Net Sales",    "count"),
            total_value  = ("Net Sales",    "sum"),
        )
        .reset_index()
    )
    agg["days_since_last_order"] = (reference_date - agg["last_order"]).dt.days

    def _classify(days: float) -> str:
        if days <= _CHURN_ACTIVE_DAYS:
            return "Active"
        elif days <= _CHURN_AT_RISK_DAYS:
            return "At-Risk"
        elif days <= _CHURN_DORMANT_DAYS:
            return "Dormant"
        return "Churned"

    agg["status"] = agg["days_since_last_order"].apply(_classify)
    agg["last_order_date"] = agg["last_order"].dt.date
    return agg.sort_values("days_since_last_order").reset_index(drop=True)


def channel_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Side-by-side KPIs for Dealer vs Service channels (lubricants excluded)."""
    sales = _revenue_df(df, exclude_lubes=True)
    rows = []
    for ch in ["Dealer", "Service"]:
        ch_df = sales[sales["channel"] == ch]
        rows.append({
            "Channel"                         : ch,
            "Sale Lines"                      : len(ch_df),
            "Unique Payers"                   : ch_df["Payer"].nunique(),
            "Unique Materials"                : ch_df["Material"].nunique(),
            f"Gross Sales ({CURRENCY})"       : round(ch_df["Net Sales"].sum()),
            "Avg Line Value (LKR)"            : round(ch_df["Net Sales"].mean(), 2) if len(ch_df) else 0,
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════
# 3. Excel report
# ══════════════════════════════════════════════════════════════

def _wb_fmts(wb: xlsxwriter.Workbook) -> dict:
    return {
        "title":   wb.add_format({"bold": True, "font_size": 14, "font_color": "#003087"}),
        "sub":     wb.add_format({"italic": True, "font_color": "#555555"}),
        "warn":    wb.add_format({"bold": True, "italic": True, "font_color": "#B71C1C"}),
        "hdr":     wb.add_format({"bold": True, "bg_color": "#003087", "font_color": "white", "border": 1, "align": "center"}),
        "hdr_warn":wb.add_format({"bold": True, "bg_color": "#B71C1C", "font_color": "white", "border": 1, "align": "center"}),
        "num":     wb.add_format({"num_format": "#,##0",    "border": 1}),
        "num2":    wb.add_format({"num_format": "#,##0.00", "border": 1}),
        "cell":    wb.add_format({"border": 1}),
        "label":   wb.add_format({"bold": True, "bg_color": "#E3F2FD", "border": 1}),
        "lube":    wb.add_format({"border": 1, "bg_color": "#FFF3E0", "italic": True, "font_color": "#E65100"}),
        "active":  wb.add_format({"border": 1, "bg_color": "#E8F5E9", "font_color": "#1B7E24"}),
        "atrisk":  wb.add_format({"border": 1, "bg_color": "#FFF9C4", "font_color": "#F57F17"}),
        "dormant": wb.add_format({"border": 1, "bg_color": "#FFE0B2", "font_color": "#E65100"}),
        "churned": wb.add_format({"border": 1, "bg_color": "#FFEBEE", "font_color": "#B71C1C"}),
    }


def _write_df(ws: Any, df: pd.DataFrame, fmts: dict, row0: int = 0, hdr: str = "hdr") -> None:
    for c, col in enumerate(df.columns):
        ws.write(row0, c, col, fmts[hdr])
    for r, (_, row) in enumerate(df.iterrows(), 1):
        for c, val in enumerate(row):
            if isinstance(val, (int, np.integer)):
                ws.write(row0 + r, c, int(val), fmts["num"])
            elif isinstance(val, (float, np.floating)):
                ws.write(row0 + r, c, round(float(val), 2), fmts["num2"])
            else:
                ws.write(row0 + r, c, str(val) if pd.notna(val) else "", fmts["cell"])


def _write_kv(ws: Any, row: int, key: str, val: Any, fmts: dict, warn: bool = False) -> None:
    ws.write(row, 0, key, fmts["label"])
    fmt = fmts["num"] if isinstance(val, (int, np.integer)) else (
          fmts["num2"] if isinstance(val, float) else fmts["cell"])
    if warn:
        fmt = fmts["lube"]
    if isinstance(val, (int, np.integer)):
        ws.write(row, 1, int(val), fmt)
    elif isinstance(val, float):
        ws.write(row, 1, val, fmt)
    else:
        ws.write(row, 1, str(val), fmt)


def write_excel_report(
    kpis:          dict,
    cat_mix:       pd.DataFrame,
    monthly:       pd.DataFrame,
    monthly_cat:   pd.DataFrame,
    dealers:       pd.DataFrame,
    ym_materials:  pd.DataFrame,
    acc_sample:    pd.DataFrame,
    churn:         pd.DataFrame,
    channel_cmp:   pd.DataFrame,
) -> None:
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb   = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))
    fmts = _wb_fmts(wb)

    # ── Sheet 1: Summary KPIs ────────────────────────────────
    ws = wb.add_worksheet("Summary")
    ws.set_column("A:A", 45)
    ws.set_column("B:B", 26)
    ws.write("A1", "Stage 5 — Parts & Tyres Sales EDA (All Product Categories)", fmts["title"])
    ws.write("A2", "Dealer channel (Seeduwa PDC) + Service channel (Yamaha service centres)", fmts["sub"])
    ws.write("A3", "⚠ Lubricants excluded from revenue KPIs — bulk-reseller accounting records negative Net Sales", fmts["warn"])
    for r, (k, v) in enumerate(kpis.items()):
        _write_kv(ws, r + 5, k, v, fmts, warn="Lubricant" in k or "anomalous" in k)

    # ── Sheet 2: Product Category Mix ────────────────────────
    ws = wb.add_worksheet("Category Mix")
    ws.set_column("A:A", 30)
    ws.set_column("B:G", 20)
    ws.write("A1", "Revenue by Product Category", fmts["title"])
    ws.write("A2", "Lubricants shown with ⚠ flag — negative gross sales are an accounting anomaly", fmts["warn"])

    headers = list(cat_mix.columns)
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmts["hdr"])
    for r, (_, row) in enumerate(cat_mix.iterrows(), 1):
        is_lube = row["product_category"] in _LUBRICANT_CATEGORIES
        row_fmt = fmts["lube"] if is_lube else fmts["cell"]
        for c, val in enumerate(row):
            if isinstance(val, (int, np.integer)):
                ws.write(3 + r, c, int(val), fmts["num"] if not is_lube else fmts["lube"])
            elif isinstance(val, (float, np.floating)):
                ws.write(3 + r, c, round(float(val), 2), fmts["num2"] if not is_lube else fmts["lube"])
            else:
                ws.write(3 + r, c, str(val) if pd.notna(val) else "", row_fmt)

    # Pie chart — excl lubricants
    non_lube = cat_mix[~cat_mix["product_category"].isin(_LUBRICANT_CATEGORIES)].reset_index(drop=True)
    pie = wb.add_chart({"type": "pie"})
    n_cat = len(non_lube)
    # Write temp data for pie
    ws2 = wb.add_worksheet("_cat_chart_data")
    ws2.hide()
    for i, (_, row) in enumerate(non_lube.iterrows()):
        ws2.write(i, 0, row["product_category"])
        ws2.write(i, 1, float(row["gross_sales"]))
    pie.add_series({
        "name":       "Category Mix",
        "categories": ["_cat_chart_data", 0, 0, n_cat - 1, 0],
        "values":     ["_cat_chart_data", 0, 1, n_cat - 1, 1],
    })
    pie.set_title({"name": "Revenue Mix (excl. Lubricants)"})
    pie.set_size({"width": 420, "height": 320})
    ws.insert_chart("I4", pie)

    # ── Sheet 3: Monthly Trend by Category ───────────────────
    ws = wb.add_worksheet("Monthly by Category")
    ws.set_column("A:A", 12)
    ws.set_column("B:D", 28)
    ws.write("A1", "Monthly Sales by Product Category", fmts["title"])
    ws.write("A2", "Lubricants shown for reference — negative values are accounting anomaly", fmts["warn"])
    _write_df(ws, monthly_cat, fmts, row0=3)

    # ── Sheet 4: Monthly Trend (excl. Lubricants) ────────────
    ws = wb.add_worksheet("Monthly Trend")
    ws.set_column("A:A", 12)
    ws.set_column("B:I", 18)
    ws.write("A1", "Monthly Revenue Trend by Channel (Lubricants excluded)", fmts["title"])
    _write_df(ws, monthly, fmts, row0=2)
    n = len(monthly[monthly["channel"] == "Dealer"])
    if n > 0:
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name":       "Gross Sales",
            "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
            "values":     ["Monthly Trend", 3, 3, 2 + n, 3],
            "fill":       {"color": "#003087"},
        })
        chart.add_series({
            "name":       "Returns",
            "categories": ["Monthly Trend", 3, 0, 2 + n, 0],
            "values":     ["Monthly Trend", 3, 4, 2 + n, 4],
            "fill":       {"color": "#B71C1C"},
        })
        chart.set_title({"name": "Monthly Revenue — Dealer Channel (excl. Lubricants)"})
        chart.set_y_axis({"name": f"Value ({CURRENCY})", "num_format": "#,##0"})
        chart.set_size({"width": 720, "height": 340})
        ws.insert_chart("K3", chart)

    # ── Sheet 5: Channel Comparison ──────────────────────────
    ws = wb.add_worksheet("Channel Comparison")
    ws.set_column("A:F", 22)
    ws.write("A1", "Channel Comparison: Dealer vs Service (lubricants excluded)", fmts["title"])
    _write_df(ws, channel_cmp, fmts, row0=3)

    # ── Sheet 6: Top Dealers ─────────────────────────────────
    ws = wb.add_worksheet("Top Dealers")
    ws.set_column("A:A", 38)
    ws.set_column("B:H", 18)
    ws.write("A1", f"Top {len(dealers)} Dealers by Gross Sales (lubricants excluded)", fmts["title"])
    _write_df(ws, dealers, fmts, row0=2)

    # ── Sheet 7: Top Yamaha Parts ────────────────────────────
    ws = wb.add_worksheet("Top Yamaha Parts")
    ws.set_column("A:A", 22)
    ws.set_column("B:B", 50)
    ws.set_column("C:G", 18)
    ws.write("A1", f"Top {len(ym_materials)} Yamaha Spare Parts by Gross Sales", fmts["title"])
    _write_df(ws, ym_materials, fmts, row0=2)

    # ── Sheet 8: Accessories & Filters Sample ────────────────
    ws = wb.add_worksheet("Accessories Sample")
    ws.set_column("A:A", 16)
    ws.set_column("B:B", 22)
    ws.set_column("C:C", 50)
    ws.set_column("D:G", 18)
    ws.write("A1", "Accessories & Filters (AWPMA) — Top Materials Breakdown", fmts["title"])
    ws.write(
        "A2",
        "Note: AWPMA mixes oil filters / Yamaha parts with Suzuki Alto 800 body panels. "
        "Review and re-scope this category as needed.",
        fmts["sub"],
    )
    _write_df(ws, acc_sample, fmts, row0=3)

    # ── Sheet 10: Dealer Churn ───────────────────────────────
    ws = wb.add_worksheet("Dealer Churn")
    ws.set_column("A:A", 38)
    ws.set_column("B:H", 18)
    ws.write("A1", "Dealer Churn Analysis — Activity Classification", fmts["title"])
    ws.write("A2", "Active ≤90 days | At-Risk 91–180d | Dormant 181–365d | Churned >365d", fmts["sub"])
    status_fmt = {"Active": "active", "At-Risk": "atrisk", "Dormant": "dormant", "Churned": "churned"}
    headers = list(churn.columns)
    status_col = headers.index("status") if "status" in headers else -1
    for c, h in enumerate(headers):
        ws.write(3, c, h, fmts["hdr"])
    for r, (_, row) in enumerate(churn.iterrows(), 1):
        sfmt_key = status_fmt.get(str(row.get("status", "")), "cell")
        for c, val in enumerate(row):
            fmt = fmts[sfmt_key] if c == status_col else fmts["cell"]
            if isinstance(val, (int, np.integer)):
                ws.write(3 + r, c, int(val), fmts["num"])
            elif isinstance(val, (float, np.floating)):
                ws.write(3 + r, c, round(float(val), 2), fmts["num2"])
            else:
                ws.write(3 + r, c, str(val) if pd.notna(val) else "", fmt)

    # ── Sheet 11: Churn Summary ──────────────────────────────
    sum_ws = wb.add_worksheet("Churn Summary")
    sum_ws.write("A1", "Dealer Activity Summary", fmts["title"])
    status_counts = churn["status"].value_counts()
    for r, (status, count) in enumerate(status_counts.items()):
        sum_ws.write(r + 2, 0, status, fmts["label"])
        sum_ws.write(r + 2, 1, int(count), fmts["num"])
    pie2 = wb.add_chart({"type": "pie"})
    n_s = len(status_counts)
    pie2.add_series({
        "name":       "Dealer Status",
        "categories": ["Churn Summary", 2, 0, 1 + n_s, 0],
        "values":     ["Churn Summary", 2, 1, 1 + n_s, 1],
    })
    pie2.set_title({"name": "Dealer Activity Distribution"})
    pie2.set_size({"width": 400, "height": 300})
    sum_ws.insert_chart("D2", pie2)

    wb.close()
    logger.info(f"Excel report: {_OUTPUT_EXCEL}")


# ══════════════════════════════════════════════════════════════
# 4. Main entry point
# ══════════════════════════════════════════════════════════════

def run(refresh: bool = False) -> None:
    """Execute Stage 5: Sales EDA + Dealer Churn Analysis."""
    logger.info("=" * 55)
    logger.info("STAGE 5 — SALES EDA + DEALER CHURN (ALL CATEGORIES)")
    logger.info("=" * 55)

    df = load_and_clean(refresh=refresh)

    kpis         = summary_kpis(df)
    cat_df       = category_mix(df)
    monthly      = monthly_revenue_trend(df)
    monthly_cat  = monthly_by_category(df)
    dealers_df   = top_dealers(df)
    ym_mats      = top_materials(df, category="Yamaha Spare Parts")
    acc_df       = accessories_sample(df)
    churn_df     = dealer_churn_analysis(df)
    channel_df   = channel_comparison(df)

    write_excel_report(
        kpis, cat_df, monthly, monthly_cat,
        dealers_df, ym_mats, acc_df, churn_df, channel_df,
    )

    logger.info("=" * 55)
    logger.info("STAGE 5 SUMMARY")
    logger.info("=" * 55)
    for k, v in kpis.items():
        logger.info(f"  {k:<45}: {v}")

    logger.info("  Category breakdown (all):")
    for _, row in cat_df.iterrows():
        flag = "  ⚠ anomaly" if row["product_category"] in _LUBRICANT_CATEGORIES else ""
        logger.info(f"    {row['product_category']:<30}: LKR {int(row['gross_sales']):>16,}{flag}")

    logger.info("  Churn breakdown:")
    for status, cnt in churn_df["status"].value_counts().items():
        logger.info(f"    {status:<10}: {cnt:>4} dealers")

    logger.info("Stage 5 complete.")
