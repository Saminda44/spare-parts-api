"""Stage 7 — Stock movement analysis.

Inputs:
  data/raw/In_and_Out.xlsx            — 243 K SAP material document rows
  data/interim/part_master.parquet    — canonical part master (Stage 6 output)

Outputs:
  data/interim/stock_movements.parquet   — cleaned movement history (all types)
  data/interim/monthly_demand.parquet    — per-SKU monthly demand (→ Stages 9 & 10)
  data/interim/ingestion_log.parquet     — append-only ingestion audit trail
  data/outputs/stage07_stock_movement.xlsx — 6-sheet Excel report

Append semantics (CLAUDE.md §11):
  When run with a new or updated In_and_Out.xlsx, only genuinely new rows are
  appended to the existing parquet. Deduplication uses a SHA-256 hash of the
  natural key: Material + Posting Date + Movement Type + Qty + Value + Customer.
  Every ingestion event is recorded in ingestion_log.parquet.
"""

from __future__ import annotations

import hashlib
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

warnings.filterwarnings("ignore")

_MOVEMENTS_RAW     = DATA_RAW / "In_and_Out.xlsx"
_PART_MASTER       = DATA_INTERIM / "part_master.parquet"
_MOVEMENTS_PARQUET = DATA_INTERIM / "stock_movements.parquet"
_DEMAND_PARQUET    = DATA_INTERIM / "monthly_demand.parquet"
_INGESTION_LOG     = DATA_INTERIM / "ingestion_log.parquet"
_OUTPUT_XLSX       = DATA_OUTPUTS / "stage07_stock_movement.xlsx"

# Natural key columns for row-level SHA-256 deduplication
_HASH_COLS = ["material_12", "posting_date_str", "movement_type_str", "qty_str", "value_str", "customer"]

# SAP movement type classification
# Business meaning: MT 601 = goods issue to dealer (demand); MT 101 = goods
# receipt from supplier (replenishment). All other types are tracked but excluded
# from demand signals.
_MT_CLASS: dict[int, str] = {
    101: "receipt",        # GR goods receipt (incl. transit, sales order stock)
    102: "receipt_rev",    # GR reversal
    122: "return_vendor",  # RE return to vendor
    201: "cost_center",    # GI for cost center
    301: "transfer",       # TF transfer plant to plant
    311: "transfer",       # TF transfer within plant
    343: "adjustment",     # TF blocked to unrestricted
    344: "adjustment",     # TR blocked to unrestricted
    411: "transfer",       # TP sales order / special stock
    413: "transfer",       # TF sales order
    453: "transfer",       # TP returns
    501: "receipt",        # Receipt without PO
    551: "scrap",          # GI scrapping
    601: "issue",          # GD goods issue: delivery (primary demand signal)
    602: "issue_rev",      # GD goods delivery reversal
    641: "transfer",       # TF to stock in transit (inter-DC)
    642: "transfer",       # TR to stock in transit
    651: "return",         # GD return delivery (customer return)
    653: "return",         # GD returns unrestricted
    654: "return_rev",     # GD returns unrestricted reversal
    701: "adjustment",     # GR physical inventory
    702: "adjustment",     # GI physical inventory
    707: "adjustment",     # GR physical inventory blocked
    708: "adjustment",     # GI physical inventory blocked
}


def _classify_movement(mt: int) -> str:
    """Map SAP movement type integer to a semantic class string."""
    return _MT_CLASS.get(mt, "other")


# ---------------------------------------------------------------------------
# Hash-based deduplication helpers (CLAUDE.md §11)
# ---------------------------------------------------------------------------

def _row_hash(row: pd.Series) -> str:
    """Compute a deterministic SHA-256 hash for a single raw row.

    Business meaning: allows appending new monthly exports without re-processing
    the full history. The hash is on raw values before any transformation so
    that re-running against the same source file always yields the same hashes.
    """
    key = "|".join(str(row.get(c, "")) for c in _HASH_COLS)
    return hashlib.sha256(key.encode()).hexdigest()


def _compute_hashes(raw: pd.DataFrame) -> pd.Series:
    """Vectorised row hashing — attach helper string columns, hash, then drop."""
    raw = raw.copy()
    raw["posting_date_str"]  = raw.get("Posting Date",          pd.Series()).astype(str)
    raw["movement_type_str"] = raw.get("Movement Type",         pd.Series()).astype(str)
    raw["qty_str"]           = raw.get("Qty in unit of entry",  pd.Series()).astype(str)
    raw["value_str"]         = raw.get("Amt.in Loc.Cur.",       pd.Series()).astype(str)
    raw["material_12"]       = raw.get("Material",              pd.Series()).fillna("").astype(str).str.strip()
    raw["customer"]          = raw.get("Customer",              pd.Series()).fillna("").astype(str)
    return raw.apply(_row_hash, axis=1)


def _update_ingestion_log(
    filename: str,
    rows_in: int,
    inserted: int,
    duplicates_skipped: int,
    validation_failures: int = 0,
) -> None:
    """Append one row to the ingestion log parquet (create if absent)."""
    new_row = pd.DataFrame([{
        "filename":            filename,
        "rows_in":             rows_in,
        "inserted":            inserted,
        "duplicates_skipped":  duplicates_skipped,
        "validation_failures": validation_failures,
        "ingested_at":         datetime.now(tz=timezone.utc).isoformat(),
    }])
    if _INGESTION_LOG.exists():
        existing = pd.read_parquet(_INGESTION_LOG)
        combined = pd.concat([existing, new_row], ignore_index=True)
    else:
        combined = new_row
    _INGESTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(_INGESTION_LOG, index=False)
    logger.info(f"Ingestion log updated: {inserted} new rows from {filename}")


def _to_9digit(part_number: str) -> str:
    """Strip the trailing colour/revision suffix from a 12-digit Yamaha part number.

    Business meaning: In_and_Out.xlsx stores full 12-digit part numbers
    (e.g. 1GC-E4450-00-00) while part_master uses 9-digit (1GC-E4450-00).
    12-digit parts have exactly 3 hyphens; 9-digit parts have 2. Only strip
    if there are 3 hyphens (i.e. 4 segments).
    """
    if isinstance(part_number, str) and part_number.count("-") == 3:
        return part_number[:-3]
    return part_number


# ---------------------------------------------------------------------------
# Load & clean
# ---------------------------------------------------------------------------

def load_movements(append: bool = False) -> pd.DataFrame:
    """Load In_and_Out.xlsx, optionally merging with the existing parquet.

    Args:
        append: if True, hash-dedup against the existing parquet and only
                add genuinely new rows; otherwise process the file from scratch.

    Returns a cleaned DataFrame with columns:
      material_12, material_9, description, movement_class,
      posting_date, year_month_str, qty, value_lkr, movement_type, customer
    """
    logger.info(f"Loading stock movements from {_MOVEMENTS_RAW}")
    raw = pd.read_excel(_MOVEMENTS_RAW)
    logger.info(f"Raw movements: {len(raw):,} rows")
    rows_in = len(raw)

    # ── Append / hash-dedup ───────────────────────────────────────────────────
    if append and _MOVEMENTS_PARQUET.exists():
        existing = pd.read_parquet(_MOVEMENTS_PARQUET)
        new_hashes = _compute_hashes(raw)
        existing_hashes: set[str] = set()
        if "row_hash" in existing.columns:
            existing_hashes = set(existing["row_hash"].dropna())
        new_mask = ~new_hashes.isin(existing_hashes)
        duplicates_skipped = int((~new_mask).sum())
        raw_new = raw[new_mask].copy()
        logger.info(
            f"Append mode: {rows_in:,} rows in file | "
            f"{duplicates_skipped:,} duplicates skipped | "
            f"{len(raw_new):,} genuinely new rows"
        )
        if raw_new.empty:
            _update_ingestion_log(_MOVEMENTS_RAW.name, rows_in, 0, duplicates_skipped)
            return existing
        raw_new["row_hash"] = new_hashes[new_mask].values
        # Process only new rows through the clean pipeline, then merge
        new_clean = _clean_raw(raw_new)
        combined  = pd.concat([existing, new_clean], ignore_index=True)
        _update_ingestion_log(_MOVEMENTS_RAW.name, rows_in, len(new_clean), duplicates_skipped)
        return combined

    # ── Full load (no append) ─────────────────────────────────────────────────
    raw["row_hash"] = _compute_hashes(raw)
    result = _clean_raw(raw)
    _update_ingestion_log(_MOVEMENTS_RAW.name, rows_in, len(result), 0)
    return result


def _clean_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply column renaming, type coercions, and part-master join to raw rows.

    This is the shared transformation pipeline used by both full-load and
    append-mode paths so that new rows are processed identically to old ones.
    """

    # Normalise qty sign: negative = outbound, positive = inbound (already in data)
    df = raw.rename(columns={
        "Material":              "material_12",
        "Material Description":  "description_raw",
        "Qty in unit of entry":  "qty",
        "Movement Type":         "movement_type",
        "Posting Date":          "posting_date",
        "Amt.in Loc.Cur.":       "value_lkr",
        "Customer":              "customer",
        "Plant":                 "plant",
    }).copy()

    df["posting_date"] = pd.to_datetime(df["posting_date"], errors="coerce")
    df = df[df["posting_date"].notna()].copy()
    df["year_month_str"] = df["posting_date"].dt.strftime("%Y-%m")

    df["material_12"] = df["material_12"].fillna("").str.strip()
    df = df[df["material_12"] != ""].copy()

    df["material_9"] = df["material_12"].apply(_to_9digit)
    df["movement_class"] = df["movement_type"].apply(_classify_movement)

    # Scope filter: exclude non-Yamaha branded materials.
    # Business meaning: SAP material codes starting with "B" are Castrol/third-party
    # lubricants (GTX, ACTIV, CRB, EDGE, VECTON, MAGNATEC, etc.) that are not part of
    # the Yamaha spare-parts inventory optimisation scope. YAMALUBE items use standard
    # Yamaha part-number formats (e.g. 571901NAE) and are retained automatically.
    before = len(df)
    df = df[~df["material_9"].str.startswith("B", na=False)].copy()
    logger.info(f"Scope filter: removed {before - len(df):,} non-Yamaha rows (B-prefix materials)")

    # Join part_master for description & catalog info
    if _PART_MASTER.exists():
        master = pd.read_parquet(_PART_MASTER)[
            ["part_number", "description", "compatible_models", "catalog_models"]
        ].rename(columns={"part_number": "material_9", "description": "description_master"})
        df = df.merge(master, on="material_9", how="left")
        df["description"] = df["description_master"].fillna(df["description_raw"])
        df = df.drop(columns=["description_master", "description_raw"], errors="ignore")
    else:
        logger.warning("part_master.parquet not found — skipping enrichment")
        df = df.rename(columns={"description_raw": "description"})

    keep = [
        "row_hash",
        "material_12", "material_9", "description", "movement_type", "movement_class",
        "posting_date", "year_month_str", "qty", "value_lkr", "customer",
        "plant", "compatible_models", "catalog_models",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()

    logger.info(
        f"Movements cleaned: {len(df):,} rows | "
        f"{df['material_9'].nunique():,} unique SKUs | "
        f"{df['year_month_str'].nunique()} months"
    )
    return df


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def build_monthly_demand(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-SKU monthly demand from stock movements.

    Business meaning:
      - issue_qty   = MT 601 outbound to dealers → realised demand
      - return_qty  = MT 653/651 customer returns (reduces net demand)
      - receipt_qty = MT 101 goods received from supplier
      - net_demand  = issue_qty − return_qty
      - issue_value = LKR cost value of issued goods (for ABC classification)

    Returns one row per (material_9, year_month_str) with demand KPIs.
    """
    issues   = df[df["movement_class"] == "issue"].copy()
    returns  = df[df["movement_class"] == "return"].copy()
    receipts = df[df["movement_class"] == "receipt"].copy()

    def _agg(sub: pd.DataFrame, qty_col: str, val_col: str) -> pd.DataFrame:
        return (
            sub.groupby(["material_9", "year_month_str"])
            .agg(**{qty_col: ("qty", lambda x: abs(x).sum()), val_col: ("value_lkr", lambda x: abs(x).sum())})
            .reset_index()
        )

    iss = _agg(issues,   "issue_qty",   "issue_value_lkr")
    ret = _agg(returns,  "return_qty",  "return_value_lkr")
    rec = _agg(receipts, "receipt_qty", "receipt_value_lkr")

    # Combine on all months present across all movement types
    months = df[["material_9", "year_month_str"]].drop_duplicates()
    demand = months.merge(iss, on=["material_9", "year_month_str"], how="left")
    demand = demand.merge(ret, on=["material_9", "year_month_str"], how="left")
    demand = demand.merge(rec, on=["material_9", "year_month_str"], how="left")
    demand = demand.fillna(0.0)

    demand["net_demand"] = demand["issue_qty"] - demand["return_qty"]

    # Enrich with master description (first occurrence per SKU)
    desc_map = (
        df[["material_9", "description"]]
        .dropna(subset=["description"])
        .drop_duplicates("material_9")
        .set_index("material_9")["description"]
        .to_dict()
    )
    demand["description"] = demand["material_9"].map(desc_map).fillna("")

    demand = demand.sort_values(["material_9", "year_month_str"]).reset_index(drop=True)
    logger.info(f"Monthly demand built: {len(demand):,} rows ({demand['material_9'].nunique():,} SKUs)")
    return demand


def summary_kpis(df: pd.DataFrame) -> dict:
    """Headline KPIs for the stock movement report."""
    issues   = df[df["movement_class"] == "issue"]
    receipts = df[df["movement_class"] == "receipt"]
    returns  = df[df["movement_class"] == "return"]

    return {
        "Total Movement Lines":         len(df),
        "Unique SKUs":                  df["material_9"].nunique(),
        "Date Range (from)":            df["posting_date"].min().strftime("%Y-%m-%d"),
        "Date Range (to)":              df["posting_date"].max().strftime("%Y-%m-%d"),
        "Total Issue Lines (MT 601)":   len(issues),
        "Total Issue Qty":              abs(issues["qty"].sum()),
        "Total Issue Value (LKR)":      abs(issues["value_lkr"].sum()),
        "Total Receipt Lines (MT 101)": len(receipts),
        "Total Receipt Qty":            receipts["qty"].sum(),
        "Total Return Lines":           len(returns),
        "Total Return Qty":             abs(returns["qty"].sum()),
        "Return Rate %":                round(
            abs(returns["qty"].sum()) / max(abs(issues["qty"].sum()), 1) * 100, 2
        ),
    }


def top_movers(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Top N SKUs by total issue value (LKR).

    Business meaning: high-value movers are ABC 'A' candidates and should have
    tighter reorder policies and higher safety stock.
    """
    issues = df[df["movement_class"] == "issue"].copy()
    agg = (
        issues.groupby("material_9")
        .agg(
            description=("description", "first"),
            issue_lines=("qty", "count"),
            total_issue_qty=("qty", lambda x: abs(x).sum()),
            total_issue_value_lkr=("value_lkr", lambda x: abs(x).sum()),
        )
        .reset_index()
        .sort_values("total_issue_value_lkr", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    total_val = agg["total_issue_value_lkr"].sum()
    agg["value_share_%"] = (agg["total_issue_value_lkr"] / max(total_val, 1) * 100).round(2)
    return agg


def slow_movers(df: pd.DataFrame, min_months_silent: int = 12) -> pd.DataFrame:
    """SKUs with no outbound movement for ≥ min_months_silent months.

    Business meaning: slow/no movers tie up working capital and may warrant
    write-off, return to supplier, or reallocation. They are FSN 'N' (non-moving)
    candidates.
    """
    latest = df["posting_date"].max()
    cutoff = latest - pd.DateOffset(months=min_months_silent)

    issues = df[df["movement_class"] == "issue"]
    last_issue = (
        issues.groupby("material_9")["posting_date"]
        .max()
        .reset_index()
        .rename(columns={"posting_date": "last_issue_date"})
    )

    all_skus = df[["material_9", "description"]].drop_duplicates("material_9")
    merged = all_skus.merge(last_issue, on="material_9", how="left")
    merged["last_issue_date"] = pd.to_datetime(merged["last_issue_date"])
    silent = merged[
        merged["last_issue_date"].isna() | (merged["last_issue_date"] < cutoff)
    ].copy()
    silent["days_since_last_issue"] = (latest - silent["last_issue_date"]).dt.days
    silent = silent.sort_values("days_since_last_issue", ascending=False).reset_index(drop=True)
    logger.info(f"Slow movers (≥{min_months_silent} months silent): {len(silent):,} SKUs")
    return silent


def monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly aggregated issue qty, receipt qty and return rate across all SKUs."""
    issues   = df[df["movement_class"] == "issue"]
    receipts = df[df["movement_class"] == "receipt"]
    returns  = df[df["movement_class"] == "return"]

    iss_m = issues.groupby("year_month_str").agg(
        issue_qty=("qty", lambda x: abs(x).sum()),
        issue_value_lkr=("value_lkr", lambda x: abs(x).sum()),
        issue_lines=("qty", "count"),
    ).reset_index()

    rec_m = receipts.groupby("year_month_str").agg(
        receipt_qty=("qty", "sum"),
        receipt_value_lkr=("value_lkr", "sum"),
    ).reset_index()

    ret_m = returns.groupby("year_month_str").agg(
        return_qty=("qty", lambda x: abs(x).sum()),
    ).reset_index()

    trend = iss_m.merge(rec_m, on="year_month_str", how="outer")
    trend = trend.merge(ret_m, on="year_month_str", how="outer")
    trend = trend.fillna(0.0).sort_values("year_month_str").reset_index(drop=True)
    trend["return_rate_%"] = (
        trend["return_qty"] / trend["issue_qty"].clip(lower=1) * 100
    ).round(2)
    trend = trend.rename(columns={"year_month_str": "Month"})
    return trend


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

def _write_excel(
    kpis: dict,
    trend: pd.DataFrame,
    top: pd.DataFrame,
    slow: pd.DataFrame,
    demand: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white"})
        num = wb.add_format({"num_format": "#,##0"})
        lkr = wb.add_format({"num_format": "#,##0.00"})
        pct = wb.add_format({"num_format": "0.00%"})

        def write_sheet(df: pd.DataFrame, sheet: str, widths: list[int]) -> None:
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for i, (col, w) in enumerate(zip(df.columns, widths)):
                ws.set_column(i, i, w)
                ws.write(0, i, col, hdr)

        # Sheet 1 — Summary KPIs
        kpi_df = pd.DataFrame(
            [{"KPI": k, "Value": v} for k, v in kpis.items()]
        )
        write_sheet(kpi_df, "Summary KPIs", [40, 25])

        # Sheet 2 — Monthly Trend (with chart)
        write_sheet(trend, "Monthly Trend", [10, 14, 16, 12, 14, 16, 12, 10])
        ws = writer.sheets["Monthly Trend"]
        n_rows = len(trend) + 1
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Issue Qty",
            "categories": ["Monthly Trend", 1, 0, n_rows - 1, 0],
            "values":     ["Monthly Trend", 1, 1, n_rows - 1, 1],
            "fill":       {"color": "#1F4E79"},
        })
        chart.add_series({
            "name": "Receipt Qty",
            "categories": ["Monthly Trend", 1, 0, n_rows - 1, 0],
            "values":     ["Monthly Trend", 1, 3, n_rows - 1, 3],
            "fill":       {"color": "#2E75B6"},
        })
        chart.set_title({"name": "Monthly Issue vs Receipt (all SKUs)"})
        chart.set_size({"width": 720, "height": 300})
        ws.insert_chart("J2", chart)

        # Sheet 3 — Top Movers by Value
        write_sheet(top, "Top Movers", [20, 40, 12, 14, 18, 12])

        # Sheet 4 — Slow Movers
        write_sheet(
            slow[["material_9", "description", "last_issue_date", "days_since_last_issue"]],
            "Slow Movers",
            [20, 40, 14, 20],
        )

        # Sheet 5 — Monthly Demand (per SKU — for Stage 9 & 10)
        write_sheet(
            demand.head(50000),  # cap Excel sheet at 50K rows; full data is in parquet
            "Monthly Demand (sample)",
            [20, 10, 12, 14, 12, 14, 14, 40],
        )

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False, append: bool = False) -> None:
    """Stage 7 entry point: stock movement EDA + demand extraction.

    Args:
        refresh: force full recompute even if cached parquets exist.
        append:  if True, hash-dedup new rows from In_and_Out.xlsx against the
                 existing parquet rather than reprocessing from scratch. Ignored
                 when refresh=True (refresh always does a full load).
    """
    use_append = append and not refresh and _MOVEMENTS_PARQUET.exists()

    if not refresh and not append and _MOVEMENTS_PARQUET.exists() and _DEMAND_PARQUET.exists():
        logger.info("Stage 7 cached — skipping (use --refresh or --append for updates)")
        return

    logger.info(f"Stage 7: Stock Movement Analysis (mode={'append' if use_append else 'full'})")

    df = load_movements(append=use_append)
    df.to_parquet(_MOVEMENTS_PARQUET, index=False)
    logger.info(f"Stock movements saved → {_MOVEMENTS_PARQUET} ({len(df):,} rows)")

    demand = build_monthly_demand(df)
    demand.to_parquet(_DEMAND_PARQUET, index=False)
    logger.info(f"Monthly demand saved → {_DEMAND_PARQUET} ({len(demand):,} rows)")

    kpis  = summary_kpis(df)
    trend = monthly_trend(df)
    top   = top_movers(df, top_n=20)
    slow  = slow_movers(df, min_months_silent=12)

    _write_excel(kpis, trend, top, slow, demand)

    logger.info(
        f"Stage 7 complete | "
        f"SKUs: {kpis['Unique SKUs']:,} | "
        f"Issue lines: {kpis['Total Issue Lines (MT 601)']:,} | "
        f"Slow movers: {len(slow):,} | "
        f"Monthly demand rows: {len(demand):,}"
    )
