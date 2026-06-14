"""ROL/ROQ policy, order recommendations, and sanity review endpoints."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from src.api.deps import get_policy
from src.api.schemas import PolicyResponse, PolicyRow, SanityRow

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
    page  = df.iloc[offset : offset + limit]
    full  = get_policy()

    def _vc(col: str) -> dict[str, int]:
        return {k: int(v) for k, v in full[col].value_counts().items()} if col in full.columns else {}

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

    export_cols = [c for c in [
        "material_9", "description", "abc", "xyz", "fsn", "policy_tier",
        "stock_status", "order_urgency", "method", "ss_method",
        "service_level", "z_score", "safety_stock", "rol", "roq",
        "net_requirement", "stock_on_hand", "forecast_lt",
        "coverage_months", "cv", "unit_value_lkr", "sanity_flag", "sanity_note",
    ] if c in df.columns]
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


@router.get("/sanity", response_model=list[SanityRow])
def sanity_review(
    limit: int = Query(200, le=2000),
) -> list[SanityRow]:
    """Return all sanity-flagged SKUs with their notes (ROL/ROQ > 3× recent demand)."""
    pol = get_policy()
    if pol.empty or "sanity_flag" not in pol.columns:
        return []

    flagged = pol[pol["sanity_flag"] == True].sort_values(  # noqa: E712
        "net_requirement", ascending=False
    ).head(limit)

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
