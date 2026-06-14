"""EDA endpoints — Stages 4, 5, 7, 8 (orders, sales, stock movements, spare parts)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

from src.api.deps import (
    get_ingestion_log, get_orders_clean, get_orders_rejection_log,
    get_sales_clean, get_spare_parts_features, get_stock_movements,
)
from src.api.schemas import (
    IntermittentSkuRow, MovementMonthlyPoint, MovementsResponse,
    OrdersEdaDealer, OrdersEdaMonthlyPoint, OrdersEdaRejectionReasonRow,
    OrdersEdaRejectionRow, OrdersEdaResponse,
    SalesEdaMonthlyPoint, SalesEdaResponse,
    SparePartsEdaResponse, TopSkuRow,
)

router = APIRouter(prefix="/eda", tags=["EDA — Stages 4-5-7-8"])


# ── Stage 4: Orders EDA ───────────────────────────────────────────────────────

@router.get("/orders", response_model=OrdersEdaResponse)
def get_orders_eda(
    rejection_limit: int = Query(200, le=1000),
    dealer_type: str = Query("MC", pattern="^(MC|OBM|ALL)$"),
) -> OrdersEdaResponse:
    orders = get_orders_clean()
    rej    = get_orders_rejection_log()

    # Filter by dealer_type column written by stage04 cleaning pipeline
    if dealer_type != "ALL" and not orders.empty and "dealer_type" in orders.columns:
        orders = orders[orders["dealer_type"] == dealer_type]
        if not rej.empty and "dealer_type" in rej.columns:
            rej = rej[rej["dealer_type"] == dealer_type]

    if orders.empty:
        return OrdersEdaResponse(
            total_po=0, total_returns=0, avg_fill_rate=0.0,
            avg_lead_time_days=0.0, fill_rate_lt1_count=0,
            top_dealers=[], monthly_trend=[], rejections=[],
        )

    po_mask  = orders["doc_type"] == "PO"
    ret_mask = orders["doc_type"] == "Return"
    total_po      = int(po_mask.sum())
    total_returns = int(ret_mask.sum())

    fill_series = orders["fill_rate"].dropna()
    avg_fill_rate     = float(fill_series.mean()) if len(fill_series) else 0.0
    fill_rate_lt1     = int((fill_series < 1.0).sum())
    avg_lead_time     = float(orders["lead_time_days"].dropna().mean()) if "lead_time_days" in orders.columns else 0.0

    # Top dealers by order count
    dealer_col = "Sold-To Party Name" if "Sold-To Party Name" in orders.columns else None
    top_dealers: list[OrdersEdaDealer] = []
    if dealer_col:
        dealer_grp = (
            orders[po_mask].groupby(dealer_col)
            .agg(order_count=("doc_type", "count"), total_value=("Net Value (Item)", "sum"))
            .sort_values("order_count", ascending=False)
            .head(10)
            .reset_index()
        )
        for _, r in dealer_grp.iterrows():
            top_dealers.append(OrdersEdaDealer(
                dealer=str(r[dealer_col]),
                order_count=int(r["order_count"]),
                total_value_lkr=float(r.get("total_value", 0.0) or 0.0),
            ))

    # Monthly trend
    monthly: list[OrdersEdaMonthlyPoint] = []
    if "Year_Month_str" in orders.columns:
        grp = (
            orders.groupby(["Year_Month_str", "doc_type"]).agg(
                cnt=("doc_type", "count"),
                val=("Net Value (Item)", "sum"),
            ).reset_index()
        )
        periods = sorted(grp["Year_Month_str"].unique())
        for p in periods:
            sub = grp[grp["Year_Month_str"] == p]
            po_r  = sub[sub["doc_type"] == "PO"]
            ret_r = sub[sub["doc_type"] == "Return"]
            monthly.append(OrdersEdaMonthlyPoint(
                period=str(p),
                po_count=int(po_r["cnt"].sum()) if len(po_r) else 0,
                return_count=int(ret_r["cnt"].sum()) if len(ret_r) else 0,
                total_value_lkr=float(sub["val"].sum()),
            ))

    # Rejection rows (fully rejected lines)
    rejections: list[OrdersEdaRejectionRow] = []
    if not rej.empty:
        sample = rej.head(rejection_limit)
        for _, r in sample.iterrows():
            rejections.append(OrdersEdaRejectionRow(
                material=str(r.get("Material", "")),
                description=str(r.get("Material Description", "")),
                customer=str(r.get("Sold-To Party Name", r.get("Sold-to Party", ""))),
                document_date=str(r.get("Document Date", ""))[:10],
                order_qty=float(r.get("Order Quantity (Item)", 0) or 0),
                lost_qty=float(r.get("lost_qty", 0) or 0),
                fill_rate=float(r.get("fill_rate", 0) or 0),
            ))

    # Orders received breakdown — document-level fulfillment classification
    orders_received: dict[str, int] = {"total_documents": 0, "fully_filled": 0, "partial_fill": 0, "complete_zero": 0}
    if not orders.empty and not rej.empty or not orders.empty:
        import pandas as _pd
        all_po_lines = _pd.concat(
            [orders[orders["doc_type"] == "PO"], rej[rej["doc_type"] == "PO"] if not rej.empty else orders.iloc[:0]],
            ignore_index=True,
        )
        if not all_po_lines.empty and "Sales Document" in all_po_lines.columns:
            doc_status = all_po_lines.groupby("Sales Document").apply(
                lambda g: (
                    "complete_zero" if (g["Confirmed Quantity (Item)"] == 0).all()
                    else "fully_filled" if (g["Confirmed Quantity (Item)"] >= g["Order Quantity (Item)"]).all()
                    else "partial_fill"
                )
            )
            counts = doc_status.value_counts()
            orders_received = {
                "total_documents": int(len(doc_status)),
                "fully_filled":    int(counts.get("fully_filled",  0)),
                "partial_fill":    int(counts.get("partial_fill",  0)),
                "complete_zero":   int(counts.get("complete_zero", 0)),
            }

    # Rejection reasons
    rej_reasons: list[OrdersEdaRejectionReasonRow] = []
    if not rej.empty and "Reason for Rejection" in rej.columns:
        po_rej = rej[rej["doc_type"] == "PO"] if "doc_type" in rej.columns else rej
        if not po_rej.empty:
            reason_grp = (
                po_rej.groupby("Reason for Rejection")
                .agg(rejected_lines=("Sales Document", "count"), rejected_qty=("Order Quantity (Item)", "sum"))
                .reset_index()
            )
            total_rej = int(reason_grp["rejected_lines"].sum()) or 1
            for _, r in reason_grp.sort_values("rejected_lines", ascending=False).iterrows():
                rej_reasons.append(OrdersEdaRejectionReasonRow(
                    reason=str(r["Reason for Rejection"]),
                    rejected_lines=int(r["rejected_lines"]),
                    rejected_qty=float(r["rejected_qty"]),
                    share_pct=round(float(r["rejected_lines"]) / total_rej * 100, 2),
                ))

    return OrdersEdaResponse(
        total_po=total_po,
        total_returns=total_returns,
        avg_fill_rate=round(avg_fill_rate, 4),
        avg_lead_time_days=round(avg_lead_time, 1),
        fill_rate_lt1_count=fill_rate_lt1,
        top_dealers=top_dealers,
        monthly_trend=monthly,
        rejections=rejections,
        orders_received_breakdown=orders_received,
        rejection_reasons=rej_reasons,
    )


# ── Stage 5: Sales EDA ────────────────────────────────────────────────────────

@router.get("/sales", response_model=SalesEdaResponse)
def get_sales_eda(
    dealer_type: str = Query("MC", pattern="^(MC|OBM|ALL)$"),
) -> SalesEdaResponse:
    sales = get_sales_clean()

    # Filter by dealer_type column written by stage05 cleaning pipeline
    if dealer_type != "ALL" and not sales.empty and "dealer_type" in sales.columns:
        sales = sales[sales["dealer_type"] == dealer_type]

    if sales.empty:
        return SalesEdaResponse(
            total_revenue_lkr=0.0, total_units=0,
            by_channel={}, by_category={}, monthly_trend=[],
        )

    sold = sales[sales["bill_class"] == "sale"] if "bill_class" in sales.columns else sales

    total_rev   = float(sold["Net Sales"].sum()) if "Net Sales" in sold.columns else 0.0
    total_units = int(sold["SlsVolQty"].sum()) if "SlsVolQty" in sold.columns else 0

    by_channel: dict[str, float] = {}
    if "channel" in sold.columns:
        by_channel = {
            str(k): round(float(v), 0)
            for k, v in sold.groupby("channel")["Net Sales"].sum().items()
        }

    by_category: dict[str, float] = {}
    if "product_category" in sold.columns:
        by_category = {
            str(k): round(float(v), 0)
            for k, v in sold.groupby("product_category")["Net Sales"].sum().sort_values(ascending=False).items()
        }

    monthly: list[SalesEdaMonthlyPoint] = []
    if "Year_Month_str" in sales.columns:
        grp = (
            sales.groupby("Year_Month_str").agg(
                revenue=("Net Sales", "sum"),
                qty=("SlsVolQty", "sum"),
                returns=("bill_class", lambda x: (x == "return").sum()),
            ).reset_index().sort_values("Year_Month_str")
        )
        for _, r in grp.iterrows():
            monthly.append(SalesEdaMonthlyPoint(
                period=str(r["Year_Month_str"]),
                revenue_lkr=round(float(r["revenue"]), 0),
                qty=int(r["qty"]),
                return_count=int(r["returns"]),
            ))

    return SalesEdaResponse(
        total_revenue_lkr=round(total_rev, 0),
        total_units=total_units,
        by_channel=by_channel,
        by_category=by_category,
        monthly_trend=monthly,
    )


# ── Stage 7: Stock Movements ──────────────────────────────────────────────────

@router.get("/movements", response_model=MovementsResponse)
def get_movements() -> MovementsResponse:
    sm = get_stock_movements()

    if sm.empty:
        return MovementsResponse(
            total_records=0, date_from="", date_to="",
            by_class={}, monthly_trend=[],
        )

    total_records = len(sm)
    date_from = str(sm["posting_date"].min())[:10]
    date_to   = str(sm["posting_date"].max())[:10]

    by_class: dict[str, int] = {
        str(k): int(v)
        for k, v in sm["movement_class"].value_counts().items()
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
            monthly.append(MovementMonthlyPoint(
                period=str(r["year_month_str"]),
                movement_class=str(r["movement_class"]),
                qty=float(r["qty"]),
                value_lkr=float(r["value_lkr"]),
            ))

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
            total_skus=0, in_ssop_count=0,
            total_issue_value_lkr=0.0, median_cv=0.0, median_p_zero=0.0,
            demand_category_counts={}, p_zero_bins=[], ingestion_summary=[],
            top_skus=[], intermittent_skus=[],
        )

    total_skus   = len(spf)
    in_ssop      = int(spf["in_ssop"].sum()) if "in_ssop" in spf.columns else 0

    total_issue_value_lkr = float(spf["total_issue_value_lkr"].sum()) if "total_issue_value_lkr" in spf.columns else 0.0
    median_cv    = float(spf["cv"].median())    if "cv" in spf.columns    else 0.0
    median_p_zero = float(spf["p_zero"].median()) if "p_zero" in spf.columns else 0.0

    demand_cat: dict[str, int] = {}
    if "demand_category" in spf.columns:
        demand_cat = {str(k): int(v) for k, v in spf["demand_category"].value_counts().items()}

    # p_zero histogram in 0.1 buckets
    p_zero_bins: list[dict[str, float | int]] = []
    if "p_zero" in spf.columns:
        bins = [i / 10 for i in range(11)]
        labels = [f"{bins[i]:.1f}–{bins[i+1]:.1f}" for i in range(len(bins)-1)]
        cut = pd.cut(spf["p_zero"].clip(0, 1), bins=bins, labels=labels, include_lowest=True)
        vc  = cut.value_counts().sort_index()
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
            top_skus.append(TopSkuRow(
                rank=int(idx) + 1,
                material_9=str(r.get("material_9", "")),
                description=str(r.get("description", "")),
                demand_category=str(r.get("demand_category", "")) or None,
                total_issue_value_lkr=round(val, 0),
                total_issue_qty=float(r.get("total_issue_qty", 0) or 0),
                cumulative_share_pct=round(cumsum / grand_total * 100, 1),
            ))

    # Intermittent / lumpy SKUs — p_zero ≥ 0.7, has ever had demand
    intermittent_skus: list[IntermittentSkuRow] = []
    if "p_zero" in spf.columns and "active_months" in spf.columns:
        int_df = spf[(spf["p_zero"] >= 0.7) & (spf["active_months"] > 0)].copy()
        if "total_issue_value_lkr" in int_df.columns:
            int_df = int_df.sort_values("total_issue_value_lkr", ascending=False)
        for _, r in int_df.head(200).iterrows():
            intermittent_skus.append(IntermittentSkuRow(
                material_9=str(r.get("material_9", "")),
                description=str(r.get("description", "")),
                demand_category=str(r.get("demand_category", "")),
                p_zero=round(float(r.get("p_zero", 0) or 0), 3),
                cv=round(float(r.get("cv", 0) or 0), 3),
                active_months=int(r.get("active_months", 0) or 0),
                avg_monthly_demand=round(float(r.get("avg_monthly_demand", 0) or 0), 2),
                total_issue_value_lkr=round(float(r.get("total_issue_value_lkr", 0) or 0), 0),
            ))

    # Ingestion log summary
    ingestion_summary: list[dict[str, str | int]] = []
    if not log.empty:
        for _, r in log.iterrows():
            ingestion_summary.append({
                "filename":           str(r.get("filename", "")),
                "rows_in":            int(r.get("rows_in", 0)),
                "inserted":           int(r.get("inserted", 0)),
                "duplicates_skipped": int(r.get("duplicates_skipped", 0)),
            })

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
