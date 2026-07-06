"""EDA endpoints — Stages 4, 5, 7, 8 (orders, sales, stock movements, spare parts)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query, Response

from src.api.deps import (
    get_ingestion_log,
    get_orders_clean,
    get_orders_rejection_log,
    get_sales_clean,
    get_spare_parts_features,
    get_stock_movements,
)
from src.api.schemas import (
    AsePerfRow,
    AssociationRule,
    CategoryMixRow,
    CoOccurrenceGroup,
    CustomerRecommendation,
    DealerPerfRow,
    DistrictPerfRow,
    FillRateBandRow,
    FrequentItemset,
    FulfillmentAnalysis,
    FulfillmentLineBucket,
    IntermittentSkuRow,
    ItemRecommendations,
    ItemSimilarity,
    LargeInvoice,
    MarketBasketMLResponse,
    MarketBasketResponse,
    McMonthlyCategoryPoint,
    MLCluster,
    MLModelInfo,
    MovementMonthlyPoint,
    MovementsResponse,
    OrdersEdaDealer,
    OrdersEdaMonthlyPoint,
    OrdersEdaRejectionReasonRow,
    OrdersEdaRejectionRow,
    OrdersEdaResponse,
    PartAnalysisRow,
    ProvinceAnalysisRow,
    ProvincePerformanceRow,
    RmPerfRow,
    SalesAseRow,
    SalesDealerRow,
    SalesDistrictRow,
    SalesEdaMonthlyPoint,
    SalesEdaResponse,
    SalesMcCategoryRow,
    SalesMcMonthlyPoint,
    SalesPartRow,
    SalesProvinceRow,
    SalesRmRow,
    ShortShipRow,
    SparePartsEdaResponse,
    TopSkuRow,
    UmapPoint,
    YoYGrowthRow,
)

router = APIRouter(prefix="/eda", tags=["EDA — Stages 4-5-7-8"])

_SEG_ORDER = ["MC – Lubricant", "MC – Battery", "MC – Tyre", "MC – Spare Parts", "OBM"]


def _business_insights(full_po: pd.DataFrame, full_all_c: pd.DataFrame | None = None) -> dict:
    """Compute business insight metrics from the full (unfiltered) PO dataframe.

    Args:
        full_po:    Non-cancelled PO lines — used for fill rate, short-ship, fill-rate bands.
        full_all_c: All original C-orders (incl. cancelled) for province order_value_lkr.
    """
    if full_po.empty:
        return {
            "category_mix": [],
            "yoy_growth": [],
            "province_perf": [],
            "top_short_shipped": [],
            "fill_rate_bands": [],
            "dealer_health_summary": {},
            "pareto_summary": {},
            "category_cross": {},
        }

    # Segment label: MC – {category} or OBM
    full_po = full_po.copy()
    full_po["_seg"] = full_po["mc_category"].apply(lambda c: "OBM" if c == "N/A" else f"MC – {c}")

    # ── 1. Category mix ───────────────────────────────────────────────────────
    cat_agg = full_po.groupby("_seg", as_index=False).agg(
        order_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        value_lkr=("confirmed_value", "sum"),
    )
    total_val = float(cat_agg["value_lkr"].sum()) or 1.0
    cat_agg["value_share_pct"] = (cat_agg["value_lkr"] / total_val * 100).round(2)
    cat_agg["fill_rate_pct"] = (
        (cat_agg["confirmed_qty"] / cat_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    sort_map = {s: i for i, s in enumerate(_SEG_ORDER)}
    cat_agg["_order"] = cat_agg["_seg"].map(sort_map).fillna(99)
    cat_agg = cat_agg.sort_values("_order")
    category_mix = [
        CategoryMixRow(
            segment=str(r["_seg"]),
            order_lines=int(r["order_lines"]),
            value_lkr=round(float(r["value_lkr"]), 0),
            value_share_pct=float(r["value_share_pct"]),
            fill_rate_pct=float(r["fill_rate_pct"]),
        )
        for _, r in cat_agg.iterrows()
    ]

    # ── 2. YoY growth (most recent two calendar years in the data) ───────────
    full_po["_year"] = full_po["Year_Month_str"].str[:4]
    yoy_agg = full_po.groupby(["_seg", "_year"], as_index=False).agg(
        value_lkr=("confirmed_value", "sum"),
        lines=("Sales Document", "count"),
    )
    _avail_years = sorted(yoy_agg["_year"].dropna().unique())
    yr_prev = (
        _avail_years[-2]
        if len(_avail_years) >= 2
        else (_avail_years[0] if _avail_years else "2024")
    )
    yr_curr = _avail_years[-1] if _avail_years else "2025"
    yoy_growth = []
    for seg in _SEG_ORDER:
        sub = yoy_agg[yoy_agg["_seg"] == seg]
        v_prev = float(sub[sub["_year"] == yr_prev]["value_lkr"].sum())
        v_curr = float(sub[sub["_year"] == yr_curr]["value_lkr"].sum())
        l_prev = int(sub[sub["_year"] == yr_prev]["lines"].sum())
        l_curr = int(sub[sub["_year"] == yr_curr]["lines"].sum())
        if v_prev > 0 or v_curr > 0:
            yoy_growth.append(
                YoYGrowthRow(
                    segment=seg,
                    year_prev=int(yr_prev),
                    year_curr=int(yr_curr),
                    value_year_prev=round(v_prev, 0),
                    value_year_curr=round(v_curr, 0),
                    yoy_pct=round((v_curr - v_prev) / max(v_prev, 1) * 100, 1),
                    lines_year_prev=l_prev,
                    lines_year_curr=l_curr,
                )
            )

    # ── 3. Province performance ───────────────────────────────────────────────
    _NVI = "Net Value (Item)"
    prov_agg = full_po.groupby("Province", as_index=False).agg(
        order_value_lkr=(
            "confirmed_value",
            "sum",
        ),  # fallback; overwritten below if all_c available
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        dealer_count=("Dealer Code", "nunique"),
    )
    if (
        full_all_c is not None
        and not full_all_c.empty
        and _NVI in full_all_c.columns
        and "Province" in full_all_c.columns
    ):
        _prov_c_val = (
            full_all_c.groupby("Province")[_NVI].sum().rename("order_value_lkr").reset_index()
        )
        prov_agg = (
            prov_agg.drop(columns=["order_value_lkr"])
            .merge(_prov_c_val, on="Province", how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    prov_agg = prov_agg.sort_values("order_value_lkr", ascending=False)
    total_prov = float(prov_agg["order_value_lkr"].sum()) or 1.0
    prov_agg["value_share_pct"] = (prov_agg["order_value_lkr"] / total_prov * 100).round(2)
    prov_agg["fill_rate_pct"] = (
        (prov_agg["confirmed_qty"] / prov_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    province_perf = [
        ProvincePerformanceRow(
            province=str(r["Province"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            value_share_pct=float(r["value_share_pct"]),
            fill_rate_pct=float(r["fill_rate_pct"]),
            dealer_count=int(r["dealer_count"]),
        )
        for _, r in prov_agg.head(10).iterrows()
    ]

    # ── 4. Top short-shipped materials ────────────────────────────────────────
    short = full_po[full_po["lost_qty"] < 0]
    top_short_shipped: list[ShortShipRow] = []
    if not short.empty:
        s_agg = (
            short.groupby(["Material", "Material Description"], as_index=False)
            .agg(
                order_qty=("Order Quantity (Item)", "sum"),
                confirmed_qty=("Confirmed Quantity (Item)", "sum"),
                short_qty=("lost_qty", lambda x: float(abs(x.sum()))),
                occurrences=("lost_qty", "count"),
            )
            .sort_values("short_qty", ascending=False)
            .head(15)
        )
        for _, r in s_agg.iterrows():
            fr = float(r["confirmed_qty"]) / max(float(r["order_qty"]), 1) * 100
            top_short_shipped.append(
                ShortShipRow(
                    material=str(r["Material"]),
                    description=str(r["Material Description"]),
                    short_qty=round(float(r["short_qty"]), 0),
                    fill_rate_pct=round(fr, 1),
                    occurrences=int(r["occurrences"]),
                )
            )

    # ── 5. Fill rate bands by segment (per-dealer fill rate) ──────────────────
    fill_rate_bands: list[FillRateBandRow] = []
    for seg in _SEG_ORDER:
        if seg == "OBM":
            seg_df = full_po[full_po["dealer_type"] == "OBM"]
        else:
            cat = seg.replace("MC – ", "")
            seg_df = full_po[(full_po["dealer_type"] == "MC") & (full_po["mc_category"] == cat)]
        if seg_df.empty:
            continue
        d_fill = seg_df.groupby("Dealer Code").agg(
            oq=("Order Quantity (Item)", "sum"),
            cq=("Confirmed Quantity (Item)", "sum"),
        )
        d_fill["fr"] = d_fill["cq"] / d_fill["oq"].replace(0, np.nan)
        d_fill = d_fill.dropna(subset=["fr"])
        fill_rate_bands.append(
            FillRateBandRow(
                segment=seg,
                above_98=int((d_fill["fr"] >= 0.98).sum()),
                between_95_98=int(((d_fill["fr"] >= 0.95) & (d_fill["fr"] < 0.98)).sum()),
                between_90_95=int(((d_fill["fr"] >= 0.90) & (d_fill["fr"] < 0.95)).sum()),
                below_90=int((d_fill["fr"] < 0.90).sum()),
            )
        )

    # ── 6. Dealer health summary ──────────────────────────────────────────────
    d_agg = (
        full_po.groupby("Dealer Code")
        .agg(
            value=("confirmed_value", "sum"),
            oq=("Order Quantity (Item)", "sum"),
            cq=("Confirmed Quantity (Item)", "sum"),
            last_month=("Year_Month_str", "max"),
        )
        .sort_values("value", ascending=False)
        .reset_index()
    )
    total_d_val = float(d_agg["value"].sum()) or 1.0
    d_agg["cumul_pct"] = d_agg["value"].cumsum() / total_d_val
    d_agg["tier"] = d_agg["cumul_pct"].apply(
        lambda x: "A" if x <= 0.8 else ("B" if x <= 0.95 else "C")
    )
    last_period = str(full_po["Year_Month_str"].max())
    try:
        max_dt = pd.Period(last_period, "M")
        dormant_threshold = str(max_dt - 3)
    except Exception:
        dormant_threshold = "2099-12"
    dormant_count = int((d_agg["last_month"] < dormant_threshold).sum())
    dealer_health_summary = {
        "a_tier": int((d_agg["tier"] == "A").sum()),
        "b_tier": int((d_agg["tier"] == "B").sum()),
        "c_tier": int((d_agg["tier"] == "C").sum()),
        "dormant_count": dormant_count,
        "total_dealers": int(len(d_agg)),
    }

    # ── 7. Pareto summary ─────────────────────────────────────────────────────
    sku_val = full_po.groupby("Material")["confirmed_value"].sum().sort_values(ascending=False)
    sku_cumul = sku_val.cumsum() / (sku_val.sum() or 1)
    sku_80 = int((sku_cumul <= 0.8).sum()) + 1

    d_val = full_po.groupby("Dealer Code")["confirmed_value"].sum().sort_values(ascending=False)
    d_cumul = d_val.cumsum() / (d_val.sum() or 1)
    d_80 = int((d_cumul <= 0.8).sum()) + 1

    pareto_summary = {
        "sku_80pct_count": min(sku_80, int(len(sku_val))),
        "total_skus": int(len(sku_val)),
        "dealer_80pct_count": min(d_80, int(len(d_val))),
        "total_dealers": int(len(d_val)),
    }

    # ── 8. Category cross (MC dealers ordering 2+ sub-categories) ────────────
    mc_po = full_po[full_po["dealer_type"] == "MC"]
    if not mc_po.empty:
        d_cats = mc_po.groupby("Dealer Code")["mc_category"].nunique()
        category_cross = {
            "multi_category": int((d_cats >= 2).sum()),
            "single_category": int((d_cats == 1).sum()),
            "total_mc_dealers": int(len(d_cats)),
        }
    else:
        category_cross = {"multi_category": 0, "single_category": 0, "total_mc_dealers": 0}

    return {
        "category_mix": category_mix,
        "yoy_growth": yoy_growth,
        "province_perf": province_perf,
        "top_short_shipped": top_short_shipped,
        "fill_rate_bands": fill_rate_bands,
        "dealer_health_summary": dealer_health_summary,
        "pareto_summary": pareto_summary,
        "category_cross": category_cross,
    }


_MC_CAT_API_MAP = {
    "Lubricant": "Lubricant",
    "Battery": "Battery",
    "Tyre": "Tyre",
    "SpareParts": "Spare Parts",
    "ALL": "ALL",
}


def _compute_analysis_tables(
    po: pd.DataFrame,
    ret: pd.DataFrame,
    all_c: pd.DataFrame | None = None,
) -> dict:
    """Compute the 6 per-segment analysis tables from filtered PO and return dataframes.

    Args:
        po:    Non-cancelled PO lines (Confirmed Qty > 0) — used for fill rate / line counts.
        ret:   Return lines (H-type + cancelled C) — used for return rate.
        all_c: All original C-orders (non-cancelled + cancelled, clean + rejected).
               Net Value (Item) from all_c is the true demand signal matching data.xlsx
               Order_Received. If None, falls back to confirmed_value from po.
    """
    empty: dict = {
        "part_analysis": [],
        "dealer_perf": [],
        "rm_perf": [],
        "ase_perf": [],
        "district_perf": [],
        "province_analysis": [],
    }
    if po.empty:
        return empty

    # Pre-build order-intake value lookups from all_c (Net Value Item, demand signal)
    _NVI = "Net Value (Item)"
    _use_all_c = (
        all_c is not None
        and not all_c.empty
        and _NVI in (all_c.columns if all_c is not None else [])
    )

    def _c_val_by(keys: str | list[str]) -> pd.DataFrame | None:
        if not _use_all_c or all_c is None:
            return None
        key_list = [keys] if isinstance(keys, str) else keys
        if not all(k in all_c.columns for k in key_list):
            return None
        return all_c.groupby(key_list)[_NVI].sum().rename("order_value_lkr").reset_index()

    # ── Part analysis (top 30 by value; share % relative to ALL parts) ───────
    part_agg_full = (
        po.groupby(["Material", "Material Description"], as_index=False)
        .agg(
            order_lines=("Sales Document", "count"),
            order_qty=("Order Quantity (Item)", "sum"),
            confirmed_qty=("Confirmed Quantity (Item)", "sum"),
            total_value_lkr=("confirmed_value", "sum"),
            short_qty=("lost_qty", lambda x: float(abs(x[x < 0].sum()))),
        )
        .sort_values("total_value_lkr", ascending=False)
    )
    total_val = float(part_agg_full["total_value_lkr"].sum()) or 1.0
    part_agg = part_agg_full.head(30).copy()
    part_agg["fill_rate_pct"] = (
        (part_agg["confirmed_qty"] / part_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    part_agg["value_share_pct"] = (part_agg["total_value_lkr"] / total_val * 100).round(2)
    part_analysis = [
        PartAnalysisRow(
            material=str(r["Material"]),
            description=str(r["Material Description"]),
            order_lines=int(r["order_lines"]),
            order_qty=float(r["order_qty"]),
            confirmed_qty=float(r["confirmed_qty"]),
            total_value_lkr=round(float(r["total_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            value_share_pct=float(r["value_share_pct"]),
            short_qty=float(r["short_qty"]),
        )
        for _, r in part_agg.iterrows()
    ]

    # ── Dealer performance (top 30 by value) ─────────────────────────────────
    _c_dealer_val = _c_val_by("Dealer Code")
    dealer_agg = po.groupby(
        ["Dealer Code", "Dealer Name", "Province", "District", "RM", "ASE"], as_index=False
    ).agg(
        po_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        order_value_lkr=("confirmed_value", "sum"),  # fallback; overwritten if all_c available
    )
    if _c_dealer_val is not None:
        dealer_agg = (
            dealer_agg.drop(columns=["order_value_lkr"])
            .merge(_c_dealer_val, on="Dealer Code", how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    dealer_agg = dealer_agg.sort_values("order_value_lkr", ascending=False)
    total_d_val = float(dealer_agg["order_value_lkr"].sum()) or 1.0
    dealer_agg["fill_rate_pct"] = (
        (dealer_agg["confirmed_qty"] / dealer_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    dealer_agg["value_share_pct"] = (dealer_agg["order_value_lkr"] / total_d_val * 100).round(2)
    dealer_agg["cumul_pct"] = dealer_agg["value_share_pct"].cumsum()
    dealer_agg["dealer_tier"] = dealer_agg["cumul_pct"].apply(
        lambda x: "A" if x <= 80 else ("B" if x <= 95 else "C")
    )

    ret_d = (
        ret.groupby("Dealer Code").agg(return_value_lkr=("confirmed_value", "sum")).reset_index()
        if not ret.empty
        else pd.DataFrame(columns=["Dealer Code", "return_value_lkr"])
    )
    dealer_agg = dealer_agg.merge(ret_d, on="Dealer Code", how="left").fillna(0)
    dealer_agg["return_rate_pct"] = (
        (dealer_agg["return_value_lkr"] / dealer_agg["order_value_lkr"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )

    dealer_perf = [
        DealerPerfRow(
            dealer_code=str(r["Dealer Code"]),
            dealer_name=str(r["Dealer Name"]),
            province=str(r["Province"]),
            district=str(r["District"]),
            rm=str(r["RM"]),
            ase=str(r["ASE"]),
            po_lines=int(r["po_lines"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            return_rate_pct=float(r["return_rate_pct"]),
            dealer_tier=str(r["dealer_tier"]),
            value_share_pct=float(r["value_share_pct"]),
        )
        for _, r in dealer_agg.head(30).iterrows()
    ]

    # ── RM performance (with primary province) ────────────────────────────────
    rm_province = (
        po.groupby("RM")["Province"]
        .agg(lambda x: x.mode().iloc[0] if len(x) > 0 else "")
        .reset_index()
        .rename(columns={"Province": "province"})
    )
    _c_rm_val = _c_val_by("RM")
    rm_agg = po.groupby("RM", as_index=False).agg(
        unique_dealers=("Dealer Code", "nunique"),
        po_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        order_value_lkr=("confirmed_value", "sum"),
    )
    if _c_rm_val is not None:
        rm_agg = (
            rm_agg.drop(columns=["order_value_lkr"])
            .merge(_c_rm_val, on="RM", how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    rm_agg = rm_agg.merge(rm_province, on="RM", how="left")
    rm_agg["province"] = rm_agg["province"].fillna("")
    rm_agg["fill_rate_pct"] = (
        (rm_agg["confirmed_qty"] / rm_agg["order_qty"].replace(0, np.nan) * 100).round(2).fillna(0)
    )
    total_rm_val = float(rm_agg["order_value_lkr"].sum()) or 1.0
    rm_agg["value_share_pct"] = (rm_agg["order_value_lkr"] / total_rm_val * 100).round(2)

    ret_rm = (
        ret.groupby("RM")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
        if not ret.empty
        else pd.DataFrame(columns=["RM", "return_value_lkr"])
    )
    rm_agg = rm_agg.merge(ret_rm, on="RM", how="left").fillna({"return_value_lkr": 0})
    rm_agg["return_rate_pct"] = (
        (rm_agg["return_value_lkr"] / rm_agg["order_value_lkr"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    rm_agg = rm_agg.sort_values("order_value_lkr", ascending=False)

    rm_perf = [
        RmPerfRow(
            rm=str(r["RM"]),
            province=str(r["province"]),
            unique_dealers=int(r["unique_dealers"]),
            po_lines=int(r["po_lines"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            return_rate_pct=float(r["return_rate_pct"]),
            value_share_pct=float(r["value_share_pct"]),
        )
        for _, r in rm_agg.iterrows()
    ]

    # ── ASE performance ───────────────────────────────────────────────────────
    _c_ase_val = _c_val_by("ASE")
    ase_agg = po.groupby(["ASE", "RM", "Province"], as_index=False).agg(
        unique_dealers=("Dealer Code", "nunique"),
        po_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        order_value_lkr=("confirmed_value", "sum"),
    )
    if _c_ase_val is not None:
        ase_agg = (
            ase_agg.drop(columns=["order_value_lkr"])
            .merge(_c_ase_val, on="ASE", how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    ase_agg["fill_rate_pct"] = (
        (ase_agg["confirmed_qty"] / ase_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )

    ret_ase = (
        ret.groupby("ASE")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
        if not ret.empty
        else pd.DataFrame(columns=["ASE", "return_value_lkr"])
    )
    ase_agg = ase_agg.merge(ret_ase, on="ASE", how="left").fillna(0)
    ase_agg["return_rate_pct"] = (
        (ase_agg["return_value_lkr"] / ase_agg["order_value_lkr"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    ase_agg = ase_agg.sort_values("order_value_lkr", ascending=False).head(30)

    ase_perf = [
        AsePerfRow(
            ase=str(r["ASE"]),
            rm=str(r["RM"]),
            province=str(r["Province"]),
            unique_dealers=int(r["unique_dealers"]),
            po_lines=int(r["po_lines"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            return_rate_pct=float(r["return_rate_pct"]),
        )
        for _, r in ase_agg.iterrows()
    ]

    # ── District performance ──────────────────────────────────────────────────
    _c_dist_val = _c_val_by(["Province", "District"])
    dist_agg = po.groupby(["Province", "District"], as_index=False).agg(
        unique_dealers=("Dealer Code", "nunique"),
        po_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        order_value_lkr=("confirmed_value", "sum"),
    )
    if _c_dist_val is not None:
        dist_agg = (
            dist_agg.drop(columns=["order_value_lkr"])
            .merge(_c_dist_val, on=["Province", "District"], how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    dist_agg = dist_agg.sort_values("order_value_lkr", ascending=False)
    total_dist_val = float(dist_agg["order_value_lkr"].sum()) or 1.0
    dist_agg["fill_rate_pct"] = (
        (dist_agg["confirmed_qty"] / dist_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    dist_agg["value_share_pct"] = (dist_agg["order_value_lkr"] / total_dist_val * 100).round(2)

    ret_dist = (
        (
            ret.groupby(["Province", "District"])["confirmed_value"]
            .sum()
            .reset_index()
            .rename(columns={"confirmed_value": "return_value_lkr"})
        )
        if not ret.empty
        else pd.DataFrame(columns=["Province", "District", "return_value_lkr"])
    )
    dist_agg = dist_agg.merge(ret_dist, on=["Province", "District"], how="left").fillna(
        {"return_value_lkr": 0}
    )
    dist_agg["return_rate_pct"] = (
        (dist_agg["return_value_lkr"] / dist_agg["order_value_lkr"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )

    district_perf = [
        DistrictPerfRow(
            province=str(r["Province"]),
            district=str(r["District"]),
            unique_dealers=int(r["unique_dealers"]),
            po_lines=int(r["po_lines"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            value_share_pct=float(r["value_share_pct"]),
            return_rate_pct=float(r["return_rate_pct"]),
        )
        for _, r in dist_agg.head(25).iterrows()
    ]

    # ── Province performance ──────────────────────────────────────────────────
    _c_prov_val = _c_val_by("Province")
    prov_agg = po.groupby("Province", as_index=False).agg(
        unique_dealers=("Dealer Code", "nunique"),
        po_lines=("Sales Document", "count"),
        order_qty=("Order Quantity (Item)", "sum"),
        confirmed_qty=("Confirmed Quantity (Item)", "sum"),
        order_value_lkr=("confirmed_value", "sum"),
    )
    if _c_prov_val is not None:
        prov_agg = (
            prov_agg.drop(columns=["order_value_lkr"])
            .merge(_c_prov_val, on="Province", how="left")
            .fillna({"order_value_lkr": 0.0})
        )
    prov_agg = prov_agg.sort_values("order_value_lkr", ascending=False)
    total_prov_val = float(prov_agg["order_value_lkr"].sum()) or 1.0
    prov_agg["fill_rate_pct"] = (
        (prov_agg["confirmed_qty"] / prov_agg["order_qty"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )
    prov_agg["value_share_pct"] = (prov_agg["order_value_lkr"] / total_prov_val * 100).round(2)

    ret_prov = (
        ret.groupby("Province")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "return_value_lkr"})
        if not ret.empty
        else pd.DataFrame(columns=["Province", "return_value_lkr"])
    )
    prov_agg = prov_agg.merge(ret_prov, on="Province", how="left").fillna(0)
    prov_agg["return_rate_pct"] = (
        (prov_agg["return_value_lkr"] / prov_agg["order_value_lkr"].replace(0, np.nan) * 100)
        .round(2)
        .fillna(0)
    )

    province_analysis = [
        ProvinceAnalysisRow(
            province=str(r["Province"]),
            unique_dealers=int(r["unique_dealers"]),
            po_lines=int(r["po_lines"]),
            order_value_lkr=round(float(r["order_value_lkr"]), 0),
            fill_rate_pct=float(r["fill_rate_pct"]),
            return_rate_pct=float(r["return_rate_pct"]),
            value_share_pct=float(r["value_share_pct"]),
        )
        for _, r in prov_agg.iterrows()
    ]

    return {
        "part_analysis": part_analysis,
        "dealer_perf": dealer_perf,
        "rm_perf": rm_perf,
        "ase_perf": ase_perf,
        "district_perf": district_perf,
        "province_analysis": province_analysis,
    }


def _fraud_alert_check(ret: pd.DataFrame) -> list[dict]:
    """Log and return dealers whose monthly return value exceeds 40% (CLAUDE.md §15)."""
    if ret.empty or "Dealer Code" not in ret.columns or "Year_Month_str" not in ret.columns:
        return []
    from loguru import logger as _log

    monthly_ret = (
        ret.groupby(["Year_Month_str", "Dealer Code", "Dealer Name"])["confirmed_value"]
        .sum()
        .reset_index()
    )
    monthly_total = (
        ret.groupby("Year_Month_str")["confirmed_value"]
        .sum()
        .reset_index()
        .rename(columns={"confirmed_value": "month_total"})
    )
    monthly_ret = monthly_ret.merge(monthly_total, on="Year_Month_str")
    monthly_ret["return_share_pct"] = (
        (monthly_ret["confirmed_value"] / monthly_ret["month_total"].replace(0, np.nan) * 100)
        .round(1)
        .fillna(0)
    )
    alerts = monthly_ret[monthly_ret["return_share_pct"] > 40].sort_values(
        "return_share_pct", ascending=False
    )
    result: list[dict] = []
    for _, r in alerts.head(20).iterrows():
        _log.warning(
            f"Fraud alert: dealer {r['Dealer Code']} ({r.get('Dealer Name', '')}) "
            f"– {r['return_share_pct']:.1f}% of returns in {r['Year_Month_str']}"
        )
        result.append(
            {
                "dealer_code": str(r["Dealer Code"]),
                "dealer_name": str(r.get("Dealer Name", "")),
                "month": str(r["Year_Month_str"]),
                "return_share_pct": float(r["return_share_pct"]),
            }
        )
    return result


# ── Stage 4: Orders EDA ───────────────────────────────────────────────────────


_orders_eda_cache: dict[tuple[str, str, int], bytes] = {}


@router.get("/orders", response_model=OrdersEdaResponse)
def get_orders_eda(
    rejection_limit: int = Query(200, le=1000),
    dealer_type: str = Query("MC", pattern="^(MC|OBM|ALL)$"),
    mc_category: str = Query("ALL", pattern="^(ALL|Lubricant|Battery|Tyre|SpareParts)$"),
) -> OrdersEdaResponse | Response:
    _cache_key = (dealer_type, mc_category, rejection_limit)
    if _cache_key in _orders_eda_cache:
        return Response(content=_orders_eda_cache[_cache_key], media_type="application/json")

    # Load once — both filtered views and full dataset are derived from these
    orders_full = get_orders_clean()
    rej_full = get_orders_rejection_log()
    orders = orders_full
    rej = rej_full

    # Filter by dealer_type and (optionally) MC sub-category
    actual_mc_cat = _MC_CAT_API_MAP.get(mc_category, "ALL")
    if dealer_type != "ALL" and not orders.empty and "dealer_type" in orders.columns:
        orders = orders[orders["dealer_type"] == dealer_type]
        if not rej.empty and "dealer_type" in rej.columns:
            rej = rej[rej["dealer_type"] == dealer_type]
    if (
        dealer_type == "MC"
        and actual_mc_cat != "ALL"
        and not orders.empty
        and "mc_category" in orders.columns
    ):
        orders = orders[orders["mc_category"] == actual_mc_cat]
        if not rej.empty and "mc_category" in rej.columns:
            rej = rej[rej["mc_category"] == actual_mc_cat]

    if orders.empty:
        return OrdersEdaResponse(
            total_po=0,
            total_returns=0,
            avg_fill_rate=0.0,
            avg_lead_time_days=0.0,
            fill_rate_lt1_count=0,
            top_dealers=[],
            monthly_trend=[],
            rejections=[],
        )

    po_mask = orders["doc_type"] == "PO"
    ret_mask = orders["doc_type"] == "Return"
    total_po = int(po_mask.sum())
    total_returns = int(ret_mask.sum())

    # All original C-orders = complete demand signal (non-cancelled + cancelled, clean + rejected).
    # Used for order_value_lkr = "Order Received" matching data.xlsx Order_Received column.
    _has_rt = "return_type" in orders.columns
    _has_rt_rej = not rej.empty and "return_type" in rej.columns
    _empty_df = pd.DataFrame()
    all_c = pd.concat(
        [
            orders[po_mask],
            orders[orders["return_type"] == "Cancelled Order"] if _has_rt else _empty_df,
            rej[rej["doc_type"] == "PO"]
            if not rej.empty and "doc_type" in rej.columns
            else _empty_df,
            rej[rej["return_type"] == "Cancelled Order"] if _has_rt_rej else _empty_df,
        ],
        ignore_index=True,
    )

    # Volume-weighted fill rate: total confirmed / total ordered (not per-line mean)
    po_lines = orders[po_mask]
    total_order_qty = float(po_lines["Order Quantity (Item)"].sum()) if not po_lines.empty else 0.0
    total_confirmed_qty = (
        float(po_lines["Confirmed Quantity (Item)"].sum()) if not po_lines.empty else 0.0
    )
    fill_rate_lt1 = (
        int((orders["fill_rate"].dropna() < 1.0).sum()) if "fill_rate" in orders.columns else 0
    )
    avg_lead_time = (
        float(orders["lead_time_days"].dropna().mean())
        if "lead_time_days" in orders.columns
        else 0.0
    )

    # Rejection rate + TRUE fill rate: include fully-rejected lines in denominator
    rej_po_lines = (
        rej[rej["doc_type"] == "PO"]
        if not rej.empty and "doc_type" in rej.columns
        else (rej if not rej.empty else pd.DataFrame())
    )
    rej_po_count = int(len(rej_po_lines))
    rej_order_qty = (
        float(rej_po_lines["Order Quantity (Item)"].sum()) if not rej_po_lines.empty else 0.0
    )
    total_po_incl_rej = total_po + rej_po_count
    rejection_rate_pct = (
        round(rej_po_count / total_po_incl_rej * 100, 2) if total_po_incl_rej > 0 else 0.0
    )
    # True fill rate: rejected lines add to ordered qty but contribute 0 confirmed qty
    true_order_qty = total_order_qty + rej_order_qty
    avg_fill_rate = round(total_confirmed_qty / true_order_qty, 4) if true_order_qty > 0 else 0.0

    # Value metrics: Order Received = Net Value (Item) for ALL C-orders (incl. cancelled + rejected)
    # This matches data.xlsx Order_Received which counts every C-order regardless of cancellation.
    total_order_value_lkr = 0.0
    total_confirmed_value_lkr = 0.0
    if not all_c.empty and "Net Value (Item)" in all_c.columns:
        total_order_value_lkr = float(all_c["Net Value (Item)"].sum())
    if not po_lines.empty and "confirmed_value" in po_lines.columns:
        total_confirmed_value_lkr = float(po_lines["confirmed_value"].sum())
    value_fill_rate_pct = (
        round(total_confirmed_value_lkr / total_order_value_lkr * 100, 2)
        if total_order_value_lkr > 0
        else 0.0
    )

    # Top dealers by order intake (all original C-orders, Net Value Item)
    top_dealers: list[OrdersEdaDealer] = []
    if not all_c.empty and "Dealer Name" in all_c.columns and "Dealer Code" in all_c.columns:
        dealer_grp = (
            all_c.groupby(["Dealer Code", "Dealer Name"])
            .agg(
                order_count=("Sales Document", "count"),
                total_value=("Net Value (Item)", "sum"),
            )
            .sort_values("total_value", ascending=False)
            .head(10)
            .reset_index()
        )
        for _, r in dealer_grp.iterrows():
            top_dealers.append(
                OrdersEdaDealer(
                    dealer=str(r["Dealer Name"]),
                    order_count=int(r["order_count"]),
                    total_value_lkr=float(r.get("total_value", 0.0) or 0.0),
                )
            )

    # Monthly trend — total_value_lkr = ALL C-orders (demand signal)
    # confirmed_value_lkr = delivered
    monthly: list[OrdersEdaMonthlyPoint] = []
    if "Year_Month_str" in orders.columns:
        # All-C monthly intake (incl. cancelled)
        _c_monthly = (
            all_c.groupby("Year_Month_str")["Net Value (Item)"]
            .sum()
            .rename("all_c_val")
            .reset_index()
            if not all_c.empty and "Year_Month_str" in all_c.columns
            else pd.DataFrame(columns=["Year_Month_str", "all_c_val"])
        )
        grp = (
            orders.groupby(["Year_Month_str", "doc_type"])
            .agg(
                cnt=("doc_type", "count"),
                conf_val=("confirmed_value", "sum"),
            )
            .reset_index()
        )
        periods = sorted(
            set(grp["Year_Month_str"].unique()) | set(_c_monthly["Year_Month_str"].unique())
        )
        for p in periods:
            sub = grp[grp["Year_Month_str"] == p]
            po_r = sub[sub["doc_type"] == "PO"]
            ret_r = sub[sub["doc_type"] == "Return"]
            _c_val_row = _c_monthly[_c_monthly["Year_Month_str"] == p]
            _total_val = float(_c_val_row["all_c_val"].values[0]) if len(_c_val_row) else 0.0
            monthly.append(
                OrdersEdaMonthlyPoint(
                    period=str(p),
                    po_count=int(po_r["cnt"].sum()) if len(po_r) else 0,
                    return_count=int(ret_r["cnt"].sum()) if len(ret_r) else 0,
                    total_value_lkr=round(_total_val, 0),
                    confirmed_value_lkr=round(float(po_r["conf_val"].sum()), 0)
                    if len(po_r)
                    else 0.0,
                )
            )

    # Rejection rows (fully rejected lines)
    rejections: list[OrdersEdaRejectionRow] = []
    if not rej.empty:
        sample = rej.head(rejection_limit)
        for _, r in sample.iterrows():
            rejections.append(
                OrdersEdaRejectionRow(
                    material=str(r.get("Material", "")),
                    description=str(r.get("Material Description", "")),
                    customer=str(r.get("Sold-To Party Name", r.get("Sold-to Party", ""))),
                    document_date=str(r.get("Document Date", ""))[:10],
                    order_qty=float(r.get("Order Quantity (Item)", 0) or 0),
                    lost_qty=float(r.get("lost_qty", 0) or 0),
                    fill_rate=float(r.get("fill_rate", 0) or 0),
                )
            )

    # Orders received breakdown — document-level fulfillment classification
    orders_received: dict[str, int] = {
        "total_documents": 0,
        "fully_filled": 0,
        "partial_fill": 0,
        "complete_zero": 0,
    }
    fulfillment_data: FulfillmentAnalysis | None = None
    if not orders.empty:
        all_po_lines = pd.concat(
            [
                orders[orders["doc_type"] == "PO"],
                rej[rej["doc_type"] == "PO"] if not rej.empty else orders.iloc[:0],
            ],
            ignore_index=True,
        )
        if not all_po_lines.empty and "Sales Document" in all_po_lines.columns:
            doc_status = all_po_lines.groupby("Sales Document").apply(
                lambda g: (
                    "complete_zero"
                    if (g["Confirmed Quantity (Item)"] == 0).all()
                    else "fully_filled"
                    if (g["Confirmed Quantity (Item)"] >= g["Order Quantity (Item)"]).all()
                    else "partial_fill"
                ),
                include_groups=False,
            )
            counts = doc_status.value_counts()
            orders_received = {
                "total_documents": int(len(doc_status)),
                "fully_filled": int(counts.get("fully_filled", 0)),
                "partial_fill": int(counts.get("partial_fill", 0)),
                "complete_zero": int(counts.get("complete_zero", 0)),
            }

            # Line-level + document-level fulfillment analysis
            _conf_col = "Confirmed Quantity (Item)"
            _ord_col = "Order Quantity (Item)"
            _val_col = (
                "confirmed_value"
                if "confirmed_value" in all_po_lines.columns
                else "Net Value (Item)"
            )
            total_lines = len(all_po_lines)
            fully_conf = all_po_lines[all_po_lines[_conf_col] >= all_po_lines[_ord_col]]
            part_conf = all_po_lines[
                (all_po_lines[_conf_col] > 0) & (all_po_lines[_conf_col] < all_po_lines[_ord_col])
            ]
            fully_rej = all_po_lines[all_po_lines[_conf_col] == 0]

            def _bucket(df: pd.DataFrame, n_total: int) -> FulfillmentLineBucket:
                return FulfillmentLineBucket(
                    lines=len(df),
                    pct_of_lines=round(len(df) / n_total * 100, 1) if n_total else 0.0,
                    order_qty=float(df[_ord_col].sum()),
                    confirmed_qty=float(df[_conf_col].sum()),
                    confirmed_value_lkr=round(float(df[_val_col].sum()), 0),
                )

            total_docs = int(len(doc_status))

            def _safe_pct(n: int) -> float:
                return round(n / total_docs * 100, 1) if total_docs else 0.0

            fulfillment_data = FulfillmentAnalysis(
                total_lines=total_lines,
                fully_confirmed=_bucket(fully_conf, total_lines),
                partially_confirmed=_bucket(part_conf, total_lines),
                fully_rejected=_bucket(fully_rej, total_lines),
                total_docs=total_docs,
                docs_fully_filled=int(counts.get("fully_filled", 0)),
                docs_fully_filled_pct=_safe_pct(int(counts.get("fully_filled", 0))),
                docs_partially_filled=int(counts.get("partial_fill", 0)),
                docs_partially_filled_pct=_safe_pct(int(counts.get("partial_fill", 0))),
                docs_complete_zero=int(counts.get("complete_zero", 0)),
                docs_complete_zero_pct=_safe_pct(int(counts.get("complete_zero", 0))),
            )

    # Rejection reasons — fully-rejected PO lines (confirmed = 0)
    rej_reasons: list[OrdersEdaRejectionReasonRow] = []
    rej_reason_col = next(
        (
            c
            for c in ["Rejection Reason Description", "Reason for Rejection"]
            if not rej.empty and c in rej.columns
        ),
        None,
    )
    if not rej.empty and rej_reason_col:
        po_rej = rej[rej["doc_type"] == "PO"] if "doc_type" in rej.columns else rej
        if not po_rej.empty:
            reason_grp = (
                po_rej.groupby(rej_reason_col)
                .agg(
                    rejected_lines=("Sales Document", "count"),
                    rejected_qty=("Order Quantity (Item)", "sum"),
                )
                .reset_index()
            )
            total_rej = int(reason_grp["rejected_lines"].sum()) or 1
            for _, r in reason_grp.sort_values("rejected_lines", ascending=False).iterrows():
                rej_reasons.append(
                    OrdersEdaRejectionReasonRow(
                        reason=str(r[rej_reason_col]),
                        rejected_lines=int(r["rejected_lines"]),
                        rejected_qty=float(r["rejected_qty"]),
                        share_pct=round(float(r["rejected_lines"]) / total_rej * 100, 2),
                    )
                )

    # Return orders analysis — H-type Return Orders only (not cancelled C-orders)
    total_return_value_lkr = 0.0
    return_rate_value_pct = 0.0
    return_type_breakdown: dict[str, int] = {}
    return_order_reasons: list[OrdersEdaRejectionReasonRow] = []

    ret_lines = orders[orders["doc_type"] == "Return"]
    if not ret_lines.empty:
        h_ret = (
            ret_lines[ret_lines["return_type"] == "Return Order"]
            if "return_type" in ret_lines.columns
            else ret_lines
        )
        total_return_value_lkr = float(
            (h_ret["Confirmed Quantity (Item)"] * h_ret["Net Price"]).sum()
        )
        gross_value = total_order_value_lkr + total_return_value_lkr
        if gross_value > 0:
            return_rate_value_pct = round(total_return_value_lkr / gross_value * 100, 2)

        if "return_type" in ret_lines.columns:
            return_type_breakdown = {
                str(k): int(v)
                for k, v in ret_lines[ret_lines["return_type"] != ""]["return_type"]
                .value_counts()
                .items()
            }

        rr_col_ret = next(
            (
                c
                for c in [
                    "return_reason",
                    "Rejection Reason Description",
                    "Order Reason Description",
                    "Reason for Rejection",
                ]
                if c in ret_lines.columns
            ),
            None,
        )
        if rr_col_ret:
            non_blank = ret_lines[ret_lines[rr_col_ret].fillna("").str.strip() != ""]
            if not non_blank.empty:
                rr_grp = (
                    non_blank.groupby(rr_col_ret)
                    .agg(
                        rejected_lines=("Sales Document", "count"),
                        rejected_qty=("Order Quantity (Item)", "sum"),
                    )
                    .reset_index()
                )
                total_rr = int(rr_grp["rejected_lines"].sum()) or 1
                for _, r in rr_grp.sort_values("rejected_lines", ascending=False).iterrows():
                    return_order_reasons.append(
                        OrdersEdaRejectionReasonRow(
                            reason=str(r[rr_col_ret]),
                            rejected_lines=int(r["rejected_lines"]),
                            rejected_qty=float(r["rejected_qty"]),
                            share_pct=round(float(r["rejected_lines"]) / total_rr * 100, 2),
                        )
                    )

    # Per-segment analysis tables (filtered data) — pass all_c for accurate order_value_lkr
    po_filtered = orders[orders["doc_type"] == "PO"]
    ret_filtered = orders[orders["doc_type"] == "Return"]
    analysis = _compute_analysis_tables(po_filtered, ret_filtered, all_c=all_c)

    # Business insights — full dataset (no second disk read: reuse already-loaded orders_full)
    full_po_all = (
        orders_full[orders_full["doc_type"] == "PO"] if not orders_full.empty else orders_full
    )
    # Build full all_c from orders_full (unfiltered) for accurate province metrics in insights
    _has_rt_full = "return_type" in orders_full.columns
    _has_rt_rej_full = not rej_full.empty and "return_type" in rej_full.columns
    full_all_c = pd.concat(
        [
            orders_full[orders_full["doc_type"] == "PO"] if not orders_full.empty else _empty_df,
            orders_full[orders_full["return_type"] == "Cancelled Order"]
            if _has_rt_full
            else _empty_df,
            rej_full[rej_full["doc_type"] == "PO"]
            if not rej_full.empty and "doc_type" in rej_full.columns
            else _empty_df,
            rej_full[rej_full["return_type"] == "Cancelled Order"]
            if _has_rt_rej_full
            else _empty_df,
        ],
        ignore_index=True,
    )
    insights = _business_insights(full_po_all, full_all_c=full_all_c)

    # Fraud alerts — check full return data across all segments (CLAUDE.md §15)
    full_ret_all = (
        orders_full[orders_full["doc_type"] == "Return"]
        if not orders_full.empty
        else pd.DataFrame()
    )
    fraud_alerts = _fraud_alert_check(full_ret_all)

    # All-MC monthly order value broken down by sub-category (always from full MC PO data)
    mc_monthly_category: list[McMonthlyCategoryPoint] = []
    if (
        not orders_full.empty
        and "dealer_type" in orders_full.columns
        and "mc_category" in orders_full.columns
    ):
        mc_po_full = orders_full[
            (orders_full["dealer_type"] == "MC") & (orders_full["doc_type"] == "PO")
        ]
        if not mc_po_full.empty and "Year_Month_str" in mc_po_full.columns:
            mc_pivot = (
                mc_po_full.groupby(["Year_Month_str", "mc_category"])["confirmed_value"]
                .sum()
                .reset_index()
            )
            for period in sorted(mc_pivot["Year_Month_str"].unique()):
                sub = mc_pivot[mc_pivot["Year_Month_str"] == period].set_index("mc_category")[
                    "confirmed_value"
                ]
                mc_monthly_category.append(
                    McMonthlyCategoryPoint(
                        period=str(period),
                        lubricant_lkr=round(float(sub.get("Lubricant", 0.0)), 0),
                        battery_lkr=round(float(sub.get("Battery", 0.0)), 0),
                        tyre_lkr=round(float(sub.get("Tyre", 0.0)), 0),
                        spare_parts_lkr=round(float(sub.get("Spare Parts", 0.0)), 0),
                        total_lkr=round(float(sub.sum()), 0),
                    )
                )

    # ── New KPI fields ────────────────────────────────────────────────────────
    _po_nv = float(po_lines["Net Value (Item)"].sum()) if not po_lines.empty else 0.0
    _po_cv = float(po_lines["confirmed_value"].sum()) if not po_lines.empty else 0.0
    _rej_nv = float(rej_po_lines["Net Value (Item)"].sum()) if not rej_po_lines.empty else 0.0
    unfulfill_value_lkr = round(max(0.0, _po_nv - _po_cv + _rej_nv), 0)
    sales_qty = float(po_lines["Confirmed Quantity (Item)"].sum()) if not po_lines.empty else 0.0
    unique_skus = (
        int(po_lines["Material"].nunique())
        if not po_lines.empty and "Material" in po_lines.columns
        else 0
    )
    total_po_documents = (
        int(all_c["Sales Document"].nunique())
        if not all_c.empty and "Sales Document" in all_c.columns
        else 0
    )

    _result = OrdersEdaResponse(
        total_po=total_po,
        total_returns=total_returns,
        avg_fill_rate=avg_fill_rate,
        avg_lead_time_days=round(avg_lead_time, 1),
        fill_rate_lt1_count=fill_rate_lt1,
        total_order_value_lkr=round(total_order_value_lkr, 0),
        total_confirmed_value_lkr=round(total_confirmed_value_lkr, 0),
        value_fill_rate_pct=value_fill_rate_pct,
        total_return_value_lkr=round(total_return_value_lkr, 0),
        return_rate_value_pct=return_rate_value_pct,
        unfulfill_value_lkr=unfulfill_value_lkr,
        sales_qty=sales_qty,
        unique_skus=unique_skus,
        total_po_documents=total_po_documents,
        return_type_breakdown=return_type_breakdown,
        return_order_reasons=return_order_reasons,
        top_dealers=top_dealers,
        monthly_trend=monthly,
        rejections=rejections,
        orders_received_breakdown=orders_received,
        rejection_reasons=rej_reasons,
        rejection_rate_pct=rejection_rate_pct,
        fulfillment=fulfillment_data,
        fraud_alerts=fraud_alerts,
        mc_monthly_category=mc_monthly_category,
        **analysis,
        **insights,
    )
    _orders_eda_cache[_cache_key] = _result.model_dump_json().encode()
    return _result


# ── Stage 5: Sales EDA ────────────────────────────────────────────────────────

_sales_eda_cache: dict[tuple[str, str, int], bytes] = {}


@router.get("/sales", response_model=SalesEdaResponse)
def get_sales_eda(
    dealer_type: str = Query("MC", pattern="^(MC|OBM|ALL)$"),
    mc_category: str = Query("ALL", pattern="^(Lubricant|Battery|Tyre|Spare Parts|ALL)$"),
    year: int = Query(0, ge=0),  # 0 = auto-select latest year in data
) -> SalesEdaResponse | Response:
    _cache_key = (dealer_type, mc_category, year)
    if _cache_key in _sales_eda_cache:
        return Response(content=_sales_eda_cache[_cache_key], media_type="application/json")

    sales = get_sales_clean()
    if sales.empty:
        return SalesEdaResponse()

    # ── Dealer-type filter ───────────────────────────────────────
    if dealer_type != "ALL" and "dealer_type" in sales.columns:
        sales = sales[sales["dealer_type"] == dealer_type]

    # ── MC sub-category filter ───────────────────────────────────
    if mc_category != "ALL" and dealer_type == "MC" and "mc_category" in sales.columns:
        sales = sales[sales["mc_category"] == mc_category]

    if sales.empty:
        return SalesEdaResponse()

    # ── Year filter (KPIs + all tables default to latest year) ───
    available_years: list[int] = []
    data_year = 0
    if "Year_Month_str" in sales.columns:
        _yrs = sorted(sales["Year_Month_str"].str[:4].astype(int).unique().tolist(), reverse=True)
        available_years = _yrs
        data_year = year if year > 0 and year in _yrs else (_yrs[0] if _yrs else 0)
        sales = sales[sales["Year_Month_str"].str[:4].astype(int) == data_year]

    if sales.empty:
        return SalesEdaResponse(data_year=data_year, available_years=available_years)

    # ── Orders-based KPIs (aligned with Performance dashboard) ──────────────────
    # Order_Received  = ALL PO orders placed: clean + cancelled + rejected = gross ordered value
    # Total Sales     = clean PO only (conf_qty > 0, not cancelled)       = fulfilled value
    # Both sourced from orders.xlsx so that Fulfillment % = Total Sales / Order_Received.
    orders_raw = get_orders_clean()
    orders_rej = get_orders_rejection_log()
    _po_monthly: dict[str, float] = {}  # order_received per month
    _fulfilled_monthly: dict[str, float] = {}  # total_sales (fulfilled) per month
    _po_dealer: dict[str, float] = {}  # order_received per dealer
    _fulfilled_dealer: dict[str, float] = {}  # fulfilled per dealer
    _po_district: dict[str, float] = {}
    order_received_total = 0.0
    total_sales_from_orders = 0.0

    _NV = "Net Value (Item)"

    def _filter_ord(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if dealer_type != "ALL" and "dealer_type" in df.columns:
            df = df[df["dealer_type"] == dealer_type]
        if mc_category != "ALL" and dealer_type == "MC" and "mc_category" in df.columns:
            df = df[df["mc_category"] == mc_category]
        if data_year > 0 and "Year_Month_str" in df.columns:
            df = df[df["Year_Month_str"].str[:4].astype(int) == data_year]
        return df

    if not orders_raw.empty:
        # Fulfilled: clean PO (conf_qty > 0, not cancelled)
        po_clean = _filter_ord(orders_raw[orders_raw["doc_type"] == "PO"].copy())
        # Cancelled POs with partial delivery (stored as Returns in parquet)
        po_cancelled = pd.DataFrame()
        if "return_type" in orders_raw.columns:
            po_cancelled = _filter_ord(
                orders_raw[
                    (orders_raw["doc_type"] == "Return")
                    & (orders_raw["return_type"] == "Cancelled Order")
                ].copy()
            )
        # All rejection-log rows: zero-conf POs + zero-conf cancelled orders.
        # Both belong in Order_Received since they represent attempted but unfulfilled orders.
        rej_po = pd.DataFrame()
        if not orders_rej.empty:
            rej_po = _filter_ord(orders_rej.copy())

        # Total Sales = fulfilled clean PO Net Value (Item)
        if not po_clean.empty and _NV in po_clean.columns:
            total_sales_from_orders = float(po_clean[_NV].sum())
            if "Year_Month_str" in po_clean.columns:
                _fulfilled_monthly = {
                    str(k): float(v)
                    for k, v in po_clean.groupby("Year_Month_str")[_NV].sum().items()
                }
            if "Dealer Name" in po_clean.columns:
                _fulfilled_dealer = {
                    str(k): float(v) for k, v in po_clean.groupby("Dealer Name")[_NV].sum().items()
                }

        # Order Received = clean PO + cancelled PO + rejected PO
        parts = [
            df for df in [po_clean, po_cancelled, rej_po] if not df.empty and _NV in df.columns
        ]
        if parts:
            _want = [_NV, "Year_Month_str", "Dealer Name", "District"]
            combined = pd.concat(
                [d[[c for c in _want if c in d.columns]] for d in parts],
                ignore_index=True,
            )
            order_received_total = float(combined[_NV].sum())
            if "Year_Month_str" in combined.columns:
                _po_monthly = {
                    str(k): float(v)
                    for k, v in combined.groupby("Year_Month_str")[_NV].sum().items()
                }
            if "Dealer Name" in combined.columns:
                _po_dealer = {
                    str(k): float(v) for k, v in combined.groupby("Dealer Name")[_NV].sum().items()
                }
            if "District" in combined.columns:
                _po_district = {
                    str(k): float(v) for k, v in combined.groupby("District")[_NV].sum().items()
                }

    sale = sales[sales["bill_class"] == "sale"]
    ret = sales[sales["bill_class"] == "return"]

    # Billing metrics from sales.xlsx (invoice records) — used for returns & revenue KPIs
    net_billing_value = float(sale["Net Sales"].sum())
    total_return_value = float(abs(ret["Net Sales"].sum()))
    net_value = net_billing_value - total_return_value
    return_rate = round(total_return_value / max(net_billing_value, 1) * 100, 2)
    total_sale_qty = float(sale["SlsVolQty"].sum())
    total_return_qty = float(abs(ret["SlsVolQty"].sum()))
    unique_parts = int(sales["Material"].nunique()) if "Material" in sales.columns else 0
    unique_dealers = int(sales["Payer"].nunique()) if "Payer" in sales.columns else 0

    # ── Monthly trend ────────────────────────────────────────────
    # All sale/return/net values come from billing (sales.xlsx) so they are internally
    # consistent: net_value_lkr = sale_value_lkr − return_value_lkr.
    # order_received_lkr stays on orders.xlsx (supply-chain metric).
    monthly: list[SalesEdaMonthlyPoint] = []
    if "Year_Month_str" in sales.columns:
        grp = (
            sales.groupby(["Year_Month_str", "bill_class"])
            .agg(value=("Net Sales", "sum"), qty=("SlsVolQty", "sum"))
            .reset_index()
            .pivot(index="Year_Month_str", columns="bill_class", values=["value", "qty"])
            .fillna(0)
        )
        grp.columns = pd.Index(["_".join(c) for c in grp.columns])
        grp = grp.reset_index()
        for _, r in grp.sort_values("Year_Month_str").iterrows():
            prd = str(r["Year_Month_str"])
            sv = float(r.get("value_sale", 0))  # billing sales this month
            rv = float(abs(r.get("value_return", 0)))  # billing returns this month
            monthly.append(
                SalesEdaMonthlyPoint(
                    period=prd,
                    sale_value_lkr=round(sv, 0),
                    return_value_lkr=round(rv, 0),
                    net_value_lkr=round(sv - rv, 0),
                    sale_qty=round(float(r.get("qty_sale", 0)), 0),
                    return_qty=round(float(abs(r.get("qty_return", 0))), 0),
                    order_received_lkr=round(float(_po_monthly.get(prd, 0)), 0),
                )
            )

    # ── Part analysis (top 300 by sale value) ───────────────────
    part_rows: list[SalesPartRow] = []
    if "Material" in sales.columns:
        s_grp = sale.groupby("Material").agg(
            sale_lines=("SlsVolQty", "count"),
            sale_qty=("SlsVolQty", "sum"),
            sale_value_lkr=("Net Sales", "sum"),
        )
        r_grp = ret.groupby("Material").agg(
            return_lines=("SlsVolQty", "count"),
            return_qty=("SlsVolQty", lambda x: abs(x.sum())),
            return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
        )
        part_df = s_grp.join(r_grp, how="left").fillna(0).reset_index()
        part_df["net_value_lkr"] = part_df["sale_value_lkr"] - part_df["return_value_lkr"]
        part_df["net_qty"] = part_df["sale_qty"] - part_df["return_qty"]
        part_df["return_rate_pct"] = (
            (part_df["return_value_lkr"] / part_df["sale_value_lkr"].replace(0, np.nan) * 100)
            .fillna(0)
            .round(2)
        )
        for _, p in part_df.sort_values("sale_value_lkr", ascending=False).head(300).iterrows():
            part_rows.append(
                SalesPartRow(
                    material=str(p["Material"]),
                    sale_lines=int(p["sale_lines"]),
                    sale_qty=float(p["sale_qty"]),
                    sale_value_lkr=round(float(p["sale_value_lkr"]), 0),
                    return_lines=int(p.get("return_lines", 0)),
                    return_qty=float(p.get("return_qty", 0)),
                    return_value_lkr=round(float(p.get("return_value_lkr", 0)), 0),
                    net_qty=float(p["net_qty"]),
                    net_value_lkr=round(float(p["net_value_lkr"]), 0),
                    return_rate_pct=float(p["return_rate_pct"]),
                )
            )

    # ── Dealer performance ───────────────────────────────────────
    dealer_rows: list[SalesDealerRow] = []
    if "Payer" in sales.columns:
        dim = ["Payer"] + [
            c for c in ["dealer_type", "Province", "District", "ASE", "RM"] if c in sales.columns
        ]
        s_d = (
            sale.groupby(dim, dropna=False)
            .agg(
                sale_qty=("SlsVolQty", "sum"),
                sale_value_lkr=("Net Sales", "sum"),
                unique_skus=("Material", "nunique"),
            )
            .reset_index()
        )
        r_d = (
            ret.groupby("Payer", dropna=False)
            .agg(
                return_qty=("SlsVolQty", lambda x: abs(x.sum())),
                return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
            )
            .reset_index()
        )
        d_df = s_d.merge(r_d, on="Payer", how="left").fillna(0)
        d_df["return_rate_pct"] = (
            (d_df["return_value_lkr"] / d_df["sale_value_lkr"].replace(0, np.nan) * 100)
            .fillna(0)
            .round(2)
        )
        for _, d in d_df.sort_values("sale_value_lkr", ascending=False).iterrows():
            _d_sv = round(float(d["sale_value_lkr"]), 0)
            _d_orv = round(float(_po_dealer.get(str(d["Payer"]), 0)), 0)
            _d_fulfilled = round(float(_fulfilled_dealer.get(str(d["Payer"]), 0)), 0)
            _d_ful = round(_d_fulfilled / max(_d_orv, 1) * 100, 1) if _d_orv > 0 else 0.0
            dealer_rows.append(
                SalesDealerRow(
                    dealer_name=str(d["Payer"]),
                    dealer_type=str(d.get("dealer_type", "")),
                    province=str(d.get("Province", "")),
                    district=str(d.get("District", "")),
                    ase=str(d.get("ASE", "")),
                    rm=str(d.get("RM", "")),
                    sale_qty=float(d["sale_qty"]),
                    sale_value_lkr=_d_sv,
                    return_qty=float(d.get("return_qty", 0)),
                    return_value_lkr=round(float(d.get("return_value_lkr", 0)), 0),
                    return_rate_pct=float(d["return_rate_pct"]),
                    unique_skus=int(d["unique_skus"]),
                    order_received_lkr=_d_orv,
                    fulfillment_pct=_d_ful,
                )
            )

    def _hier(grp_col: str, extra: list[str], row_cls: type) -> list:
        if grp_col not in sales.columns:
            return []
        cols = [grp_col] + [c for c in extra if c in sales.columns]
        s_h = (
            sale.groupby(cols, dropna=False)
            .agg(
                sale_qty=("SlsVolQty", "sum"),
                sale_value_lkr=("Net Sales", "sum"),
                dealer_count=("Payer", "nunique"),
                unique_skus=("Material", "nunique"),
            )
            .reset_index()
        )
        r_h = (
            ret.groupby(grp_col, dropna=False)
            .agg(
                return_qty=("SlsVolQty", lambda x: abs(x.sum())),
                return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
            )
            .reset_index()
        )
        h = s_h.merge(r_h, on=grp_col, how="left").fillna(0)
        h["return_rate_pct"] = (
            (h["return_value_lkr"] / h["sale_value_lkr"].replace(0, np.nan) * 100)
            .fillna(0)
            .round(2)
        )
        return h.sort_values("sale_value_lkr", ascending=False).to_dict(orient="records")

    rm_dicts = _hier("RM", [], SalesRmRow)
    ase_dicts = _hier("ASE", ["RM"], SalesAseRow)
    district_dicts = _hier("District", ["Province"], SalesDistrictRow)
    province_dicts = _hier("Province", [], SalesProvinceRow)

    def _to_rm(d: dict) -> SalesRmRow:
        return SalesRmRow(
            rm=str(d.get("RM", "")),
            sale_value_lkr=round(float(d.get("sale_value_lkr", 0)), 0),
            return_value_lkr=round(float(d.get("return_value_lkr", 0)), 0),
            return_rate_pct=float(d.get("return_rate_pct", 0)),
            sale_qty=float(d.get("sale_qty", 0)),
            dealer_count=int(d.get("dealer_count", 0)),
            unique_skus=int(d.get("unique_skus", 0)),
        )

    def _to_ase(d: dict) -> SalesAseRow:
        return SalesAseRow(
            ase=str(d.get("ASE", "")),
            rm=str(d.get("RM", "")),
            sale_value_lkr=round(float(d.get("sale_value_lkr", 0)), 0),
            return_value_lkr=round(float(d.get("return_value_lkr", 0)), 0),
            return_rate_pct=float(d.get("return_rate_pct", 0)),
            sale_qty=float(d.get("sale_qty", 0)),
            dealer_count=int(d.get("dealer_count", 0)),
            unique_skus=int(d.get("unique_skus", 0)),
        )

    def _to_district(d: dict) -> SalesDistrictRow:
        return SalesDistrictRow(
            district=str(d.get("District", "")),
            province=str(d.get("Province", "")),
            sale_value_lkr=round(float(d.get("sale_value_lkr", 0)), 0),
            return_value_lkr=round(float(d.get("return_value_lkr", 0)), 0),
            return_rate_pct=float(d.get("return_rate_pct", 0)),
            sale_qty=float(d.get("sale_qty", 0)),
            dealer_count=int(d.get("dealer_count", 0)),
            unique_skus=int(d.get("unique_skus", 0)),
            order_received_lkr=round(float(_po_district.get(str(d.get("District", "")), 0)), 0),
        )

    def _to_province(d: dict) -> SalesProvinceRow:
        return SalesProvinceRow(
            province=str(d.get("Province", "")),
            sale_value_lkr=round(float(d.get("sale_value_lkr", 0)), 0),
            return_value_lkr=round(float(d.get("return_value_lkr", 0)), 0),
            return_rate_pct=float(d.get("return_rate_pct", 0)),
            sale_qty=float(d.get("sale_qty", 0)),
            dealer_count=int(d.get("dealer_count", 0)),
            unique_skus=int(d.get("unique_skus", 0)),
        )

    # ── MC category mix (MC dealers, current filter) ─────────────
    mc_cat_rows: list[SalesMcCategoryRow] = []
    mc_monthly_rows: list[SalesMcMonthlyPoint] = []
    if dealer_type in ("MC", "ALL") and "mc_category" in sales.columns:
        mc_df = sales[(sales["dealer_type"] == "MC") & sales["mc_category"].notna()].copy()
        mc_df = mc_df[mc_df["mc_category"].astype(str) != "nan"]
        if not mc_df.empty:
            mc_sale = mc_df[mc_df["bill_class"] == "sale"]
            mc_ret = mc_df[mc_df["bill_class"] == "return"]
            s_mc = (
                mc_sale.groupby("mc_category")
                .agg(
                    sale_lines=("SlsVolQty", "count"),
                    sale_qty=("SlsVolQty", "sum"),
                    sale_value_lkr=("Net Sales", "sum"),
                    unique_skus=("Material", "nunique"),
                )
                .reset_index()
            )
            r_mc = (
                mc_ret.groupby("mc_category")
                .agg(
                    return_value_lkr=("Net Sales", lambda x: abs(x.sum())),
                )
                .reset_index()
            )
            mc_agg = s_mc.merge(r_mc, on="mc_category", how="left").fillna(0)
            total_mc_sale = float(mc_agg["sale_value_lkr"].sum())
            mc_agg["value_share_pct"] = (
                mc_agg["sale_value_lkr"] / max(total_mc_sale, 1) * 100
            ).round(2)
            mc_agg["return_rate_pct"] = (
                (mc_agg["return_value_lkr"] / mc_agg["sale_value_lkr"].replace(0, np.nan) * 100)
                .fillna(0)
                .round(2)
            )
            cat_order = {"Lubricant": 0, "Battery": 1, "Tyre": 2, "Spare Parts": 3}
            mc_agg["_ord"] = mc_agg["mc_category"].map(cat_order).fillna(99)
            for _, m in mc_agg.sort_values("_ord").iterrows():
                mc_cat_rows.append(
                    SalesMcCategoryRow(
                        mc_category=str(m["mc_category"]),
                        sale_lines=int(m["sale_lines"]),
                        sale_qty=float(m["sale_qty"]),
                        sale_value_lkr=round(float(m["sale_value_lkr"]), 0),
                        return_value_lkr=round(float(m.get("return_value_lkr", 0)), 0),
                        value_share_pct=float(m["value_share_pct"]),
                        return_rate_pct=float(m["return_rate_pct"]),
                        unique_skus=int(m["unique_skus"]),
                    )
                )

            # monthly by MC category
            if "Year_Month_str" in mc_sale.columns:
                mc_mo = (
                    mc_sale.groupby(["Year_Month_str", "mc_category"])["Net Sales"]
                    .sum()
                    .reset_index()
                    .pivot(index="Year_Month_str", columns="mc_category", values="Net Sales")
                    .fillna(0)
                    .reset_index()
                )
                for col in ["Lubricant", "Battery", "Tyre", "Spare Parts"]:
                    if col not in mc_mo.columns:
                        mc_mo[col] = 0.0
                for _, row in mc_mo.sort_values("Year_Month_str").iterrows():
                    mc_monthly_rows.append(
                        SalesMcMonthlyPoint(
                            period=str(row["Year_Month_str"]),
                            lubricant_lkr=round(float(row.get("Lubricant", 0)), 0),
                            battery_lkr=round(float(row.get("Battery", 0)), 0),
                            tyre_lkr=round(float(row.get("Tyre", 0)), 0),
                            spare_parts_lkr=round(float(row.get("Spare Parts", 0)), 0),
                            total_lkr=round(
                                float(
                                    row.get("Lubricant", 0)
                                    + row.get("Battery", 0)
                                    + row.get("Tyre", 0)
                                    + row.get("Spare Parts", 0)
                                ),
                                0,
                            ),
                        )
                    )

    fulfillment_pct = (
        round(total_sales_from_orders / max(order_received_total, 1) * 100, 1)
        if order_received_total > 0
        else 0.0
    )

    _result = SalesEdaResponse(
        data_year=data_year,
        available_years=available_years,
        total_sale_value_lkr=round(net_billing_value, 0),  # billed sales (sales.xlsx)
        total_return_value_lkr=round(total_return_value, 0),
        net_sale_value_lkr=round(net_value, 0),  # net billed revenue (sales.xlsx)
        return_rate_pct=return_rate,
        total_sale_qty=total_sale_qty,
        total_return_qty=total_return_qty,
        unique_parts=unique_parts,
        unique_dealers=unique_dealers,
        total_sale_lines=int(len(sale)),
        total_return_lines=int(len(ret)),
        order_received_lkr=round(order_received_total, 0),
        fulfillment_pct=fulfillment_pct,
        monthly_trend=monthly,
        part_analysis=part_rows,
        dealer_perf=dealer_rows,
        rm_perf=[_to_rm(d) for d in rm_dicts],
        ase_perf=[_to_ase(d) for d in ase_dicts],
        district_perf=[_to_district(d) for d in district_dicts],
        province_perf=[_to_province(d) for d in province_dicts],
        mc_category_mix=mc_cat_rows,
        mc_monthly_category=mc_monthly_rows,
    )
    _sales_eda_cache[_cache_key] = _result.model_dump_json().encode()
    return _result


# ── Stage 7: Stock Movements ──────────────────────────────────────────────────


@router.get("/movements", response_model=MovementsResponse)
def get_movements() -> MovementsResponse:
    sm = get_stock_movements()

    if sm.empty:
        return MovementsResponse(
            total_records=0,
            date_from="",
            date_to="",
            by_class={},
            monthly_trend=[],
        )

    total_records = len(sm)
    date_from = str(sm["posting_date"].min())[:10]
    date_to = str(sm["posting_date"].max())[:10]

    by_class: dict[str, int] = {
        str(k): int(v) for k, v in sm["movement_class"].value_counts().items()
    }

    # Monthly trend by movement class (top 5 classes)
    top_classes = sm["movement_class"].value_counts().head(5).index.tolist()
    monthly: list[MovementMonthlyPoint] = []
    if "year_month_str" in sm.columns:
        grp = (
            sm[sm["movement_class"].isin(top_classes)]
            .groupby(["year_month_str", "movement_class"])
            .agg(qty=("qty", "sum"), value_lkr=("value_lkr", "sum"))
            .reset_index()
            .sort_values(["year_month_str", "movement_class"])
        )
        for _, r in grp.iterrows():
            monthly.append(
                MovementMonthlyPoint(
                    period=str(r["year_month_str"]),
                    movement_class=str(r["movement_class"]),
                    qty=float(r["qty"]),
                    value_lkr=float(r["value_lkr"]),
                )
            )

    return MovementsResponse(
        total_records=total_records,
        date_from=date_from,
        date_to=date_to,
        by_class=by_class,
        monthly_trend=monthly,
    )


# ── Stage 8: Spare Parts EDA ──────────────────────────────────────────────────


@router.get("/spare-parts", response_model=SparePartsEdaResponse)
def get_spare_parts_eda() -> SparePartsEdaResponse:
    spf = get_spare_parts_features()
    log = get_ingestion_log()

    if spf.empty:
        return SparePartsEdaResponse(
            total_skus=0,
            in_ssop_count=0,
            total_issue_value_lkr=0.0,
            median_cv=0.0,
            median_p_zero=0.0,
            demand_category_counts={},
            p_zero_bins=[],
            ingestion_summary=[],
            top_skus=[],
            intermittent_skus=[],
        )

    total_skus = len(spf)
    in_ssop = int(spf["in_ssop"].sum()) if "in_ssop" in spf.columns else 0

    total_issue_value_lkr = (
        float(spf["total_issue_value_lkr"].sum()) if "total_issue_value_lkr" in spf.columns else 0.0
    )
    median_cv = float(spf["cv"].median()) if "cv" in spf.columns else 0.0
    median_p_zero = float(spf["p_zero"].median()) if "p_zero" in spf.columns else 0.0

    demand_cat: dict[str, int] = {}
    if "demand_category" in spf.columns:
        demand_cat = {str(k): int(v) for k, v in spf["demand_category"].value_counts().items()}

    # p_zero histogram in 0.1 buckets
    p_zero_bins: list[dict[str, float | int]] = []
    if "p_zero" in spf.columns:
        bins = [i / 10 for i in range(11)]
        labels = [f"{bins[i]:.1f}–{bins[i+1]:.1f}" for i in range(len(bins) - 1)]
        cut = pd.cut(spf["p_zero"].clip(0, 1), bins=bins, labels=labels, include_lowest=True)
        vc = cut.value_counts().sort_index()
        for label, cnt in vc.items():
            lo, hi = float(str(label).split("–")[0]), float(str(label).split("–")[1])
            p_zero_bins.append({"bin": str(label), "lo": lo, "hi": hi, "count": int(cnt)})

    # Top 20 SKUs by issue value — Pareto
    top_skus: list[TopSkuRow] = []
    if "total_issue_value_lkr" in spf.columns:
        top_df = spf.nlargest(20, "total_issue_value_lkr").reset_index(drop=True)
        grand_total = float(spf["total_issue_value_lkr"].sum()) or 1.0
        cumsum = 0.0
        for idx, r in top_df.iterrows():
            val = float(r["total_issue_value_lkr"])
            cumsum += val
            top_skus.append(
                TopSkuRow(
                    rank=int(idx) + 1,
                    material_9=str(r.get("material_9", "")),
                    description=str(r.get("description", "")),
                    demand_category=str(r.get("demand_category", "")) or None,
                    total_issue_value_lkr=round(val, 0),
                    total_issue_qty=float(r.get("total_issue_qty", 0) or 0),
                    cumulative_share_pct=round(cumsum / grand_total * 100, 1),
                )
            )

    # Intermittent / lumpy SKUs — p_zero ≥ 0.7, has ever had demand
    intermittent_skus: list[IntermittentSkuRow] = []
    if "p_zero" in spf.columns and "active_months" in spf.columns:
        int_df = spf[(spf["p_zero"] >= 0.7) & (spf["active_months"] > 0)].copy()
        if "total_issue_value_lkr" in int_df.columns:
            int_df = int_df.sort_values("total_issue_value_lkr", ascending=False)
        for _, r in int_df.head(200).iterrows():
            intermittent_skus.append(
                IntermittentSkuRow(
                    material_9=str(r.get("material_9", "")),
                    description=str(r.get("description", "")),
                    demand_category=str(r.get("demand_category", "")),
                    p_zero=round(float(r.get("p_zero", 0) or 0), 3),
                    cv=round(float(r.get("cv", 0) or 0), 3),
                    active_months=int(r.get("active_months", 0) or 0),
                    avg_monthly_demand=round(float(r.get("avg_monthly_demand", 0) or 0), 2),
                    total_issue_value_lkr=round(float(r.get("total_issue_value_lkr", 0) or 0), 0),
                )
            )

    # Ingestion log summary
    ingestion_summary: list[dict[str, str | int]] = []
    if not log.empty:
        for _, r in log.iterrows():
            ingestion_summary.append(
                {
                    "filename": str(r.get("filename", "")),
                    "rows_in": int(r.get("rows_in", 0)),
                    "inserted": int(r.get("inserted", 0)),
                    "duplicates_skipped": int(r.get("duplicates_skipped", 0)),
                }
            )

    return SparePartsEdaResponse(
        total_skus=total_skus,
        in_ssop_count=in_ssop,
        total_issue_value_lkr=round(total_issue_value_lkr, 0),
        median_cv=round(median_cv, 3),
        median_p_zero=round(median_p_zero, 3),
        demand_category_counts=demand_cat,
        p_zero_bins=p_zero_bins,
        ingestion_summary=ingestion_summary,
        top_skus=top_skus,
        intermittent_skus=intermittent_skus,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Market Basket Analysis endpoint  (orders_clean — MC dealers only)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/market-basket")
def market_basket(
    min_support: float = Query(0.01, ge=0.001, le=1.0),
    min_confidence: float = Query(0.05, ge=0.01, le=1.0),
    min_lift: float = Query(1.0, ge=0.0),
    max_rules: int = Query(200, ge=1, le=1000),
) -> MarketBasketResponse:
    """Market Basket Analysis on MC dealer purchase orders (orders_clean).

    Business meaning: discovers which spare parts are co-ordered in the same
    purchase order, enabling cross-selling recommendations and kitting decisions.
    Basket = one Sales Document (purchase order). Items = Material (part number).
    Only MC-type dealers from dealers.xlsx are included.
    """
    from mlxtend.frequent_patterns import association_rules, fpgrowth
    from mlxtend.preprocessing import TransactionEncoder

    raw = get_orders_clean()
    df = raw[(raw["dealer_type"] == "MC") & (raw["doc_type"] == "PO")].copy()

    # Build baskets: one list of unique materials per Sales Document (purchase order)
    baskets = (
        df.groupby("Sales Document")["Material"]
        .apply(lambda items: sorted(set(str(m).strip() for m in items)))
        .tolist()
    )

    total_baskets = len(baskets)
    multi_item = sum(1 for b in baskets if len(b) > 1)
    all_mats = sorted({m for b in baskets for m in b})

    # Top materials by frequency
    mat_counts: dict[str, int] = {}
    for b in baskets:
        for m in b:
            mat_counts[m] = mat_counts.get(m, 0) + 1
    top_materials = [
        {"material": k, "count": v, "support": round(v / total_baskets, 4)}
        for k, v in sorted(mat_counts.items(), key=lambda x: -x[1])[:30]
    ]

    # Encode & mine
    te = TransactionEncoder()
    te_arr = te.fit(baskets).transform(baskets)
    basket_df = pd.DataFrame(te_arr, columns=te.columns_)

    fi = fpgrowth(basket_df, min_support=min_support, use_colnames=True)
    fi["count"] = (fi["support"] * total_baskets).round().astype(int)

    frequent_itemsets: list[FrequentItemset] = [
        FrequentItemset(
            items=sorted(row["itemsets"]),
            support=round(float(row["support"]), 4),
            count=int(row["count"]),
        )
        for _, row in fi.sort_values("support", ascending=False).head(50).iterrows()
    ]

    rules_out: list[AssociationRule] = []
    if len(fi) > 0:
        rules = association_rules(fi, metric="lift", min_threshold=min_lift, num_itemsets=len(fi))
        rules = rules[rules["confidence"] >= min_confidence]
        rules = rules.sort_values("lift", ascending=False).head(max_rules)
        for _, r in rules.iterrows():
            conv = float(r["conviction"]) if not np.isinf(r["conviction"]) else None
            rules_out.append(
                AssociationRule(
                    antecedents=sorted(r["antecedents"]),
                    consequents=sorted(r["consequents"]),
                    support=round(float(r["support"]), 4),
                    confidence=round(float(r["confidence"]), 4),
                    lift=round(float(r["lift"]), 4),
                    conviction=round(conv, 4) if conv is not None else None,
                    antecedent_support=round(float(r["antecedent support"]), 4),
                    consequent_support=round(float(r["consequent support"]), 4),
                )
            )

    # Co-occurrence groups: all combos of size 2–4 that appear on the same invoice.
    # Cap combo size at 4 to keep payload manageable for large baskets.
    import math
    from itertools import combinations

    group_counts: dict[tuple[str, ...], int] = {}
    for basket in baskets:
        for r in range(2, len(basket) + 1):
            for combo in combinations(basket, r):
                key = tuple(sorted(combo))
                group_counts[key] = group_counts.get(key, 0) + 1

    # Keep only groups that appear in at least 2 baskets
    groups: list[CoOccurrenceGroup] = []
    for key, cnt in sorted(group_counts.items(), key=lambda x: -x[1]):
        if cnt < 2:
            continue
        grp_sup = cnt / total_baskets
        # Lift = support(group) / product of individual supports
        ind_sups = [mat_counts.get(m, 0) / total_baskets for m in key]
        expected = math.prod(ind_sups)
        lift_val = (grp_sup / expected) if expected > 0 else 0.0
        groups.append(
            CoOccurrenceGroup(
                items=list(key),
                count=cnt,
                support=round(grp_sup, 4),
                lift=round(lift_val, 4),
            )
        )

    # Large orders: all purchase orders with 3+ items, sorted by size desc
    inv_info = (
        df.groupby("Sales Document")
        .agg(
            billing_date=("Document Date", "first"),
            payer=("Dealer Name", "first"),
            materials=("Material", lambda x: sorted(set(str(m).strip() for m in x))),
        )
        .reset_index()
    )
    inv_info["size"] = inv_info["materials"].apply(len)
    inv_info = inv_info[inv_info["size"] >= 3].sort_values("size", ascending=False)

    large_invoices: list[LargeInvoice] = []
    for _, row in inv_info.iterrows():
        bd_str = str(row["billing_date"])
        if hasattr(row["billing_date"], "strftime"):
            bd_str = row["billing_date"].strftime("%Y-%m-%d")
        large_invoices.append(
            LargeInvoice(
                billing_document=str(row["Sales Document"]),
                billing_date=bd_str,
                payer=str(row["payer"]),
                items=row["materials"],
                item_count=int(row["size"]),
            )
        )

    return MarketBasketResponse(
        total_baskets=total_baskets,
        multi_item_baskets=multi_item,
        total_unique_materials=len(all_mats),
        total_rules=len(rules_out),
        max_basket_size=max((len(b) for b in baskets), default=0),
        top_materials=top_materials,
        frequent_itemsets=frequent_itemsets,
        rules=rules_out,
        groups=groups,
        large_invoices=large_invoices,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ML Market Basket endpoint — Item2Vec + SVD Collaborative Filtering
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/market-basket/ml")
def market_basket_ml() -> MarketBasketMLResponse:
    """ML-based market basket analysis on MC dealer purchase orders (orders_clean).

    Business meaning: trains Item2Vec (Word2Vec on purchase baskets) and
    SVD collaborative filtering to surface item-item similarities and
    per-customer recommendations that go beyond simple co-occurrence counts.
    UMAP projects embeddings to 2D for visualisation; KMeans clusters items
    by purchase-pattern similarity. Only MC-type dealers from dealers.xlsx included.
    """

    import numpy as np
    import umap as _umap
    from gensim.models import Word2Vec
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD
    from sklearn.metrics.pairwise import cosine_similarity
    from sklearn.preprocessing import normalize

    raw = get_orders_clean()
    df = raw[(raw["dealer_type"] == "MC") & (raw["doc_type"] == "PO")].copy()

    # Baskets: one sorted list of unique material names per Sales Document (purchase order)
    baskets: list[list[str]] = (
        df.groupby("Sales Document")["Material"]
        .apply(lambda items: sorted(set(str(m).strip() for m in items)))
        .tolist()
    )

    mat_counts: dict[str, int] = {}
    for b in baskets:
        for m in b:
            mat_counts[m] = mat_counts.get(m, 0) + 1

    # ── 1. Item2Vec (skip-gram Word2Vec on baskets) ───────────────────────
    w2v = Word2Vec(
        sentences=baskets,
        vector_size=32,
        window=10,  # large: basket items are unordered
        min_count=2,
        workers=4,
        epochs=100,
        sg=1,  # skip-gram
        seed=42,
    )
    vocab: list[str] = list(w2v.wv.key_to_index.keys())

    item2vec_sims: dict[str, list[ItemSimilarity]] = {}
    for item in vocab:
        similar = w2v.wv.most_similar(item, topn=10)
        item2vec_sims[item] = [
            ItemSimilarity(item=s, score=round(float(sc), 4), method="item2vec")
            for s, sc in similar
        ]

    # ── 2. SVD Collaborative Filtering ───────────────────────────────────
    payer_item = (
        df.groupby(["Dealer Code", "Material"])["Order Quantity (Item)"].sum().unstack(fill_value=0)
    )
    n_comp = min(20, payer_item.shape[0] - 1, payer_item.shape[1] - 1)
    svd = TruncatedSVD(n_components=n_comp, random_state=42)
    # Fit on dealer×item matrix: rows=dealers, cols=items
    payer_latent: np.ndarray = svd.fit_transform(payer_item)  # (n_dealers, n_comp)
    item_latent: np.ndarray = svd.components_.T  # (n_items,   n_comp)
    item_latent_norm = normalize(item_latent)
    item_cols: list[str] = list(payer_item.columns)

    sim_matrix = cosine_similarity(item_latent_norm)  # (n_items, n_items)
    svd_sims: dict[str, list[ItemSimilarity]] = {}
    for i, item in enumerate(item_cols):
        ranked = sorted(
            ((item_cols[j], sim_matrix[i, j]) for j in range(len(item_cols)) if j != i),
            key=lambda x: -x[1],
        )
        svd_sims[item] = [
            ItemSimilarity(item=s, score=round(float(sc), 4), method="svd") for s, sc in ranked[:10]
        ]

    # Per-customer predictions: reconstruct the payer×item score matrix
    predicted = payer_latent @ item_latent.T  # (n_payers, n_items)

    customer_recs: list[CustomerRecommendation] = []
    for pi, payer in enumerate(payer_item.index):
        purchased = [item_cols[j] for j in range(len(item_cols)) if payer_item.iloc[pi, j] > 0]
        not_bought = [j for j in range(len(item_cols)) if payer_item.iloc[pi, j] == 0]
        scores = sorted(
            ((item_cols[j], predicted[pi, j]) for j in not_bought),
            key=lambda x: -x[1],
        )
        recs = [{"item": s, "score": round(float(sc), 4)} for s, sc in scores[:5] if sc > 0]
        if recs and purchased:
            customer_recs.append(
                CustomerRecommendation(payer=str(payer), purchased=purchased, recommendations=recs)
            )
    customer_recs.sort(key=lambda x: -len(x.purchased))

    # ── 3. UMAP + KMeans clustering on Item2Vec embeddings ───────────────
    embeddings = np.array([w2v.wv[item] for item in vocab])  # (n_vocab, 32)

    n_clusters = min(8, max(2, len(vocab) // 8))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    cluster_labels: np.ndarray = km.fit_predict(embeddings)

    n_neighbors = min(15, len(vocab) - 1)
    reducer = _umap.UMAP(n_components=2, random_state=42, n_neighbors=n_neighbors, min_dist=0.1)
    coords_2d: np.ndarray = reducer.fit_transform(embeddings)

    umap_coords = [
        UmapPoint(
            item=vocab[i],
            x=round(float(coords_2d[i, 0]), 4),
            y=round(float(coords_2d[i, 1]), 4),
            cluster=int(cluster_labels[i]),
            freq=mat_counts.get(vocab[i], 0),
        )
        for i in range(len(vocab))
    ]

    clusters_dict: dict[int, list[str]] = {}
    for i, item in enumerate(vocab):
        clusters_dict.setdefault(int(cluster_labels[i]), []).append(item)
    clusters_out = [
        MLCluster(
            cluster_id=k,
            items=sorted(v, key=lambda x: -mat_counts.get(x, 0)),
            count=len(v),
        )
        for k, v in sorted(clusters_dict.items())
    ]

    # ── Merge item recommendations (union of vocab + SVD items) ──────────
    all_items = sorted(
        set(vocab) | set(item_cols),
        key=lambda x: -mat_counts.get(x, 0),
    )
    item_recs_out = [
        ItemRecommendations(
            item=item,
            freq=mat_counts.get(item, 0),
            item2vec=item2vec_sims.get(item, []),
            svd=svd_sims.get(item, []),
        )
        for item in all_items
    ]

    return MarketBasketMLResponse(
        item_recommendations=item_recs_out,
        customer_recommendations=customer_recs[:50],
        umap_coords=umap_coords,
        clusters=clusters_out,
        model_info=MLModelInfo(
            embedding_dim=32,
            n_baskets=len(baskets),
            n_items_trained=len(vocab),
            n_components_svd=n_comp,
            n_clusters=n_clusters,
        ),
    )
