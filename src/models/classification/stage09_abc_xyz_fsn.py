"""Stage 9 — ABC-XYZ-FSN inventory classification.

Inputs:
  data/interim/spare_parts_features.parquet  — per-SKU demand features (Stage 8 output)

Outputs:
  data/interim/abc_xyz_fsn.parquet           — per-SKU classification table (→ Stages 10 & 12)
  data/outputs/stage09_abc_xyz_fsn.xlsx      — 6-sheet classification report
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_FEATURES_PARQUET = DATA_INTERIM / "spare_parts_features.parquet"
_ABC_XYZ_FSN_PARQUET = DATA_INTERIM / "abc_xyz_fsn.parquet"
_OUTPUT_XLSX = DATA_OUTPUTS / "stage09_abc_xyz_fsn.xlsx"

# ABC cumulative-value thresholds (standard Pareto analysis)
# Business meaning: A-items are the vital few that drive 80% of issue value;
# B-items are the middle band; C-items are the trivial many with minimal value.
_ABC_A_THRESHOLD = 0.80  # cumulative value share ≤ 80% → A
_ABC_B_THRESHOLD = 0.95  # cumulative value share ≤ 95% → B (else C)

# XYZ CV thresholds
# Business meaning: X=stable (CV<0.5), Y=variable (0.5≤CV<1.0), Z=erratic (CV≥1.0)
_XYZ_X_MAX_CV = 0.5
_XYZ_Y_MAX_CV = 1.0

# FSN recency windows (months from the latest observation date)
# Business meaning: F=fast (sold within last quarter), S=slow (3-12 months),
# N=non-moving (never issued or silent >12 months)
_FSN_FAST_MONTHS = 3
_FSN_SLOW_MONTHS = 12


# ---------------------------------------------------------------------------
# Classification functions
# ---------------------------------------------------------------------------

def classify_abc(features: pd.DataFrame) -> pd.DataFrame:
    """Assign ABC class based on cumulative total_issue_value_lkr (descending).

    Business meaning:
      A-items (≈top 80% of value): tight reorder control, frequent review, lower safety stock
      B-items (≈next 15% of value): moderate control, periodic review
      C-items (≈bottom 5% of value): relaxed control, larger safety stocks acceptable

    SKUs with zero issue value are always class C (no contribution to turnover).

    Args:
        features: spare_parts_features DataFrame with total_issue_value_lkr column.

    Returns:
        Input DataFrame with added 'abc' column.
    """
    df = features.copy()

    # Sort by value descending; zero-value SKUs will naturally fall to C
    sorted_idx = df["total_issue_value_lkr"].sort_values(ascending=False).index
    df_sorted = df.loc[sorted_idx].copy()

    total_value = df_sorted["total_issue_value_lkr"].sum()
    if total_value == 0:
        df["abc"] = "C"
        return df

    df_sorted["_cum_value"] = df_sorted["total_issue_value_lkr"].cumsum()
    # Use PRE-inclusion cumulative share so a high-value item that pushes
    # cumulative past 80% is itself still assigned "A" (standard Pareto practice).
    df_sorted["_prev_cum_share"] = (df_sorted["_cum_value"].shift(1).fillna(0)) / total_value

    df_sorted["abc"] = "C"
    df_sorted.loc[df_sorted["_prev_cum_share"] < _ABC_A_THRESHOLD, "abc"] = "A"
    df_sorted.loc[
        (df_sorted["_prev_cum_share"] >= _ABC_A_THRESHOLD)
        & (df_sorted["_prev_cum_share"] < _ABC_B_THRESHOLD),
        "abc",
    ] = "B"

    # Zero-value SKUs are always C regardless of cumulative-share edge effects
    df_sorted.loc[df_sorted["total_issue_value_lkr"] == 0, "abc"] = "C"

    df["abc"] = df_sorted.reindex(df.index)["abc"]
    return df


def classify_xyz(features: pd.DataFrame) -> pd.DataFrame:
    """Assign XYZ class based on CV (coefficient of variation of non-zero demand months).

    Business meaning:
      X (CV<0.5):  stable, predictable demand → deterministic reorder point feasible
      Y (0.5≤CV<1.0): moderate variability → probabilistic safety stock
      Z (CV≥1.0):  erratic/lumpy → Croston or simulation-based forecasting

    SKUs with zero active months (non-movers) are assigned Z because their
    demand is maximally uncertain.

    Args:
        features: spare_parts_features DataFrame with cv and active_months columns.

    Returns:
        Input DataFrame with added 'xyz' column.
    """
    df = features.copy()
    cv = df["cv"]
    df["xyz"] = "Z"
    df.loc[cv < _XYZ_X_MAX_CV, "xyz"] = "X"
    df.loc[(cv >= _XYZ_X_MAX_CV) & (cv < _XYZ_Y_MAX_CV), "xyz"] = "Y"
    # Non-movers forced to Z (no demand history → maximum forecast uncertainty)
    df.loc[df["active_months"] == 0, "xyz"] = "Z"
    return df


def classify_fsn(
    features: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Assign FSN class based on recency of last issue relative to observation window end.

    Business meaning:
      F (Fast):    last issue within 3 months → stock must be maintained
      S (Slow):    last issue 3–12 months ago → review and reduce stock
      N (Non-moving): never issued OR silent >12 months → stock reduction / write-off candidate

    The reference date defaults to the most recent last_issue_date in the dataset
    (proxy for "today" within the data window). Pass reference_date explicitly in
    tests or when scoring against a fixed evaluation date.

    Args:
        features: spare_parts_features DataFrame with last_issue_date and active_months columns.
        reference_date: override the reference date (useful for unit tests and backtesting).

    Returns:
        Input DataFrame with added 'fsn' column.
    """
    df = features.copy()
    df["last_issue_date"] = pd.to_datetime(df["last_issue_date"])

    has_date = df["last_issue_date"].notna()
    if not has_date.any():
        df["fsn"] = "N"
        return df

    ref_date: pd.Timestamp = reference_date if reference_date is not None else df.loc[has_date, "last_issue_date"].max()
    fast_cutoff = ref_date - pd.DateOffset(months=_FSN_FAST_MONTHS)
    slow_cutoff = ref_date - pd.DateOffset(months=_FSN_SLOW_MONTHS)

    df["fsn"] = "N"  # default: non-moving
    df.loc[has_date & (df["last_issue_date"] >= fast_cutoff), "fsn"] = "F"
    df.loc[
        has_date & (df["last_issue_date"] < fast_cutoff) & (df["last_issue_date"] >= slow_cutoff),
        "fsn",
    ] = "S"
    # Never-issued (active_months == 0) stays N regardless of date
    df.loc[df["active_months"] == 0, "fsn"] = "N"
    return df


def build_classification(
    features: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Run all three classifications and produce the combined ABC-XYZ-FSN table.

    Args:
        features: spare_parts_features DataFrame.
        reference_date: optional override for FSN recency window (default: max last_issue_date).

    Returns:
        DataFrame with original feature columns plus 'abc', 'xyz', 'fsn',
        'abc_xyz_fsn' (combined code), and 'policy_tier' (recommended handling).
    """
    df = classify_abc(features)
    df = classify_xyz(df)
    df = classify_fsn(df, reference_date=reference_date)
    df["abc_xyz_fsn"] = df["abc"] + df["xyz"] + df["fsn"]
    df["policy_tier"] = df["abc_xyz_fsn"].apply(_policy_tier)
    return df


def _policy_tier(code: str) -> str:
    """Map a 3-letter ABC-XYZ-FSN code to an inventory policy recommendation.

    Business meaning: drives Stage 12 ROL/ROQ selection:
      - critical: continuous review, tight safety stock, frequent replenishment
      - managed:  periodic review, standard safety stock
      - watch:    review for rationalisation or consolidation
      - rationalise: consider write-off, return to supplier, or no-replenishment
    """
    if len(code) != 3:
        return "unknown"
    abc, xyz, fsn = code[0], code[1], code[2]

    # Critical = high-value AND fast-moving
    if abc == "A" and fsn == "F":
        return "critical"
    # Managed = high-value but slow/erratic, OR medium-value fast-moving
    if (abc == "A" and fsn in ("S",)) or (abc == "B" and fsn == "F"):
        return "managed"
    if abc == "B" and fsn in ("S",):
        return "managed"
    # Watch = medium/low value with some activity
    if fsn in ("F", "S") and abc == "C":
        return "watch"
    if abc == "A" and fsn == "N":
        return "watch"  # high value but stale — investigate
    # Rationalise = non-moving with low value, or permanently silent
    return "rationalise"


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def abc_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-ABC-class SKU count, value total, and cumulative value share."""
    grp = df.groupby("abc").agg(
        sku_count=("material_9", "count"),
        total_value_lkr=("total_issue_value_lkr", "sum"),
    ).reindex(["A", "B", "C"]).reset_index()
    grand_total = grp["total_value_lkr"].sum()
    grp["value_share_%"] = (grp["total_value_lkr"] / max(grand_total, 1) * 100).round(2)
    grp["sku_share_%"] = (grp["sku_count"] / grp["sku_count"].sum() * 100).round(2)
    grp["cum_value_share_%"] = grp["value_share_%"].cumsum().round(2)
    return grp


def xyz_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-XYZ-class SKU count and median CV."""
    grp = df.groupby("xyz").agg(
        sku_count=("material_9", "count"),
        median_cv=("cv", "median"),
        mean_cv=("cv", "mean"),
    ).reindex(["X", "Y", "Z"]).reset_index()
    grp["sku_share_%"] = (grp["sku_count"] / grp["sku_count"].sum() * 100).round(2)
    return grp


def fsn_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-FSN-class SKU count, total value, and median months since last issue."""
    ref = df["last_issue_date"].max()
    df = df.copy()
    df["_months_silent"] = ((ref - df["last_issue_date"]).dt.days / 30.44).round(1)
    grp = df.groupby("fsn").agg(
        sku_count=("material_9", "count"),
        total_value_lkr=("total_issue_value_lkr", "sum"),
        median_months_silent=("_months_silent", "median"),
    ).reindex(["F", "S", "N"]).reset_index()
    grp["value_share_%"] = (grp["total_value_lkr"] / max(grp["total_value_lkr"].sum(), 1) * 100).round(2)
    grp["sku_share_%"] = (grp["sku_count"] / grp["sku_count"].sum() * 100).round(2)
    return grp


def policy_tier_summary(df: pd.DataFrame) -> pd.DataFrame:
    """SKU count and value per policy tier."""
    grp = df.groupby("policy_tier").agg(
        sku_count=("material_9", "count"),
        total_value_lkr=("total_issue_value_lkr", "sum"),
    ).reset_index().sort_values("total_value_lkr", ascending=False)
    grand = grp["total_value_lkr"].sum()
    grp["value_share_%"] = (grp["total_value_lkr"] / max(grand, 1) * 100).round(2)
    grp["sku_share_%"] = (grp["sku_count"] / grp["sku_count"].sum() * 100).round(2)
    return grp.reset_index(drop=True)


def segment_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-tabulation of ABC vs FSN (SKU counts) for the executive summary."""
    return pd.crosstab(df["abc"], df["fsn"], margins=True, margins_name="Total")


def ml_cluster_segments(features: pd.DataFrame, n_clusters: int = 6) -> pd.DataFrame:
    """Discover natural demand segments via K-Means clustering on demand features.

    Business meaning: the fixed ABC-XYZ-FSN thresholds can miss natural groupings
    in the data. K-Means on the continuous demand metrics discovers data-driven
    segments (e.g. "high-value-erratic" vs "low-value-dormant") without imposing
    predetermined cut-offs. The cluster labels enrich the classification table and
    can validate or refine the rule-based tiers.

    Features used (log-scaled or standardised):
      log1p(total_issue_value_lkr), cv, p_zero, log1p(avg_monthly_demand),
      active_months / total_months (activity ratio)

    Args:
        features: spare_parts_features or classified DataFrame.
        n_clusters: number of K-Means clusters (default 6).

    Returns:
        Input DataFrame with added columns:
          demand_cluster (int 0..n-1),
          demand_segment (human-readable label derived from cluster centroid).
    """
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    df = features.copy()

    X = pd.DataFrame({
        "log_value":      np.log1p(df["total_issue_value_lkr"]),
        "cv":             df["cv"].fillna(0.0),
        "p_zero":         df["p_zero"].fillna(1.0),
        "log_avg_demand": np.log1p(df["avg_monthly_demand"].fillna(0.0)),
        "activity_ratio": (df["active_months"] / df["total_months"].clip(lower=1)).fillna(0.0),
    })

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(Xs)
    df["demand_cluster"] = labels

    # Label clusters from centroids (un-scale back for interpretation)
    centroids = pd.DataFrame(
        scaler.inverse_transform(km.cluster_centers_), columns=X.columns
    )

    # Rank-based labelling: sort clusters by composite importance score so that
    # labels are always unique regardless of centroid distribution shape.
    centroids["_score"] = (
        centroids["log_value"] * 0.5
        + centroids["activity_ratio"] * 0.3
        - centroids["p_zero"] * 0.2
    )
    _SEGMENT_LABELS = [
        "high-value-fast",
        "high-value-moderate",
        "medium-value-active",
        "low-value-sporadic",
        "low-value-slow",
        "dormant",
    ]
    sorted_clusters = centroids.sort_values("_score", ascending=False)
    cluster_label_map: dict[int, str] = {}
    for rank, (idx, _) in enumerate(sorted_clusters.iterrows()):
        label = _SEGMENT_LABELS[rank] if rank < len(_SEGMENT_LABELS) else f"segment-{rank}"
        cluster_label_map[int(idx)] = label
    df["demand_segment"] = df["demand_cluster"].map(cluster_label_map)

    counts = df["demand_segment"].value_counts()
    logger.info(f"ML clustering (k={n_clusters}): {dict(counts)}")
    return df


def summary_kpis(df: pd.DataFrame) -> dict:
    """Headline KPIs for the classification report."""
    return {
        "Total SKUs classified": len(df),
        "ABC-A SKUs": int((df["abc"] == "A").sum()),
        "ABC-B SKUs": int((df["abc"] == "B").sum()),
        "ABC-C SKUs": int((df["abc"] == "C").sum()),
        "XYZ-X SKUs (stable)": int((df["xyz"] == "X").sum()),
        "XYZ-Y SKUs (variable)": int((df["xyz"] == "Y").sum()),
        "XYZ-Z SKUs (erratic)": int((df["xyz"] == "Z").sum()),
        "FSN-F SKUs (fast)": int((df["fsn"] == "F").sum()),
        "FSN-S SKUs (slow)": int((df["fsn"] == "S").sum()),
        "FSN-N SKUs (non-moving)": int((df["fsn"] == "N").sum()),
        "Critical tier SKUs": int((df["policy_tier"] == "critical").sum()),
        "Managed tier SKUs": int((df["policy_tier"] == "managed").sum()),
        "Watch tier SKUs": int((df["policy_tier"] == "watch").sum()),
        "Rationalise tier SKUs": int((df["policy_tier"] == "rationalise").sum()),
        "Top-20 A-items value share %": round(
            df.nlargest(20, "total_issue_value_lkr")["total_issue_value_lkr"].sum()
            / max(df["total_issue_value_lkr"].sum(), 1) * 100, 2
        ),
    }


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

def _write_excel(
    kpis: dict,
    abc_sum: pd.DataFrame,
    xyz_sum: pd.DataFrame,
    fsn_sum: pd.DataFrame,
    policy_sum: pd.DataFrame,
    matrix: pd.DataFrame,
    classified: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white"})
        lkr = wb.add_format({"num_format": "#,##0.00"})

        def write_sheet(df: pd.DataFrame, sheet: str, widths: list[int]) -> None:
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for i, (col, w) in enumerate(zip(df.columns, widths)):
                ws.set_column(i, i, w)
                ws.write(0, i, col, hdr)

        # Sheet 1 — Summary KPIs
        kpi_df = pd.DataFrame([{"KPI": k, "Value": v} for k, v in kpis.items()])
        write_sheet(kpi_df, "Summary KPIs", [45, 25])

        # Sheet 2 — ABC breakdown (with Pareto bar chart)
        write_sheet(abc_sum, "ABC Analysis", [10, 14, 18, 14, 14, 18])
        ws = writer.sheets["ABC Analysis"]
        n = len(abc_sum) + 1
        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name": "Value Share %",
            "categories": ["ABC Analysis", 1, 0, n - 1, 0],
            "values":     ["ABC Analysis", 1, 3, n - 1, 3],
            "fill": {"color": "#1F4E79"},
        })
        chart.set_title({"name": "ABC — Value Share by Class"})
        chart.set_size({"width": 400, "height": 280})
        ws.insert_chart("H2", chart)

        # Sheet 3 — XYZ breakdown
        write_sheet(xyz_sum, "XYZ Analysis", [10, 14, 12, 12, 14])

        # Sheet 4 — FSN breakdown
        write_sheet(fsn_sum, "FSN Analysis", [10, 14, 18, 20, 14, 14])

        # Sheet 5 — Policy Tier summary
        write_sheet(policy_sum, "Policy Tiers", [20, 14, 18, 14, 14])

        # Sheet 6 — ABC × FSN matrix
        matrix_reset = matrix.reset_index()
        matrix_reset.to_excel(writer, sheet_name="ABC-FSN Matrix", index=False)
        ws = writer.sheets["ABC-FSN Matrix"]
        ws.write(0, 0, "ABC \\ FSN", hdr)
        for i, col in enumerate(matrix_reset.columns[1:], start=1):
            ws.write(0, i, str(col), hdr)
        ws.set_column(0, 0, 12)
        for i in range(1, len(matrix_reset.columns)):
            ws.set_column(i, i, 10)

        # Sheet 7 — ML Demand Clusters
        if "demand_segment" in classified.columns:
            cluster_sum = (
                classified.groupby(["demand_cluster", "demand_segment"])
                .agg(sku_count=("material_9", "count"),
                     total_value_lkr=("total_issue_value_lkr", "sum"),
                     median_cv=("cv", "median"),
                     median_p_zero=("p_zero", "median"))
                .reset_index()
                .sort_values("demand_cluster")
            )
            write_sheet(cluster_sum, "ML Demand Clusters", [14, 22, 12, 18, 12, 14])

        # Sheet 8 — Full classification table (capped at 50K)
        out_cols = [
            "material_9", "description",
            "abc", "xyz", "fsn", "abc_xyz_fsn", "policy_tier",
            "total_issue_value_lkr", "total_issue_qty",
            "avg_monthly_demand", "cv", "p_zero",
            "active_months", "last_issue_date", "in_ssop",
            "demand_cluster", "demand_segment",
        ]
        out_cols = [c for c in out_cols if c in classified.columns]
        write_sheet(
            classified[out_cols].head(50_000),
            "Full Classification",
            [20, 40, 6, 6, 6, 12, 16, 20, 16, 18, 10, 10, 14, 14, 8, 14, 22],
        )

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 9 entry point: ABC-XYZ-FSN inventory classification."""
    if not refresh and _ABC_XYZ_FSN_PARQUET.exists():
        logger.info("Stage 9 cached — skipping (use --refresh to force)")
        return

    logger.info("Stage 9: ABC-XYZ-FSN Classification")

    if not _FEATURES_PARQUET.exists():
        raise FileNotFoundError(
            f"spare_parts_features.parquet not found at {_FEATURES_PARQUET}. "
            "Run Stage 8 first."
        )

    features = pd.read_parquet(_FEATURES_PARQUET)
    logger.info(f"Features loaded: {len(features):,} SKUs")

    classified = build_classification(features)

    # ML enrichment: K-Means demand segmentation
    logger.info("Running ML demand clustering (K-Means, k=6) …")
    classified = ml_cluster_segments(classified, n_clusters=6)

    classified.to_parquet(_ABC_XYZ_FSN_PARQUET, index=False)
    logger.info(f"Classification saved → {_ABC_XYZ_FSN_PARQUET} ({len(classified):,} rows)")

    kpis = summary_kpis(classified)
    abc_sum = abc_summary(classified)
    xyz_sum = xyz_summary(classified)
    fsn_sum = fsn_summary(classified)
    policy_sum = policy_tier_summary(classified)
    matrix = segment_matrix(classified)

    _write_excel(kpis, abc_sum, xyz_sum, fsn_sum, policy_sum, matrix, classified)

    logger.info(
        f"Stage 9 complete | "
        f"A:{kpis['ABC-A SKUs']} B:{kpis['ABC-B SKUs']} C:{kpis['ABC-C SKUs']} | "
        f"F:{kpis['FSN-F SKUs (fast)']} S:{kpis['FSN-S SKUs (slow)']} N:{kpis['FSN-N SKUs (non-moving)']} | "
        f"Critical:{kpis['Critical tier SKUs']} Rationalise:{kpis['Rationalise tier SKUs']}"
    )
