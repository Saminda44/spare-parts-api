"""Stage 8 — Spare parts EDA: per-SKU demand feature extraction.

Inputs:
  data/interim/monthly_demand.parquet   — per-SKU monthly demand (Stage 7 output)
  data/interim/part_master.parquet      — canonical part master (Stage 6 output)

Outputs:
  data/interim/spare_parts_features.parquet  — per-SKU demand features (→ Stage 9)
  data/outputs/stage08_spare_parts_eda.xlsx  — 5-sheet EDA report
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_DEMAND_PARQUET = DATA_INTERIM / "monthly_demand.parquet"
_PART_MASTER = DATA_INTERIM / "part_master.parquet"
_FEATURES_PARQUET = DATA_INTERIM / "spare_parts_features.parquet"
_OUTPUT_XLSX = DATA_OUTPUTS / "stage08_spare_parts_eda.xlsx"

# Demand intermittency classification thresholds
# Business meaning: CV ≥ 0.5 indicates irregular demand; p_zero ≥ 0.7 means
# the SKU is non-moving most of the time → qualifies as Slow or Non-moving in FSN.
_CV_HIGH_THRESHOLD = 0.5
_P_ZERO_SLOW_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Core feature extraction
# ---------------------------------------------------------------------------

def compute_sku_features(demand: pd.DataFrame) -> pd.DataFrame:
    """Compute per-SKU demand statistics from the monthly demand table.

    Business meaning:
      - avg_monthly_demand: expected units per month → drives ROL/ROQ sizing
      - cv: coefficient of variation of non-zero demand → drives safety stock multiplier
      - p_zero: fraction of months with zero demand → FSN classification driver
      - active_months: months with demand > 0 (out of total observed months)
      - total_months: calendar span of the observation window (same for all SKUs)
      - total_issue_qty: lifetime units issued (magnitude signal for ABC)
      - total_issue_value_lkr: lifetime LKR value issued (ABC primary signal)
      - last_issue_date: most recent issue date (recency signal for FSN)
      - demand_category: intermittency label based on cv and p_zero

    Args:
        demand: monthly_demand DataFrame with columns
            [material_9, year_month_str, issue_qty, issue_value_lkr,
             return_qty, net_demand, description]

    Returns:
        One row per material_9 with demand KPIs.
    """
    # Total observation window (calendar months across all SKUs)
    all_months = demand["year_month_str"].nunique()

    # Aggregate per SKU
    agg = (
        demand.groupby("material_9")
        .agg(
            description=("description", "first"),
            total_issue_qty=("issue_qty", "sum"),
            total_issue_value_lkr=("issue_value_lkr", "sum"),
            total_return_qty=("return_qty", "sum"),
            total_net_demand=("net_demand", "sum"),
            observed_months=("year_month_str", "count"),
            active_months=("issue_qty", lambda x: (x > 0).sum()),
        )
        .reset_index()
    )

    # Months with demand > 0 in the full observation window
    agg["total_months"] = all_months
    agg["p_zero"] = ((agg["total_months"] - agg["active_months"]) / agg["total_months"].clip(lower=1)).round(4)
    agg["avg_monthly_demand"] = (agg["total_issue_qty"] / agg["total_months"].clip(lower=1)).round(4)

    # CV on monthly demand (non-zero months only to avoid deflation by structural zeros)
    cv_map = _compute_cv(demand)
    agg["cv"] = agg["material_9"].map(cv_map).fillna(0.0).round(4)

    # Last issue date
    last_issue_map = _last_issue_date(demand)
    agg["last_issue_date"] = agg["material_9"].map(last_issue_map)
    agg["last_issue_date"] = pd.to_datetime(agg["last_issue_date"])

    # Demand category (intermittency label)
    agg["demand_category"] = agg.apply(_categorise_demand, axis=1)

    col_order = [
        "material_9", "description",
        "total_months", "active_months", "p_zero",
        "avg_monthly_demand", "cv",
        "total_issue_qty", "total_issue_value_lkr",
        "total_return_qty", "total_net_demand",
        "last_issue_date", "demand_category",
    ]
    agg = agg[col_order].sort_values("total_issue_value_lkr", ascending=False).reset_index(drop=True)

    logger.info(
        f"SKU features computed: {len(agg):,} SKUs | "
        f"active (p_zero<0.7): {(agg['p_zero'] < _P_ZERO_SLOW_THRESHOLD).sum():,} | "
        f"non-moving (p_zero=1): {(agg['p_zero'] == 1.0).sum():,}"
    )
    return agg


def _compute_cv(demand: pd.DataFrame) -> dict[str, float]:
    """Return CV of non-zero monthly issue_qty per SKU.

    Business meaning: CV on non-zero months avoids penalising normally-ordered
    parts that simply have months they weren't ordered in. A CV > 0.5 signals
    erratic/lumpy demand requiring higher safety stock.
    """
    cv_rows: dict[str, float] = {}
    for sku, grp in demand.groupby("material_9"):
        non_zero = grp.loc[grp["issue_qty"] > 0, "issue_qty"]
        if len(non_zero) < 2:
            cv_rows[str(sku)] = 0.0
        else:
            mean = non_zero.mean()
            std = non_zero.std(ddof=1)
            cv_rows[str(sku)] = float(std / mean) if mean > 0 else 0.0
    return cv_rows


def _last_issue_date(demand: pd.DataFrame) -> dict[str, pd.Timestamp | None]:
    """Return the most recent year_month_str (as Timestamp) with issue_qty > 0 per SKU."""
    issued = demand[demand["issue_qty"] > 0].copy()
    if issued.empty:
        return {}
    issued["_ts"] = pd.to_datetime(issued["year_month_str"] + "-01")
    last = issued.groupby("material_9")["_ts"].max()
    return last.to_dict()


def _categorise_demand(row: pd.Series) -> str:
    """Label demand pattern for each SKU.

    Categories:
      smooth      — low CV, regularly ordered (good forecaster)
      erratic     — high CV but active (orders when needed, hard to predict exactly)
      intermittent — low CV but many zero months (sporadic but predictable size)
      lumpy       — high CV AND many zero months (worst case: sporadic + unpredictable)
      non_moving  — never issued or p_zero == 1.0
    """
    if row["active_months"] == 0 or row["p_zero"] == 1.0:
        return "non_moving"
    high_cv = row["cv"] >= _CV_HIGH_THRESHOLD
    many_zeros = row["p_zero"] >= _P_ZERO_SLOW_THRESHOLD
    if high_cv and many_zeros:
        return "lumpy"
    if high_cv:
        return "erratic"
    if many_zeros:
        return "intermittent"
    return "smooth"


# ---------------------------------------------------------------------------
# SSOP enrichment
# ---------------------------------------------------------------------------

def enrich_with_ssop(features: pd.DataFrame) -> pd.DataFrame:
    """Flag each SKU as in_ssop (appears in the canonical part master)."""
    if not _PART_MASTER.exists():
        logger.warning("part_master.parquet not found — in_ssop flag skipped")
        features["in_ssop"] = False
        return features

    master = pd.read_parquet(_PART_MASTER)[["part_number"]].rename(columns={"part_number": "material_9"})
    ssop_set = set(master["material_9"])
    features = features.copy()
    features["in_ssop"] = features["material_9"].isin(ssop_set)
    in_ssop_n = features["in_ssop"].sum()
    logger.info(f"SSOP coverage: {in_ssop_n:,}/{len(features):,} SKUs in part master")
    return features


# ---------------------------------------------------------------------------
# Report summaries
# ---------------------------------------------------------------------------

def summary_kpis(features: pd.DataFrame) -> dict:
    """Headline KPIs for the spare parts EDA report."""
    active = features[features["active_months"] > 0]
    return {
        "Total SKUs (with movement history)": len(features),
        "SKUs with ≥1 Issue": int((features["active_months"] > 0).sum()),
        "Non-moving SKUs (p_zero=1)": int((features["p_zero"] == 1.0).sum()),
        "SKUs in SSOP/Part Master": int(features["in_ssop"].sum()) if "in_ssop" in features else "N/A",
        "Total Observation Months": int(features["total_months"].iloc[0]) if len(features) > 0 else 0,
        "Median Active Months per SKU": float(features["active_months"].median()),
        "Median CV (non-zero months)": float(features["cv"].median()),
        "Median p_zero": float(features["p_zero"].median()),
        "Total Issue Qty (all SKUs)": float(features["total_issue_qty"].sum()),
        "Total Issue Value LKR": float(features["total_issue_value_lkr"].sum()),
        "Smooth Demand SKUs": int((features["demand_category"] == "smooth").sum()),
        "Erratic Demand SKUs": int((features["demand_category"] == "erratic").sum()),
        "Intermittent Demand SKUs": int((features["demand_category"] == "intermittent").sum()),
        "Lumpy Demand SKUs": int((features["demand_category"] == "lumpy").sum()),
        "Non-moving SKUs": int((features["demand_category"] == "non_moving").sum()),
    }


def demand_pattern_distribution(features: pd.DataFrame) -> pd.DataFrame:
    """Count and share of SKUs per demand category."""
    counts = features["demand_category"].value_counts().reset_index()
    counts.columns = ["demand_category", "sku_count"]
    total = counts["sku_count"].sum()
    counts["share_%"] = (counts["sku_count"] / total * 100).round(2)
    return counts


def top_skus_by_value(features: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Top N SKUs by total issue value LKR."""
    top = features.nlargest(top_n, "total_issue_value_lkr").copy()
    total_value = features["total_issue_value_lkr"].sum()
    top["cumulative_value_share_%"] = (
        top["total_issue_value_lkr"].cumsum() / max(total_value, 1) * 100
    ).round(2)
    return top[[
        "material_9", "description", "demand_category",
        "total_issue_qty", "total_issue_value_lkr",
        "active_months", "avg_monthly_demand", "cv", "p_zero",
        "cumulative_value_share_%",
    ]]


def intermittent_skus(features: pd.DataFrame) -> pd.DataFrame:
    """SKUs with p_zero ≥ 0.7 and at least one issue (lumpy or intermittent)."""
    mask = (features["p_zero"] >= _P_ZERO_SLOW_THRESHOLD) & (features["active_months"] > 0)
    sub = features[mask].copy()
    sub = sub.sort_values("total_issue_value_lkr", ascending=False).reset_index(drop=True)
    return sub[[
        "material_9", "description", "demand_category",
        "active_months", "p_zero", "cv", "avg_monthly_demand",
        "total_issue_value_lkr", "last_issue_date",
    ]]


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

def _write_excel(
    kpis: dict,
    pattern_dist: pd.DataFrame,
    top: pd.DataFrame,
    intermittent: pd.DataFrame,
    features: pd.DataFrame,
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
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        write_sheet(kpi_df, "Summary KPIs", [45, 25])

        # Sheet 2 — Demand Pattern Distribution (with pie chart)
        write_sheet(pattern_dist, "Demand Patterns", [22, 12, 12])
        ws = writer.sheets["Demand Patterns"]
        n = len(pattern_dist) + 1
        chart = wb.add_chart({"type": "pie"})
        chart.add_series({
            "name": "SKU Count by Demand Pattern",
            "categories": ["Demand Patterns", 1, 0, n - 1, 0],
            "values":     ["Demand Patterns", 1, 1, n - 1, 1],
            "data_labels": {"percentage": True, "category": True},
        })
        chart.set_title({"name": "Demand Category Distribution"})
        chart.set_size({"width": 480, "height": 320})
        ws.insert_chart("E2", chart)

        # Sheet 3 — Top SKUs by Issue Value
        write_sheet(top, "Top SKUs by Value", [20, 40, 16, 14, 18, 14, 18, 10, 10, 20])

        # Sheet 4 — Intermittent / Lumpy SKUs
        write_sheet(
            intermittent,
            "Intermittent SKUs",
            [20, 40, 16, 14, 10, 10, 18, 18, 14],
        )

        # Sheet 5 — Full Feature Table (capped at 50K)
        cols = [
            "material_9", "description", "demand_category",
            "total_months", "active_months", "p_zero",
            "avg_monthly_demand", "cv",
            "total_issue_qty", "total_issue_value_lkr",
            "last_issue_date",
        ]
        if "in_ssop" in features.columns:
            cols.append("in_ssop")
        write_sheet(
            features[cols].head(50_000),
            "All SKU Features",
            [20, 40, 16, 12, 14, 10, 18, 10, 14, 20, 14, 10],
        )

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 8 entry point: spare parts EDA and demand feature extraction."""
    if not refresh and _FEATURES_PARQUET.exists():
        logger.info("Stage 8 cached — skipping (use --refresh to force)")
        return

    logger.info("Stage 8: Spare Parts EDA")

    if not _DEMAND_PARQUET.exists():
        raise FileNotFoundError(
            f"monthly_demand.parquet not found at {_DEMAND_PARQUET}. "
            "Run Stage 7 first."
        )

    demand = pd.read_parquet(_DEMAND_PARQUET)
    logger.info(f"Monthly demand loaded: {len(demand):,} rows | {demand['material_9'].nunique():,} SKUs")

    features = compute_sku_features(demand)
    features = enrich_with_ssop(features)

    features.to_parquet(_FEATURES_PARQUET, index=False)
    logger.info(f"Spare parts features saved → {_FEATURES_PARQUET} ({len(features):,} rows)")

    kpis = summary_kpis(features)
    pattern_dist = demand_pattern_distribution(features)
    top = top_skus_by_value(features, top_n=20)
    intermittent = intermittent_skus(features)

    _write_excel(kpis, pattern_dist, top, intermittent, features)

    logger.info(
        f"Stage 8 complete | "
        f"SKUs: {len(features):,} | "
        f"Active: {kpis['SKUs with ≥1 Issue']:,} | "
        f"Non-moving: {kpis['Non-moving SKUs (p_zero=1)']:,} | "
        f"Lumpy: {kpis['Lumpy Demand SKUs']:,}"
    )
