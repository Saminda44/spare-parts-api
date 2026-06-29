"""Stage 5: Spare Parts Sales EDA — Yamaha dealer-scoped billing analysis.

Data source:
  sales.xlsx — SAP billing export (Payer = the customer / dealer name).

Dealer scoping:
  Inner join ``Payer`` = ``Dealer Name`` from dealers.xlsx.
  Unmatched Payers (institutions, end-consumers, etc.) are excluded.
  No Active/Inactive filter — all matched dealers are included.

Dealer type (from dealers.xlsx ``Type`` column):
  MC  → motorcycle spare-parts dealer
  OBM → outboard-motor spare-parts dealer

MC sub-category (from ``Material`` description keyword):
  YAMALUBE       → Lubricant
  KARATE BATTERY → Battery
  KATANA TYRE    → Tyre
  (all others)   → Spare Parts

Sale vs Return classification:
  SlsVolQty > 0  → bill_class = "sale"
  SlsVolQty < 0  → bill_class = "return"
  SlsVolQty == 0 → dropped

Value metric: ``Net Sales`` (LKR after discount, before tax).
  Return Net Sales is already negative — no sign flip needed.

Hierarchy for drilldown:
  Province → RM → ASE → Dealer (all from dealers.xlsx).

Outputs:
  data/interim/sales_clean.parquet
  data/outputs/stage05_sales_eda.xlsx
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from src.config.constants import CURRENCY
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW, SRC_CONFIG

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────
_SALES_RAW = DATA_RAW / "sales.xlsx"
_DEALERS_RAW = DATA_RAW / "Dealers.xlsx"
_CLEAN_PARQ = DATA_INTERIM / "sales_clean.parquet"
_OUTPUT_EXCEL = DATA_OUTPUTS / "stage05_sales_eda.xlsx"

# ── MC sub-category keywords (applied to Material description) ──
_KW_LUBRICANT = "YAMALUBE"
_KW_BATTERY = "KARATE BATTERY"
_KW_TYRE = "KATANA TYRE"


def _mc_category(material: str) -> str:
    """Classify an MC material into its sub-category based on description keywords."""
    m = str(material).upper()
    if _KW_LUBRICANT in m:
        return "Lubricant"
    if _KW_BATTERY in m:
        return "Battery"
    if _KW_TYRE in m:
        return "Tyre"
    return "Spare Parts"


# ══════════════════════════════════════════════════════════════
# 1. Loading & cleaning
# ══════════════════════════════════════════════════════════════


def load_dealer_map() -> pd.DataFrame:
    """Load dealers.xlsx with all hierarchy attributes.

    Returns ALL dealers (Active and Inactive) whose Dealer Name is non-empty,
    including duplicate-named rows. Callers that need a unique-name map must
    handle deduplication themselves (e.g. load_and_clean uses ASE disambiguation).

    Columns returned: Dealer Code, Dealer Name, Type, Province, District, ASE, RM.
    """
    df = pd.read_excel(_DEALERS_RAW, dtype=str)
    for col in df.columns:
        df[col] = df[col].fillna("").str.strip()
    df = df[df["Dealer Name"] != ""].copy()
    # Drop rows with placeholder or non-numeric Dealer Codes
    df = df[df["Dealer Code"].str.strip().str.match(r"^\d+$", na=False)].copy()
    logger.info(
        f"Dealer master: {len(df)} dealers  "
        f"(MC={(df['Type']=='MC').sum()}, OBM={(df['Type']=='OBM').sum()})"
    )
    keep = [
        c
        for c in ["Dealer Code", "Dealer Name", "Type", "Province", "District", "ASE", "RM"]
        if c in df.columns
    ]
    df = df[keep]
    dup_names = df[df.duplicated("Dealer Name", keep=False)]["Dealer Name"].unique()
    if len(dup_names):
        logger.info(
            f"dealers.xlsx has {len(dup_names)} duplicate Dealer Names "
            f"(will be split by Sales Employee = ASE match): "
            + ", ".join(sorted(dup_names)[:5])
            + (f" ... and {len(dup_names)-5} more" if len(dup_names) > 5 else "")
        )
    return df


def load_and_clean(refresh: bool = False) -> pd.DataFrame:
    """Load sales.xlsx, scope to Yamaha dealers, assign categories and bill_class.

    Business rules applied:
    - Payer (sales) must exactly match Dealer Name (dealers.xlsx) — inner join.
    - bill_class 'sale'   : SlsVolQty > 0
    - bill_class 'return' : SlsVolQty < 0
    - Rows with SlsVolQty == 0 are dropped.
    - mc_category assigned only for MC dealers; OBM rows carry mc_category = None.
    """
    if not refresh and _CLEAN_PARQ.exists():
        logger.info("Loading cached sales_clean.parquet")
        return pd.read_parquet(_CLEAN_PARQ)

    dealer_df = load_dealer_map()

    raw = pd.read_excel(_SALES_RAW)
    logger.info(f"Raw sales rows: {len(raw):,}")

    # ── Normalise key columns ────────────────────────────────────
    raw["Billing Date"] = pd.to_datetime(raw["Billing Date"], dayfirst=True, errors="coerce")
    raw["Net Sales"] = pd.to_numeric(raw["Net Sales"], errors="coerce").fillna(0.0)
    raw["SlsVolQty"] = pd.to_numeric(raw["SlsVolQty"], errors="coerce").fillna(0.0)
    raw["Payer"] = raw["Payer"].astype(str).str.strip()
    raw["Material"] = raw["Material"].astype(str).str.strip()

    # ── Drop rows with no date or zero qty ──────────────────────
    before = len(raw)
    raw = raw[raw["Billing Date"].notna() & (raw["SlsVolQty"] != 0)].copy()
    logger.info(f"After date/zero-qty filter: {len(raw):,} (dropped {before - len(raw):,})")

    # ── Remove exact duplicates from SAP export artefact ────────
    # Each billing line (Billing Document + Item) appears twice in the raw
    # export with identical data. Drop the duplicate before any aggregation.
    before_dedup = len(raw)
    raw = raw.drop_duplicates(subset=["Billing Document", "Item"])
    logger.info(f"Dedup (Billing Document, Item): {before_dedup - len(raw):,} duplicates removed, {len(raw):,} rows remain")

    # ── Payer aliases: name-mismatch dealers not fixed by dealers.xlsx ──────
    # src/config/payer_aliases.csv maps Payer strings in sales.xlsx that don't
    # match any Dealer Name in dealers.xlsx (e.g. name suffix differences).
    _ALIASES_FILE = SRC_CONFIG / "payer_aliases.csv"
    aliases: dict[str, str] = {}
    if _ALIASES_FILE.exists():
        alias_df = pd.read_csv(_ALIASES_FILE, dtype=str)
        aliases = dict(
            zip(alias_df["payer_name"].str.lower(), alias_df["dealer_code"])
        )
        logger.info(f"Loaded {len(aliases)} payer alias(es) from payer_aliases.csv")

    # ── Dealer scoping: Payer = Dealer Name (inner join, case-insensitive) ────
    # dealer_df may have duplicate Dealer Names (same name, different codes for
    # different regions). We join ALL matching rows then disambiguate by ASE.
    raw["_payer_key"] = raw["Payer"].str.lower()
    raw["_se_key"] = raw["Sales Employee"].astype(str).str.lower().str.strip()
    dealer_df = dealer_df.copy()
    dealer_df["_name_key"] = dealer_df["Dealer Name"].str.lower()
    dealer_df["_ase_key"] = dealer_df["ASE"].astype(str).str.lower().str.strip()

    # Apply payer aliases: remap unmatched Payers to their correct Dealer Code.
    # Skip any alias whose payer_name already matches an existing Dealer Name
    # (e.g. if dealers.xlsx was updated after the alias was written).
    if aliases:
        existing_name_keys = set(dealer_df["_name_key"].tolist())
        alias_codes = list(aliases.values())
        alias_dealer_rows = dealer_df[dealer_df["Dealer Code"].isin(alias_codes)].copy()
        for payer_lower, code in aliases.items():
            if payer_lower in existing_name_keys:
                logger.info(
                    f"Alias '{payer_lower}' already matched by dealers.xlsx — skipping injection"
                )
                continue
            matched = alias_dealer_rows[alias_dealer_rows["Dealer Code"] == code].copy()
            matched["_name_key"] = payer_lower
            dealer_df = pd.concat([dealer_df, matched], ignore_index=True)
            logger.info(f"Alias injected: '{payer_lower}' -> Dealer Code {code}")

    before = len(raw)
    merged = raw.merge(dealer_df, left_on="_payer_key", right_on="_name_key", how="inner")

    # ── Disambiguate duplicate-name matches using Sales Employee = ASE ──────
    # When a Payer name matches multiple Dealer Codes (duplicate names in
    # dealers.xlsx), each billing row appears duplicated in `merged`. Resolve by
    # matching Sales Employee (the field in sales.xlsx) to ASE (dealers.xlsx).
    # Strategy per (Billing Document, Item):
    #   - If ANY row matches SE == ASE → keep ONLY those row(s), drop the rest
    #   - If NO rows match SE == ASE → keep the first row (first-occurrence fallback)
    dup_bill_mask = merged.duplicated(subset=["Billing Document", "Item"], keep=False)
    n_ambiguous = dup_bill_mask.sum()
    if n_ambiguous:
        clean = merged[~dup_bill_mask].copy()
        ambiguous = merged[dup_bill_mask].copy()
        ambiguous["_ase_match"] = ambiguous["_se_key"] == ambiguous["_ase_key"]

        # Per billing line: how many rows have an ASE match?
        # Use transform (broadcasts back to index) so NaN Billing Document rows
        # are handled safely — unlike groupby().any() which drops NaN keys.
        ambiguous["_ase_cnt"] = ambiguous.groupby(
            ["Billing Document", "Item"], dropna=False
        )["_ase_match"].transform("sum")

        # Lines with ≥1 ASE match → keep only the first ASE-matching row per line
        resolved = (
            ambiguous[(ambiguous["_ase_cnt"] > 0) & ambiguous["_ase_match"]]
            .drop_duplicates(subset=["Billing Document", "Item"], keep="first")
            .copy()
        )

        # Lines with 0 ASE matches → keep first occurrence (fallback to original code)
        fallback = (
            ambiguous[ambiguous["_ase_cnt"] == 0]
            .drop_duplicates(subset=["Billing Document", "Item"], keep="first")
            .copy()
        )

        ambiguous.drop(columns=["_ase_match", "_ase_cnt"], inplace=True)
        resolved.drop(columns=["_ase_match", "_ase_cnt"], inplace=True, errors="ignore")
        fallback.drop(columns=["_ase_match", "_ase_cnt"], inplace=True, errors="ignore")

        merged = pd.concat([clean, resolved, fallback], ignore_index=True)
        logger.info(
            f"Duplicate-name disambiguation: {n_ambiguous} ambiguous rows -> "
            f"{len(resolved)} resolved by ASE, {len(fallback)} first-occurrence fallback"
        )

    merged.drop(columns=["_payer_key", "_name_key", "_se_key", "_ase_key"], inplace=True)
    raw = merged
    logger.info(
        f"Dealer scoping: {len(raw):,} rows kept, {before - len(raw):,} excluded  "
        f"(MC={(raw['Type']=='MC').sum():,}, OBM={(raw['Type']=='OBM').sum():,})"
    )

    # ── Rename Type → dealer_type ────────────────────────────────
    raw.rename(columns={"Type": "dealer_type"}, inplace=True)

    # ── Bill class from SlsVolQty sign ───────────────────────────
    raw["bill_class"] = np.where(raw["SlsVolQty"] > 0, "sale", "return")

    # ── MC sub-category ─────────────────────────────────────────
    raw["mc_category"] = np.where(
        raw["dealer_type"] == "MC",
        raw["Material"].apply(_mc_category),
        pd.NA,
    )

    # ── Year-Month string for trending ──────────────────────────
    raw["Year_Month_str"] = raw["Billing Date"].dt.strftime("%Y-%m")

    # ── Summary at load time ─────────────────────────────────────
    sale_rows = raw[raw["bill_class"] == "sale"]
    return_rows = raw[raw["bill_class"] == "return"]
    logger.info(f"Sale lines:   {len(sale_rows):,}  LKR {sale_rows['Net Sales'].sum():,.0f}")
    logger.info(f"Return lines: {len(return_rows):,}  LKR {return_rows['Net Sales'].sum():,.0f}")

    mc_sale = sale_rows[sale_rows["dealer_type"] == "MC"]
    for cat, grp in mc_sale.groupby("mc_category"):
        logger.info(f"  MC {cat:<15}: {len(grp):>7,} lines  LKR {grp['Net Sales'].sum():>16,.0f}")

    # ── Select columns for parquet ───────────────────────────────
    keep = [
        c
        for c in [
            "Payer",
            "Dealer Code",
            "dealer_type",
            "mc_category",
            "Province",
            "District",
            "ASE",
            "RM",
            "Material",
            "Billing Date",
            "Year_Month_str",
            "Billing Document",
            "Item",
            "SlsVolQty",
            "Net Sales",
            "bill_class",
        ]
        if c in raw.columns
    ]
    out = raw[keep].copy()

    for col in out.select_dtypes(include="object").columns:
        out[col] = out[col].astype(str).replace("nan", pd.NA)

    _CLEAN_PARQ.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(_CLEAN_PARQ, index=False)
    logger.info(f"Saved: {_CLEAN_PARQ}  ({len(out):,} rows)")
    return out


# ══════════════════════════════════════════════════════════════
# 2. EDA computations
# ══════════════════════════════════════════════════════════════


def summary_kpis(df: pd.DataFrame) -> dict:
    """Top-level KPIs — sale value, return value, return rate, qty, unique counts."""
    sale = df[df["bill_class"] == "sale"]
    ret = df[df["bill_class"] == "return"]
    sale_v = float(sale["Net Sales"].sum())
    ret_v = float(ret["Net Sales"].sum())  # already negative
    return {
        "Period": (f"{df['Billing Date'].min().date()} → {df['Billing Date'].max().date()}"),
        "Total Sale Lines": int(len(sale)),
        "Total Return Lines": int(len(ret)),
        "Unique Dealers": int(df["Payer"].nunique()),
        "Unique Materials (SKUs)": int(df["Material"].nunique()),
        f"Total Sale Value ({CURRENCY})": round(sale_v),
        f"Total Return Value ({CURRENCY})": round(abs(ret_v)),
        f"Net Value ({CURRENCY})": round(sale_v + ret_v),
        "Return Rate % (value)": round(abs(ret_v) / max(sale_v, 1) * 100, 2),
        "Total Sale Qty": float(sale["SlsVolQty"].sum()),
        "Total Return Qty": float(abs(ret["SlsVolQty"].sum())),
    }


def monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly sale value, return value, net value, and quantities."""
    grp = (
        df.groupby(["Year_Month_str", "bill_class"])
        .agg(value=("Net Sales", "sum"), qty=("SlsVolQty", "sum"))
        .reset_index()
        .pivot(index="Year_Month_str", columns="bill_class", values=["value", "qty"])
        .fillna(0)
    )
    grp.columns = pd.Index(["_".join(c) for c in grp.columns])
    grp = grp.reset_index().rename(columns={"Year_Month_str": "period"})

    for col in ["value_sale", "value_return", "qty_sale", "qty_return"]:
        if col not in grp.columns:
            grp[col] = 0.0

    grp["return_value_lkr"] = grp["value_return"].abs()
    grp["sale_value_lkr"] = grp["value_sale"]
    grp["net_value_lkr"] = grp["value_sale"] + grp["value_return"]
    grp["sale_qty"] = grp["qty_sale"]
    grp["return_qty"] = grp["qty_return"].abs()
    return (
        grp[
            [
                "period",
                "sale_value_lkr",
                "return_value_lkr",
                "net_value_lkr",
                "sale_qty",
                "return_qty",
            ]
        ]
        .sort_values("period")
        .reset_index(drop=True)
    )


def mc_monthly_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly sale value split by MC sub-category (MC dealers only, sale lines)."""
    mc_sale = df[
        (df["dealer_type"] == "MC") & (df["bill_class"] == "sale") & df["mc_category"].notna()
    ].copy()
    if mc_sale.empty:
        return pd.DataFrame(columns=["period", "Lubricant", "Battery", "Tyre", "Spare Parts"])
    pivot = (
        mc_sale.groupby(["Year_Month_str", "mc_category"])["Net Sales"]
        .sum()
        .reset_index()
        .pivot(index="Year_Month_str", columns="mc_category", values="Net Sales")
        .fillna(0)
        .reset_index()
        .rename(columns={"Year_Month_str": "period"})
    )
    for col in ["Lubricant", "Battery", "Tyre", "Spare Parts"]:
        if col not in pivot.columns:
            pivot[col] = 0.0
    return (
        pivot[["period", "Lubricant", "Battery", "Tyre", "Spare Parts"]]
        .sort_values("period")
        .reset_index(drop=True)
    )


def part_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Part-wise analysis: qty, value, return rate per Material description."""
    sale = df[df["bill_class"] == "sale"]
    ret = df[df["bill_class"] == "return"]

    s = sale.groupby("Material").agg(
        sale_lines=("SlsVolQty", "count"),
        sale_qty=("SlsVolQty", "sum"),
        sale_value_lkr=("Net Sales", "sum"),
    )
    r = ret.groupby("Material").agg(
        return_lines=("SlsVolQty", "count"),
        return_qty=("SlsVolQty", lambda x: abs(x.sum())),
        return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
    )
    agg = s.join(r, how="left").fillna(0).reset_index()
    agg["net_value_lkr"] = agg["sale_value_lkr"] - agg["return_value_lkr"]
    agg["net_qty"] = agg["sale_qty"] - agg["return_qty"]
    agg["return_rate_pct"] = (
        (agg["return_value_lkr"] / agg["sale_value_lkr"].replace(0, np.nan) * 100)
        .fillna(0)
        .round(2)
    )
    return agg.sort_values("sale_value_lkr", ascending=False).reset_index(drop=True)


def dealer_performance(df: pd.DataFrame) -> pd.DataFrame:
    """Dealer-level performance: sale value, return value, return rate, SKU count."""
    sale = df[df["bill_class"] == "sale"]
    ret = df[df["bill_class"] == "return"]

    dim_cols = ["Payer"] + [
        c
        for c in ["Dealer Code", "dealer_type", "Province", "District", "ASE", "RM"]
        if c in df.columns
    ]

    s = (
        sale.groupby(dim_cols, dropna=False)
        .agg(
            sale_qty=("SlsVolQty", "sum"),
            sale_value_lkr=("Net Sales", "sum"),
            unique_skus=("Material", "nunique"),
        )
        .reset_index()
    )

    r = (
        ret.groupby("Payer", dropna=False)
        .agg(
            return_qty=("SlsVolQty", lambda x: abs(x.sum())),
            return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
        )
        .reset_index()
    )

    agg = s.merge(r, on="Payer", how="left").fillna(0)
    agg["return_rate_pct"] = (
        (agg["return_value_lkr"] / agg["sale_value_lkr"].replace(0, np.nan) * 100)
        .fillna(0)
        .round(2)
    )
    return (
        agg.rename(columns={"Payer": "dealer_name"})
        .sort_values("sale_value_lkr", ascending=False)
        .reset_index(drop=True)
    )


def _hierarchy_perf(
    df: pd.DataFrame, group_col: str, extra_cols: list[str] | None = None
) -> pd.DataFrame:
    """Generic hierarchy-level aggregation (RM, ASE, District, Province)."""
    if group_col not in df.columns:
        return pd.DataFrame()
    sale = df[df["bill_class"] == "sale"]
    ret = df[df["bill_class"] == "return"]
    cols = [group_col] + (extra_cols or [])
    cols = [c for c in cols if c in df.columns]

    s = (
        sale.groupby(cols, dropna=False)
        .agg(
            sale_qty=("SlsVolQty", "sum"),
            sale_value_lkr=("Net Sales", "sum"),
            dealer_count=("Payer", "nunique"),
            unique_skus=("Material", "nunique"),
        )
        .reset_index()
    )

    r = (
        ret.groupby(group_col, dropna=False)
        .agg(
            return_qty=("SlsVolQty", lambda x: abs(x.sum())),
            return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
        )
        .reset_index()
    )

    agg = s.merge(r, on=group_col, how="left").fillna(0)
    agg["return_rate_pct"] = (
        (agg["return_value_lkr"] / agg["sale_value_lkr"].replace(0, np.nan) * 100)
        .fillna(0)
        .round(2)
    )
    return agg.sort_values("sale_value_lkr", ascending=False).reset_index(drop=True)


def rm_performance(df: pd.DataFrame) -> pd.DataFrame:
    """RM-level performance."""
    return _hierarchy_perf(df, "RM")


def ase_performance(df: pd.DataFrame) -> pd.DataFrame:
    """ASE-level performance (with RM context)."""
    return _hierarchy_perf(df, "ASE", extra_cols=["RM"])


def district_performance(df: pd.DataFrame) -> pd.DataFrame:
    """District-level performance (with Province context)."""
    return _hierarchy_perf(df, "District", extra_cols=["Province"])


def province_performance(df: pd.DataFrame) -> pd.DataFrame:
    """Province-level performance."""
    return _hierarchy_perf(df, "Province")


def mc_category_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """MC sub-category summary: Lubricant, Battery, Tyre, Spare Parts."""
    mc = df[(df["dealer_type"] == "MC") & df["mc_category"].notna()].copy()
    if mc.empty:
        return pd.DataFrame()
    sale = mc[mc["bill_class"] == "sale"]
    ret = mc[mc["bill_class"] == "return"]

    s = (
        sale.groupby("mc_category")
        .agg(
            sale_lines=("Net Sales", "count"),
            sale_qty=("SlsVolQty", "sum"),
            sale_value_lkr=("Net Sales", "sum"),
            unique_skus=("Material", "nunique"),
        )
        .reset_index()
    )
    r = (
        ret.groupby("mc_category")
        .agg(
            return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
        )
        .reset_index()
    )

    agg = s.merge(r, on="mc_category", how="left").fillna(0)
    total_sale = agg["sale_value_lkr"].sum()
    agg["value_share_pct"] = (agg["sale_value_lkr"] / max(total_sale, 1) * 100).round(2)
    agg["return_rate_pct"] = (
        (agg["return_value_lkr"] / agg["sale_value_lkr"].replace(0, np.nan) * 100)
        .fillna(0)
        .round(2)
    )
    cat_order = {"Lubricant": 0, "Battery": 1, "Tyre": 2, "Spare Parts": 3}
    agg["_ord"] = agg["mc_category"].map(cat_order).fillna(99)
    return agg.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 3. Excel report
# ══════════════════════════════════════════════════════════════


def write_excel(df: pd.DataFrame) -> None:
    """Write stage05 EDA report to Excel."""

    kpis = summary_kpis(df)
    monthly = monthly_trend(df)
    parts = part_analysis(df)
    dealers = dealer_performance(df)
    rms = rm_performance(df)
    ases = ase_performance(df)
    districts = district_performance(df)
    provinces = province_performance(df)
    mc_cats = mc_category_breakdown(df)
    mc_monthly = mc_monthly_by_category(df)

    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(_OUTPUT_EXCEL, engine="xlsxwriter") as writer:
        # KPIs sheet
        kpi_df = pd.DataFrame(list(kpis.items()), columns=["Metric", "Value"])
        kpi_df.to_excel(writer, sheet_name="Summary KPIs", index=False)

        monthly.to_excel(writer, sheet_name="Monthly Trend", index=False)
        mc_monthly.to_excel(writer, sheet_name="MC Monthly Category", index=False)
        mc_cats.to_excel(writer, sheet_name="MC Category Mix", index=False)
        parts.head(500).to_excel(writer, sheet_name="Part Analysis", index=False)
        dealers.to_excel(writer, sheet_name="Dealer Performance", index=False)
        rms.to_excel(writer, sheet_name="RM Performance", index=False)
        ases.to_excel(writer, sheet_name="ASE Performance", index=False)
        districts.to_excel(writer, sheet_name="District Performance", index=False)
        provinces.to_excel(writer, sheet_name="Province Performance", index=False)

    logger.info(f"Excel report: {_OUTPUT_EXCEL}")


# ══════════════════════════════════════════════════════════════
# 4. Pipeline entry point
# ══════════════════════════════════════════════════════════════


def run(refresh: bool = False) -> dict[str, Any]:
    """Run Stage 5 — Sales EDA pipeline.

    Args:
        refresh: Re-read raw Excel files even if parquet cache exists.

    Returns:
        Summary KPI dict.
    """
    logger.info("=== Stage 5: Sales EDA ===")
    df = load_and_clean(refresh=refresh)
    kpis = summary_kpis(df)

    logger.info("--- KPIs ---")
    for k, v in kpis.items():
        logger.info(f"  {k}: {v}")

    write_excel(df)
    logger.info("Stage 5 complete.")
    return kpis


if __name__ == "__main__":
    from loguru import logger as _log

    _log.remove()
    _log.add(lambda msg: print(msg, end=""), level="INFO")
    run(refresh=True)
