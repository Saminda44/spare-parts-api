"""Stage 11 — Stock Tracker: current stock position per SKU.

Inputs:
  data/raw/current stock.xlsx            — SAP stock snapshot (primary source)
  data/interim/stock_movements.parquet   — full movement history (Stage 7, audit cols)
  data/interim/monthly_demand.parquet    — per-SKU monthly demand (Stage 7)
  data/interim/abc_xyz_fsn.parquet       — per-SKU classification (Stage 9)
  data/interim/demand_forecast.parquet   — per-SKU 3-month forecasts (Stage 10)

Outputs:
  data/interim/stock_tracker.parquet        — per-SKU stock position (→ Stage 12)
  data/outputs/stage11_stock_tracker.xlsx   — 6-sheet Excel report

When data/raw/current stock.xlsx is present it is used as the authoritative
source of stock_on_hand and stock_value_lkr (SAP snapshot is more accurate than
cumulative-movement reconstruction). Damage, GR-unavailable, and internal
transfer locations are excluded from the snapshot. Only parts that appear in the
part master (by material number first, description fallback) are included.

Movement history (total_receipts, total_issues, last_movement_date) is always
sourced from stock_movements.parquet and merged in on top of the snapshot.

When the snapshot file is absent the pipeline falls back to the original
cumulative-movement approach.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from src.config.constants import LEAD_TIME_DAYS
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

_MOVEMENTS_PARQUET = DATA_INTERIM / "stock_movements.parquet"
_DEMAND_PARQUET = DATA_INTERIM / "monthly_demand.parquet"
_ABC_XYZ_PARQUET = DATA_INTERIM / "abc_xyz_fsn.parquet"
_FORECAST_PARQUET = DATA_INTERIM / "demand_forecast.parquet"
_TRACKER_PARQUET = DATA_INTERIM / "stock_tracker.parquet"
_OUTPUT_XLSX = DATA_OUTPUTS / "stage11_stock_tracker.xlsx"
_CURRENT_STOCK_RAW = DATA_RAW / "current stock.xlsx"
_PART_MASTER_PATH = DATA_INTERIM / "part_master.parquet"

# Business thresholds
_LEAD_TIME_MONTHS: int = LEAD_TIME_DAYS // 30  # 3 months
_EXCESS_MONTHS: int = 6  # coverage above this = excess
_CRITICAL_MONTHS: float = 1.0  # coverage below this = critical

# Movement classes that represent internal plant-to-plant transfers — excluded
# from stock position because they net to zero across the whole distribution centre
# and double-counting them would distort on-hand balances.
_TRANSFER_CLASSES: frozenset[str] = frozenset({"transfer"})

# Storage location descriptions in current stock.xlsx that represent non-usable
# or in-transit stock and must be excluded from on-hand balances.
_EXCLUDED_LOCATIONS: frozenset[str] = frozenset(
    {
        "Description",  # stray header row
        "Damage",
        "GR Unavailiable",  # intentional: matches the typo present in the SAP export
        "0020",
        "0030",
    }
)


# ---------------------------------------------------------------------------
# SAP snapshot ingestion (current stock.xlsx)
# ---------------------------------------------------------------------------


def load_current_stock_snapshot() -> pd.DataFrame:
    """Load the SAP current-stock snapshot and derive stock_on_hand per part-master SKU.

    Business meaning: the SAP snapshot is the authoritative source of physical
    inventory. Movement-based reconstruction can drift over time if the data
    window is incomplete. This function replaces the movement-computed
    stock_on_hand whenever the snapshot file is available.

    Exclusions applied to the Description (storage location) column:
      - "Damage"          : physically damaged stock, not available for sale
      - "GR Unavailiable" : goods-receipt blocked stock
      - "0020" / "0030"  : location codes with no valid label (treated as above)
      - "Description"     : stray header row from the SAP export

    Part master filter — exact part-number match only:
      Material column must match part_master.part_number exactly.
      Rows with no part-number match are excluded regardless of description.
      When matched, the part master's description (not the stock file's Material
      Description) is used as the canonical description in Inventory Status.

    Returns:
        DataFrame with columns [material_9, description, stock_on_hand, stock_value_lkr].
        Empty DataFrame if the snapshot file or part master is missing.
    """
    if not _CURRENT_STOCK_RAW.exists():
        logger.info("current stock.xlsx not found — snapshot skipped")
        return pd.DataFrame()
    if not _PART_MASTER_PATH.exists():
        logger.warning(
            "part_master.parquet not found — cannot apply part master filter; snapshot skipped"
        )
        return pd.DataFrame()

    logger.info(f"Loading current stock snapshot from {_CURRENT_STOCK_RAW}")
    raw = pd.read_excel(_CURRENT_STOCK_RAW, engine="openpyxl")
    n_raw = len(raw)
    logger.info(f"Snapshot: {n_raw:,} rows (all locations)")

    # 1. Exclude non-usable storage locations
    raw["_desc"] = raw["Description"].astype(str).str.strip()
    filtered = raw[~raw["_desc"].isin(_EXCLUDED_LOCATIONS)].copy()
    n_dropped_loc = n_raw - len(filtered)
    logger.info(
        f"Location filter: dropped {n_dropped_loc:,} rows "
        f"(Damage / GR-unavailable / internal) → {len(filtered):,} remaining"
    )

    if filtered.empty:
        logger.warning("Snapshot empty after location filter")
        return pd.DataFrame()

    # 2. Normalise material code and parse quantities / values
    filtered["mat_clean"] = filtered["Material"].astype(str).str.strip()

    # Unrestricted qty: typically integer but guard against European decimal
    filtered["_unrestricted"] = (
        filtered["Unrestricted"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )

    # Value Unrestricted: European format "1.234,56" → 1234.56
    filtered["_value"] = (
        filtered["Value Unrestricted"]
        .astype(str)
        .str.replace(".", "", regex=False)  # strip thousands separator
        .str.replace(",", ".", regex=False)  # decimal comma → period
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )

    # 3. Load part master — part_number and description only
    pm = pd.read_parquet(_PART_MASTER_PATH)[["part_number", "description"]].copy()
    pm["part_number"] = pm["part_number"].astype(str).str.strip()
    pm["description"] = pm["description"].astype(str).str.strip()

    # 4. Exact part-number match only — no description fallback
    pm_part_set: set[str] = set(pm["part_number"])
    filtered["matched_part"] = filtered["mat_clean"].where(filtered["mat_clean"].isin(pm_part_set))

    n_matched = int(filtered["matched_part"].notna().sum())
    n_no_match = int(filtered["matched_part"].isna().sum())
    logger.info(
        f"Part master join (exact part-number): {n_matched:,} rows matched "
        f"| {n_no_match:,} excluded (not in master)"
    )

    # 5. Inner join — drop rows with no part-number match
    matched = filtered[filtered["matched_part"].notna()].copy()
    if matched.empty:
        logger.warning("No rows survived part master filter — check material codes")
        return pd.DataFrame()

    # 6. Aggregate per part_number; bring in part master description
    snap = (
        matched.groupby("matched_part", sort=False)
        .agg(
            stock_on_hand=("_unrestricted", "sum"),
            stock_value_lkr=("_value", "sum"),
        )
        .reset_index()
        .rename(columns={"matched_part": "material_9"})
    )
    snap["stock_on_hand"] = snap["stock_on_hand"].clip(lower=0.0)
    snap["stock_value_lkr"] = snap["stock_value_lkr"].clip(lower=0.0)

    # Attach part master description so it is used as-is in Inventory Status
    pm_desc_map = pm.set_index("part_number")["description"].to_dict()
    snap["description"] = snap["material_9"].map(pm_desc_map)

    logger.info(
        f"Snapshot aggregated: {len(snap):,} unique SKUs "
        f"| with stock: {(snap['stock_on_hand'] > 0).sum():,}"
    )
    return snap


# ---------------------------------------------------------------------------
# Stock position
# ---------------------------------------------------------------------------


def compute_stock_position(movements: pd.DataFrame) -> pd.DataFrame:
    """Derive current stock on hand per SKU from cumulative net movements.

    Business meaning: in the absence of a SAP stock snapshot, the running sum
    of all signed movement quantities (inbound positive, outbound negative)
    gives the net stock change since the first recorded movement. Transfer
    movements are excluded because they redistribute stock between plants
    without changing the total held by the distributor.

    Negative balances are clipped to zero — they indicate the opening stock
    before our data window was non-zero; we cannot know the true balance
    for those SKUs.

    Args:
        movements: stock_movements.parquet as loaded from Stage 7.

    Returns:
        DataFrame with columns:
          material_9, stock_on_hand, stock_value_lkr, last_movement_date,
          total_receipts, total_issues, total_returns.
    """
    # Exclude internal transfers — they net to zero across the DC
    stock_mv = movements[~movements["movement_class"].isin(_TRANSFER_CLASSES)].copy()

    # Tag individual movement types for audit columns
    receipts = movements[movements["movement_class"].isin({"receipt", "issue_rev"})].copy()
    issues = movements[movements["movement_class"].isin({"issue"})].copy()
    returns = movements[movements["movement_class"].isin({"return"})].copy()

    def _abs_agg(sub: pd.DataFrame, col: str) -> pd.Series:
        return sub.groupby("material_9")["qty"].apply(lambda x: abs(x).sum()).rename(col)

    rec_agg = _abs_agg(receipts, "total_receipts")
    iss_agg = _abs_agg(issues, "total_issues")
    ret_agg = _abs_agg(returns, "total_returns")

    # Net stock position: sum of signed qty across all non-transfer movements
    net = (
        stock_mv.groupby("material_9")
        .agg(
            stock_on_hand=("qty", "sum"),
            stock_value_lkr=("value_lkr", "sum"),
            last_movement_date=("posting_date", "max"),
        )
        .reset_index()
    )

    # Clip negative balances — can't have negative physical stock
    n_negative = (net["stock_on_hand"] < 0).sum()
    if n_negative > 0:
        logger.warning(
            f"{n_negative:,} SKUs have negative computed stock "
            f"(opening balance not in data) — clipped to 0"
        )
    net["stock_on_hand"] = net["stock_on_hand"].clip(lower=0.0)
    net["stock_value_lkr"] = net["stock_value_lkr"].clip(lower=0.0)

    net = net.merge(rec_agg, on="material_9", how="left")
    net = net.merge(iss_agg, on="material_9", how="left")
    net = net.merge(ret_agg, on="material_9", how="left")
    for col in ("total_receipts", "total_issues", "total_returns"):
        net[col] = net[col].fillna(0.0)

    logger.info(
        f"Stock positions computed: {len(net):,} SKUs | "
        f"zero-stock: {(net['stock_on_hand'] == 0).sum():,}"
    )
    return net


# ---------------------------------------------------------------------------
# Coverage & risk classification
# ---------------------------------------------------------------------------


def compute_coverage(
    stock_pos: pd.DataFrame,
    classified: pd.DataFrame,
    forecast: pd.DataFrame,
) -> pd.DataFrame:
    """Attach classification, forecast, and coverage metrics to stock positions.

    Coverage months = stock_on_hand / avg_monthly_demand.
    For non-moving SKUs (avg_monthly_demand == 0) with stock > 0, coverage is
    set to a sentinel value of 999 (interpret as "indefinite").

    Args:
        stock_pos: output of compute_stock_position().
        classified: abc_xyz_fsn.parquet (Stage 9).
        forecast: demand_forecast.parquet (Stage 10).

    Returns:
        stock_pos enriched with classification and coverage columns.
    """
    cls_cols = [
        "material_9",
        "description",
        "abc",
        "xyz",
        "fsn",
        "abc_xyz_fsn",
        "policy_tier",
        "avg_monthly_demand",
        "cv",
        "active_months",
        "total_issue_value_lkr",
    ]
    fc_cols = [
        "material_9",
        "forecast_lt",
        "forecast_m1",
        "forecast_m2",
        "forecast_m3",
        "demand_std_monthly",
        "demand_std_lt",
        "method",
    ]
    cls_sub = classified[[c for c in cls_cols if c in classified.columns]].copy()
    fc_sub = forecast[[c for c in fc_cols if c in forecast.columns]].copy()

    # When the snapshot already supplies the part master description, drop it
    # from cls_sub so the snapshot value is preserved rather than overwritten.
    if "description" in stock_pos.columns and stock_pos["description"].notna().any():
        cls_sub = cls_sub.drop(columns=["description"], errors="ignore")

    df = stock_pos.merge(cls_sub, on="material_9", how="left")
    df = df.merge(fc_sub, on="material_9", how="left")

    df["avg_monthly_demand"] = df["avg_monthly_demand"].fillna(0.0)
    df["forecast_lt"] = df["forecast_lt"].fillna(0.0)
    df["demand_std_lt"] = df["demand_std_lt"].fillna(0.0)

    # Coverage = months of supply on hand
    demand_for_cov = df["avg_monthly_demand"].clip(lower=1e-9)
    df["coverage_months"] = df["stock_on_hand"] / demand_for_cov

    # SKUs with zero demand and zero stock → 0 coverage; zero demand but some stock → 999
    zero_demand = df["avg_monthly_demand"] == 0.0
    df.loc[zero_demand & (df["stock_on_hand"] == 0.0), "coverage_months"] = 0.0
    df.loc[zero_demand & (df["stock_on_hand"] > 0.0), "coverage_months"] = 999.0

    df["days_of_stock"] = (df["coverage_months"] * 30.0).round(0).astype(int)

    return df


def classify_stock_status(df: pd.DataFrame) -> pd.DataFrame:
    """Assign a stock status label based on coverage months.

    Business meaning:
      stockout  — no stock on hand; immediate service risk
      critical  — < 1 month; will run out before an emergency order arrives
      low       — 1–3 months; below the 3-month import lead time; needs urgent replenishment
      ok        — 3–6 months; healthy buffer covering the full lead time
      excess    — > 6 months; capital tied up; consider deferring next order

    Args:
        df: output of compute_coverage().

    Returns:
        df with stock_status column added.
    """
    conditions = [
        df["stock_on_hand"] <= 0.0,
        df["coverage_months"] < _CRITICAL_MONTHS,
        df["coverage_months"] < _LEAD_TIME_MONTHS,
        df["coverage_months"] <= _EXCESS_MONTHS,
    ]
    choices = ["stockout", "critical", "low", "ok"]
    df = df.copy()
    df["stock_status"] = np.select(conditions, choices, default="excess")
    return df


# ---------------------------------------------------------------------------
# Report slices
# ---------------------------------------------------------------------------


def risk_report(df: pd.DataFrame) -> pd.DataFrame:
    """SKUs at replenishment risk: coverage below lead time (< 3 months).

    Business meaning: these SKUs need a purchase order in the current ordering
    cycle to avoid a stockout during the 3-month India import lead time.
    """
    at_risk = df[
        (df["stock_status"].isin({"stockout", "critical", "low"}))
        & (df["avg_monthly_demand"] > 0.0)
    ].copy()
    at_risk = at_risk.sort_values(
        ["abc", "coverage_months"],
        ascending=[True, True],
    ).reset_index(drop=True)
    logger.info(f"At-risk SKUs (coverage < {_LEAD_TIME_MONTHS} months): {len(at_risk):,}")
    return at_risk


def excess_report(df: pd.DataFrame) -> pd.DataFrame:
    """SKUs with excess stock: coverage above 6 months.

    Business meaning: slow-moving overstock ties up working capital. These
    items should have order quantity reduced or deferred in the next cycle.
    """
    excess = df[(df["stock_status"] == "excess") & (df["stock_on_hand"] > 0.0)].copy()
    excess = excess.sort_values("coverage_months", ascending=False).reset_index(drop=True)
    logger.info(f"Excess-stock SKUs (coverage > {_EXCESS_MONTHS} months): {len(excess):,}")
    return excess


def summary_kpis(df: pd.DataFrame) -> dict[str, object]:
    """Headline KPIs for the stock tracker dashboard."""
    status_counts = df["stock_status"].value_counts().to_dict()
    active = df[df["avg_monthly_demand"] > 0.0]
    return {
        "Total SKUs": len(df),
        "SKUs with Stock": int((df["stock_on_hand"] > 0).sum()),
        "Stockout SKUs": int(status_counts.get("stockout", 0)),
        "Critical SKUs (< 1 month)": int(status_counts.get("critical", 0)),
        "Low Stock SKUs (< 3 months)": int(status_counts.get("low", 0)),
        "OK SKUs (3–6 months)": int(status_counts.get("ok", 0)),
        "Excess SKUs (> 6 months)": int(status_counts.get("excess", 0)),
        "Total Stock Value (LKR)": float(df["stock_value_lkr"].sum()),
        "Active SKUs": int(len(active)),
        "Avg Coverage (active, months)": float(
            active["coverage_months"].replace(999.0, np.nan).mean()
        )
        if len(active) > 0
        else 0.0,
        "At-risk SKUs (< lead time)": int(
            df["stock_status"].isin({"stockout", "critical", "low"}).sum()
        ),
    }


def top_stockout_risk(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Top N active SKUs with lowest coverage, sorted by ABC tier then coverage."""
    active_at_risk = df[
        (df["avg_monthly_demand"] > 0.0)
        & (df["stock_status"].isin({"stockout", "critical", "low"}))
    ].copy()
    return (
        active_at_risk.sort_values(["abc", "coverage_months"], ascending=[True, True])
        .head(top_n)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------


def _write_excel(
    df: pd.DataFrame,
    kpis: dict[str, object],
    risk: pd.DataFrame,
    excess: pd.DataFrame,
    top_risk: pd.DataFrame,
) -> None:
    """Write a 6-sheet Excel report."""
    report_cols = [
        "material_9",
        "description",
        "abc",
        "xyz",
        "fsn",
        "abc_xyz_fsn",
        "policy_tier",
        "stock_on_hand",
        "stock_value_lkr",
        "coverage_months",
        "days_of_stock",
        "stock_status",
        "avg_monthly_demand",
        "forecast_lt",
        "forecast_m1",
        "forecast_m2",
        "forecast_m3",
        "demand_std_monthly",
        "demand_std_lt",
        "method",
        "total_receipts",
        "total_issues",
        "total_returns",
        "last_movement_date",
        "active_months",
    ]
    summary_cols = [c for c in report_cols if c in df.columns]

    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr_fmt = wb.add_format(
            {"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1}
        )
        wb.add_format({"num_format": "#,##0.0"})
        wb.add_format({"num_format": "#,##0"})
        wb.add_format({"num_format": "0.0%"})

        def _write_sheet(data: pd.DataFrame, name: str) -> None:
            data.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.set_row(0, 18, hdr_fmt)
            ws.set_column(0, len(data.columns) - 1, 18)

        # Sheet 1: KPI summary
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        kpi_df.to_excel(writer, sheet_name="Summary KPIs", index=False)
        ws = writer.sheets["Summary KPIs"]
        ws.set_row(0, 18, hdr_fmt)
        ws.set_column(0, 0, 40)
        ws.set_column(1, 1, 25)

        # Sheet 2: Full stock positions
        _write_sheet(df[summary_cols], "All SKUs")

        # Sheet 3: At-risk SKUs
        risk_out = [c for c in summary_cols if c in risk.columns]
        _write_sheet(
            risk[risk_out] if not risk.empty else pd.DataFrame(columns=risk_out), "At-Risk SKUs"
        )

        # Sheet 4: Excess stock
        excess_out = [c for c in summary_cols if c in excess.columns]
        _write_sheet(
            excess[excess_out] if not excess.empty else pd.DataFrame(columns=excess_out),
            "Excess Stock",
        )

        # Sheet 5: Top stockout risk
        top_out = [c for c in summary_cols if c in top_risk.columns]
        _write_sheet(
            top_risk[top_out] if not top_risk.empty else pd.DataFrame(columns=top_out),
            "Top Stockout Risk",
        )

        # Sheet 6: Status distribution
        status_dist = (
            df.groupby(["policy_tier", "stock_status"], observed=True)
            .agg(sku_count=("material_9", "count"), total_stock=("stock_on_hand", "sum"))
            .reset_index()
            .sort_values(["policy_tier", "stock_status"])
        )
        _write_sheet(status_dist, "Status Distribution")

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run(refresh: bool = False) -> None:
    """Stage 11: Stock Tracker — compute and report current stock positions."""
    if not refresh and _TRACKER_PARQUET.exists():
        logger.info("stock_tracker.parquet exists and refresh=False — skipping Stage 11")
        return

    logger.info("Stage 11: Stock Tracker")

    # Load inputs
    movements = pd.read_parquet(_MOVEMENTS_PARQUET)
    classified = pd.read_parquet(_ABC_XYZ_PARQUET)
    forecast = pd.read_parquet(_FORECAST_PARQUET)

    logger.info(
        f"Movements: {len(movements):,} rows | "
        f"Classified: {len(classified):,} SKUs | "
        f"Forecasts: {len(forecast):,} SKUs"
    )

    # Movement-based aggregates — always computed for audit columns
    movement_pos = compute_stock_position(movements)
    _audit_cols = [
        "material_9",
        "last_movement_date",
        "total_receipts",
        "total_issues",
        "total_returns",
    ]
    audit = movement_pos[[c for c in _audit_cols if c in movement_pos.columns]].copy()

    # Use SAP snapshot as the authoritative source for stock_on_hand / stock_value_lkr
    snap = load_current_stock_snapshot()
    if not snap.empty:
        # Snapshot is the leading frame; movement audit columns are merged in
        stock_pos = snap.merge(audit, on="material_9", how="left")
        for col in ("total_receipts", "total_issues", "total_returns"):
            if col in stock_pos.columns:
                stock_pos[col] = stock_pos[col].fillna(0.0)
        logger.info(
            f"Stock source: SAP snapshot ({len(snap):,} SKUs) "
            f"enriched with movement audit columns"
        )
    else:
        logger.info("Stock source: movement-based cumulative (snapshot unavailable)")
        stock_pos = movement_pos

    df = compute_coverage(stock_pos, classified, forecast)
    df = classify_stock_status(df)

    # Report slices
    risk = risk_report(df)
    excess = excess_report(df)
    kpis = summary_kpis(df)
    top_risk = top_stockout_risk(df)

    # Save parquet
    df.to_parquet(_TRACKER_PARQUET, index=False)
    logger.info(f"Stock tracker saved → {_TRACKER_PARQUET} ({len(df):,} rows)")

    # Excel report
    _write_excel(df, kpis, risk, excess, top_risk)

    status_dist = df["stock_status"].value_counts().to_dict()
    logger.info(
        "Stage 11 complete | "
        + " | ".join(f"{s}:{n:,}" for s, n in sorted(status_dist.items()))
        + f" | At-risk: {len(risk):,} | Excess: {len(excess):,}"
    )
