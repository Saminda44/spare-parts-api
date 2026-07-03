"""ROL/ROQ policy, order recommendations, and sanity review endpoints."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from src.api.deps import get_dealers, get_mcsi_clean, get_orders_clean, get_policy
from src.api.schemas import (
    PolicyResponse,
    PolicyRow,
    SanityRow,
    UIOAdjustedResponse,
    UIOAdjustedRow,
    UIOServicePlanResponse,
    UIOServicePlanRow,
)

router = APIRouter(prefix="/policy", tags=["Policy"])

_URGENCY_ORDER = {"immediate": 0, "soon": 1, "planned": 2, "none": 3}


def _to_row(r: pd.Series) -> PolicyRow:
    return PolicyRow(
        material_9=str(r.get("material_9", "")),
        description=str(r.get("description", "")),
        abc=str(r.get("abc", "")),
        xyz=str(r.get("xyz", "")),
        fsn=str(r.get("fsn", "")),
        policy_tier=str(r.get("policy_tier", "")),
        stock_status=str(r.get("stock_status", "")),
        coverage_months=float(r.get("coverage_months", 0.0)),
        days_of_stock=float(r.get("days_of_stock", 0.0)),
        method=str(r.get("method", "")),
        service_level=float(r.get("service_level", 0.0)),
        z_score=float(r.get("z_score", 0.0)),
        safety_stock=float(r.get("safety_stock", 0.0)),
        ss_method=str(r.get("ss_method", "")),
        rol=float(r.get("rol", 0.0)),
        roq=float(r.get("roq", 0.0)),
        net_requirement=float(r.get("net_requirement", 0.0)),
        order_urgency=str(r.get("order_urgency", "none")),
        unit_value_lkr=float(r.get("unit_value_lkr", 0.0)),
        stock_on_hand=float(r.get("stock_on_hand", 0.0)),
        forecast_lt=float(r.get("forecast_lt", 0.0)),
        cv=float(r.get("cv", 0.0)),
        sanity_flag=bool(r.get("sanity_flag", False)),
        sanity_note=str(r.get("sanity_note", "")),
    )


@router.get("", response_model=PolicyResponse)
def list_policy(
    urgency: str | None = Query(None, description="immediate / soon / planned / none"),
    tier: str | None = Query(None),
    abc: str | None = Query(None),
    fsn: str | None = Query(None),
    ss_method: str | None = Query(None, description="ML-Quantile or Classical"),
    sanity_flag: bool | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> PolicyResponse:
    df = get_policy().copy()

    if urgency and "order_urgency" in df.columns:
        df = df[df["order_urgency"] == urgency]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]
    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if fsn and "fsn" in df.columns:
        df = df[df["fsn"] == fsn.upper()]
    if ss_method and "ss_method" in df.columns:
        df = df[df["ss_method"] == ss_method]
    if sanity_flag is not None and "sanity_flag" in df.columns:
        df = df[df["sanity_flag"] == sanity_flag]

    if "order_urgency" in df.columns:
        df["_urg_ord"] = df["order_urgency"].map(_URGENCY_ORDER).fillna(9)
        df = df.sort_values(["_urg_ord", "net_requirement"], ascending=[True, False])
        df = df.drop(columns=["_urg_ord"])

    total = len(df)
    page = df.iloc[offset : offset + limit]
    full = get_policy()

    def _vc(col: str) -> dict[str, int]:
        return (
            {k: int(v) for k, v in full[col].value_counts().items()} if col in full.columns else {}
        )

    return PolicyResponse(
        total=total,
        rows=[_to_row(r) for _, r in page.iterrows()],
        urgency_counts=_vc("order_urgency"),
        tier_counts=_vc("policy_tier"),
        ss_method_counts=_vc("ss_method"),
    )


@router.get("/export.xlsx")
def export_policy_xlsx(
    urgency: str | None = Query(None),
    tier: str | None = Query(None),
    abc: str | None = Query(None),
    fsn: str | None = Query(None),
    sanity_flag: bool | None = Query(None),
) -> StreamingResponse:
    """Download the full order plan as an Excel workbook."""
    df = get_policy().copy()

    if urgency and "order_urgency" in df.columns:
        df = df[df["order_urgency"] == urgency]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]
    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if fsn and "fsn" in df.columns:
        df = df[df["fsn"] == fsn.upper()]
    if sanity_flag is not None and "sanity_flag" in df.columns:
        df = df[df["sanity_flag"] == sanity_flag]

    if "order_urgency" in df.columns:
        df["_urg_ord"] = df["order_urgency"].map(_URGENCY_ORDER).fillna(9)
        df = df.sort_values(["_urg_ord", "net_requirement"], ascending=[True, False])
        df = df.drop(columns=["_urg_ord"])

    export_cols = [
        c
        for c in [
            "material_9",
            "description",
            "abc",
            "xyz",
            "fsn",
            "policy_tier",
            "stock_status",
            "order_urgency",
            "method",
            "ss_method",
            "service_level",
            "z_score",
            "safety_stock",
            "rol",
            "roq",
            "net_requirement",
            "stock_on_hand",
            "forecast_lt",
            "coverage_months",
            "cv",
            "unit_value_lkr",
            "sanity_flag",
            "sanity_note",
        ]
        if c in df.columns
    ]
    df = df[export_cols]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Order Plan")
        sanity_df = df[df["sanity_flag"] == True] if "sanity_flag" in df.columns else pd.DataFrame()  # noqa: E712
        if not sanity_df.empty:
            sanity_df.to_excel(writer, index=False, sheet_name="Sanity Review")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=order_plan.xlsx"},
    )


@router.get("/uio-plan", response_model=UIOAdjustedResponse)
def uio_adjusted_plan(limit: int = Query(500, le=5000)) -> UIOAdjustedResponse:
    """Return ROQ adjusted by UIO growth ratio per bike model.

    Business meaning:
      uio_ratio = current fleet size / fleet size during demand-history period.
      uio_adjusted_roq = base_roq × uio_ratio.
      Ratio > 1 → fleet grew → need more parts.
      Ratio < 1 → fleet shrank → need fewer parts.
    """
    policy = get_policy()
    mcsi = get_mcsi_clean()
    orders = get_orders_clean()

    if policy.empty or mcsi.empty or orders.empty:
        return UIOAdjustedResponse(total_skus=0, models_covered=0, avg_uio_ratio=1.0, rows=[])

    # ── 1. Current UIO per model: total MCSI sold bikes (full dataset) ────────
    sold_mask = (
        mcsi["Status"] == "Sold" if "Status" in mcsi.columns else pd.Series(True, index=mcsi.index)
    )
    mcsi_sold = mcsi[sold_mask]
    current_uio: dict[str, int] = (
        mcsi_sold.groupby("Model")["VIN"].nunique().to_dict()
        if "Model" in mcsi_sold.columns and "VIN" in mcsi_sold.columns
        else {}
    )

    # ── 2. Historical UIO proxy: MCSI sold in the EARLIEST half of its date range ──
    if "Billing Date" in mcsi_sold.columns:
        dates = pd.to_datetime(mcsi_sold["Billing Date"], errors="coerce").dropna()
        if not dates.empty:
            midpoint = dates.quantile(0.5)
            hist_mask = pd.to_datetime(mcsi_sold["Billing Date"], errors="coerce") <= midpoint
            hist_uio: dict[str, int] = (
                mcsi_sold[hist_mask].groupby("Model")["VIN"].nunique().to_dict()
                if "Model" in mcsi_sold.columns
                else {}
            )
        else:
            hist_uio = current_uio
    else:
        hist_uio = current_uio

    # ── 3. UIO ratio per model (floor at 0.5, cap at 3.0 to prevent extremes) ─
    uio_ratio: dict[str, float] = {}
    for model, cur in current_uio.items():
        hist = max(hist_uio.get(model, cur), 1)
        uio_ratio[model] = min(max(cur / hist, 0.5), 3.0)

    # ── 4. Dealer → primary model affinity from MCSI ──────────────────────────
    if "Dealer Code" in mcsi_sold.columns and "Model" in mcsi_sold.columns:
        dealer_model = (
            mcsi_sold.groupby(["Dealer Code", "Model"])["VIN"]
            .nunique()
            .reset_index()
            .sort_values("VIN", ascending=False)
            .drop_duplicates("Dealer Code")
            .set_index("Dealer Code")["Model"]
            .to_dict()
        )
    else:
        dealer_model = {}

    # ── 5. SKU → primary model via dealer orders ──────────────────────────────
    mc_orders = (
        orders[orders.get("dealer_type", pd.Series(dtype=str)) == "MC"]
        if "dealer_type" in orders.columns
        else orders
    )

    if "Material" in mc_orders.columns and "Dealer Code" in mc_orders.columns:
        sku_dealer_vol = (
            (
                mc_orders.groupby(["Material", "Dealer Code"])["Confirmed Quantity (Item)"]
                .sum()
                .reset_index()
            )
            if "Confirmed Quantity (Item)" in mc_orders.columns
            else pd.DataFrame()
        )
    else:
        sku_dealer_vol = pd.DataFrame()

    sku_primary_model: dict[str, str] = {}
    if not sku_dealer_vol.empty:
        sku_dealer_vol["model"] = sku_dealer_vol["Dealer Code"].map(dealer_model)
        sku_dealer_vol = sku_dealer_vol.dropna(subset=["model"])
        if not sku_dealer_vol.empty:
            best = (
                sku_dealer_vol.groupby(["Material", "model"])["Confirmed Quantity (Item)"]
                .sum()
                .reset_index()
                .sort_values("Confirmed Quantity (Item)", ascending=False)
                .drop_duplicates("Material")
                .set_index("Material")["model"]
                .to_dict()
            )
            sku_primary_model = {str(k): str(v) for k, v in best.items()}

    # ── 6. Build UIO-adjusted rows ─────────────────────────────────────────────
    fallback_model = max(current_uio, key=current_uio.get) if current_uio else "Unknown"
    rows: list[UIOAdjustedRow] = []

    pol_subset = policy.head(limit)
    for _, r in pol_subset.iterrows():
        sku = str(r.get("material_9", ""))
        model = sku_primary_model.get(sku, fallback_model)
        ratio = uio_ratio.get(model, 1.0)
        base_roq = float(r.get("roq", 0.0))
        adj_roq = round(base_roq * ratio)
        delta = adj_roq - base_roq
        rows.append(
            UIOAdjustedRow(
                material_9=sku,
                description=str(r.get("description", "")),
                abc=str(r.get("abc", "")),
                policy_tier=str(r.get("policy_tier", "")),
                order_urgency=str(r.get("order_urgency", "none")),
                primary_model=model,
                uio_current=int(current_uio.get(model, 0)),
                uio_historical=int(hist_uio.get(model, 0)),
                uio_ratio=round(ratio, 3),
                base_roq=base_roq,
                uio_adjusted_roq=float(adj_roq),
                delta=delta,
                delta_pct=round(delta / base_roq * 100, 1) if base_roq > 0 else 0.0,
                net_requirement=float(r.get("net_requirement", 0.0)),
                stock_on_hand=float(r.get("stock_on_hand", 0.0)),
                unit_value_lkr=float(r.get("unit_value_lkr", 0.0)),
            )
        )

    models_in_result = {r.primary_model for r in rows}
    avg_ratio = sum(r.uio_ratio for r in rows) / len(rows) if rows else 1.0

    return UIOAdjustedResponse(
        total_skus=len(rows),
        models_covered=len(models_in_result),
        avg_uio_ratio=round(avg_ratio, 3),
        rows=rows,
    )


@router.get("/uio-service-plan", response_model=UIOServicePlanResponse)
def uio_service_plan(
    horizon_months: int = Query(
        5, ge=1, le=12, description="Planning horizon in months (lead time + buffer)"
    ),
    limit: int = Query(500, le=5000),
) -> UIOServicePlanResponse:
    """UIO-based order plan using actual MC dealer order history.

    Business meaning:
      Derives avg monthly demand per SKU from MC dealer orders (confirmed quantities).
      Projects forward by horizon_months (default 5 = 3-month lead time + 2-month buffer).
      Compares service_plan_qty vs rule-based ROQ side-by-side.
      Recommended order = max(service_plan_qty, net_requirement) to cover both
      statistical and fleet-driven demand signals.
    """
    policy = get_policy()
    orders = get_orders_clean()

    _empty = UIOServicePlanResponse(
        total_skus=0,
        skus_with_history=0,
        horizon_months=horizon_months,
        avg_monthly_demand_total=0.0,
        total_service_plan_value=0.0,
        total_rule_based_value=0.0,
        value_delta=0.0,
        rows=[],
    )
    if policy.empty:
        return _empty

    # ── 1. Filter to purchase orders from registered dealers only ─────────────
    # Business rule: only rows where Sold-to Party == Dealer Code AND
    # Sold-To Party Name == Dealer Name in dealers.xlsx (both must match).
    dealers = get_dealers()
    mc = orders.copy()
    if not dealers.empty and "Dealer Code" in dealers.columns and "Dealer Name" in dealers.columns:
        # Restrict to MC-type dealers only
        if "Type" in dealers.columns:
            dealers = dealers[dealers["Type"].str.strip().str.upper() == "MC"]
        # Build normalised lookup: code → canonical name (strip, upper for comparison)
        dealer_name_map: dict[str, str] = dealers.set_index("Dealer Code")["Dealer Name"].to_dict()
        stp_col = "Sold-to Party"
        stpn_col = "Sold-To Party Name"
        if stp_col in mc.columns and stpn_col in mc.columns:
            mc_code = mc[stp_col].astype(str).str.strip()
            mc_name = mc[stpn_col].astype(str).str.strip().str.upper()
            dealer_name_upper = {k: v.upper() for k, v in dealer_name_map.items()}
            # Row is valid if code is in dealers AND the names match (case-insensitive)
            valid = mc_code.isin(dealer_name_map) & (mc_code.map(dealer_name_upper) == mc_name)
            mc = mc[valid]
    if "doc_type" in mc.columns:
        mc = mc[mc["doc_type"] == "PO"]

    if not mc.empty and "Year_Month" in mc.columns and "Confirmed Quantity (Item)" in mc.columns:
        demand_agg = (
            mc.groupby(["Material", "Year_Month"])["Confirmed Quantity (Item)"].sum().reset_index()
        )
        sku_hist = (
            demand_agg.groupby("Material")
            .agg(
                hist_demand_total=("Confirmed Quantity (Item)", "sum"),
                hist_months=("Year_Month", "nunique"),
            )
            .reset_index()
            .rename(columns={"Material": "material_9"})
        )
        sku_hist["avg_monthly"] = sku_hist["hist_demand_total"] / sku_hist["hist_months"].clip(
            lower=1
        )
    else:
        sku_hist = pd.DataFrame(
            columns=["material_9", "hist_demand_total", "hist_months", "avg_monthly"]
        )

    # ── 2. Catalog model mapping (1,630 SKUs overlap with catalog_parts) ──────
    try:
        cat = pd.read_parquet("data/interim/catalog_parts.parquet")
        cat_map = (
            cat.groupby("part_number")["model"]
            .apply(lambda x: ", ".join(sorted(set(x.dropna()))))
            .reset_index()
            .rename(columns={"part_number": "material_9", "model": "catalog_models"})
        )
    except Exception:
        cat_map = pd.DataFrame(columns=["material_9", "catalog_models"])

    # ── 3. Merge policy with demand history and catalog ────────────────────────
    merged = policy.merge(sku_hist, on="material_9", how="left")
    merged = merged.merge(cat_map, on="material_9", how="left")

    merged["hist_demand_total"] = merged["hist_demand_total"].fillna(0.0)
    merged["hist_months"] = merged["hist_months"].fillna(0).astype(int)
    merged["avg_monthly"] = merged["avg_monthly"].fillna(0.0)
    merged["catalog_models"] = merged["catalog_models"].fillna("")

    merged["service_plan_qty"] = merged["avg_monthly"] * horizon_months
    merged["base_roq"] = merged.get("roq", pd.Series(0.0, index=merged.index)).fillna(0.0)
    merged["_net_req"] = merged.get("net_requirement", pd.Series(0.0, index=merged.index)).fillna(
        0.0
    )
    merged["recommended_order"] = merged[["service_plan_qty", "_net_req"]].max(axis=1)
    merged["delta_vs_roq"] = merged["service_plan_qty"] - merged["base_roq"]
    merged["delta_pct"] = merged.apply(
        lambda r: round(r["delta_vs_roq"] / r["base_roq"] * 100, 1) if r["base_roq"] > 0 else 0.0,
        axis=1,
    )
    merged["in_catalog"] = merged["catalog_models"].str.len() > 0

    # Sort: immediate urgency first, then highest service_plan_qty
    if "order_urgency" in merged.columns:
        merged["_urg"] = merged["order_urgency"].map(_URGENCY_ORDER).fillna(9)
        merged = merged.sort_values(["_urg", "service_plan_qty"], ascending=[True, False])
        merged = merged.drop(columns=["_urg"])

    skus_with_history = int((merged["hist_months"] > 0).sum())
    unit_val = merged.get("unit_value_lkr", pd.Series(0.0, index=merged.index)).fillna(0.0)
    total_svc_val = float((merged["service_plan_qty"] * unit_val).sum())
    total_rule_val = float((merged["base_roq"] * unit_val).sum())

    page = merged.head(limit)
    rows: list[UIOServicePlanRow] = []
    for _, r in page.iterrows():
        rows.append(
            UIOServicePlanRow(
                material_9=str(r.get("material_9", "")),
                description=str(r.get("description", "")),
                abc=str(r.get("abc", "")),
                policy_tier=str(r.get("policy_tier", "")),
                order_urgency=str(r.get("order_urgency", "none")),
                catalog_models=str(r.get("catalog_models", "")),
                hist_months=int(r.get("hist_months", 0)),
                hist_demand_total=float(r.get("hist_demand_total", 0.0)),
                avg_monthly=round(float(r.get("avg_monthly", 0.0)), 2),
                service_plan_qty=round(float(r.get("service_plan_qty", 0.0))),
                base_roq=round(float(r.get("base_roq", 0.0))),
                net_requirement=round(float(r.get("_net_req", 0.0))),
                recommended_order=round(float(r.get("recommended_order", 0.0))),
                delta_vs_roq=round(float(r.get("delta_vs_roq", 0.0))),
                delta_pct=float(r.get("delta_pct", 0.0)),
                stock_on_hand=float(r.get("stock_on_hand", 0.0)),
                unit_value_lkr=float(r.get("unit_value_lkr", 0.0)),
                in_catalog=bool(r.get("in_catalog", False)),
            )
        )

    return UIOServicePlanResponse(
        total_skus=len(merged),
        skus_with_history=skus_with_history,
        horizon_months=horizon_months,
        avg_monthly_demand_total=round(float(merged["avg_monthly"].sum()), 1),
        total_service_plan_value=total_svc_val,
        total_rule_based_value=total_rule_val,
        value_delta=total_svc_val - total_rule_val,
        rows=rows,
    )


@router.get("/sanity", response_model=list[SanityRow])
def sanity_review(
    limit: int = Query(200, le=2000),
) -> list[SanityRow]:
    """Return all sanity-flagged SKUs with their notes (ROL/ROQ > 3× recent demand)."""
    pol = get_policy()
    if pol.empty or "sanity_flag" not in pol.columns:
        return []

    flagged = pol[pol["sanity_flag"]].sort_values("net_requirement", ascending=False).head(limit)

    return [
        SanityRow(
            material_9=str(r.get("material_9", "")),
            description=str(r.get("description", "")),
            abc=str(r.get("abc", "")),
            policy_tier=str(r.get("policy_tier", "")),
            sanity_note=str(r.get("sanity_note", "")),
            rol=float(r.get("rol", 0.0)),
            roq=float(r.get("roq", 0.0)),
            net_requirement=float(r.get("net_requirement", 0.0)),
            avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
            order_urgency=str(r.get("order_urgency", "none")),
        )
        for _, r in flagged.iterrows()
    ]
