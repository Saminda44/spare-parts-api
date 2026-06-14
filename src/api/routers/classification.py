"""ABC/XYZ/FSN + K-Means classification endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Query

from src.api.deps import get_classification
from src.api.schemas import ClassificationResponse, ClassificationRow

router = APIRouter(prefix="/classification", tags=["Classification"])


def _to_row(r: pd.Series) -> ClassificationRow:
    last_issue = r.get("last_issue_date")
    if pd.notna(last_issue):
        try:
            last_issue = pd.Timestamp(last_issue).strftime("%Y-%m-%d")
        except Exception:
            last_issue = str(last_issue)
    else:
        last_issue = None

    return ClassificationRow(
        material_9=str(r.get("material_9", "")),
        description=str(r.get("description", "")),
        abc=str(r.get("abc", "")),
        xyz=str(r.get("xyz", "")),
        fsn=str(r.get("fsn", "")),
        abc_xyz_fsn=str(r.get("abc_xyz_fsn", "")),
        policy_tier=str(r.get("policy_tier", "")),
        demand_category=str(r["demand_category"]) if pd.notna(r.get("demand_category")) else None,
        demand_cluster=int(r["demand_cluster"]) if pd.notna(r.get("demand_cluster")) else None,
        demand_segment=str(r["demand_segment"]) if pd.notna(r.get("demand_segment")) else None,
        in_ssop=bool(r["in_ssop"]) if pd.notna(r.get("in_ssop")) else None,
        avg_monthly_demand=float(r.get("avg_monthly_demand", 0.0)),
        cv=float(r.get("cv", 0.0)),
        p_zero=float(r.get("p_zero", 0.0)),
        active_months=int(r.get("active_months", 0)),
        total_months=int(r.get("total_months", 0)),
        total_issue_qty=float(r.get("total_issue_qty", 0.0)),
        total_issue_value_lkr=float(r.get("total_issue_value_lkr", 0.0)),
        total_return_qty=float(r.get("total_return_qty", 0.0)),
        last_issue_date=last_issue,
    )


@router.get("", response_model=ClassificationResponse)
def list_classification(
    abc: str | None = Query(None, description="A, B, or C"),
    xyz: str | None = Query(None, description="X, Y, or Z"),
    fsn: str | None = Query(None, description="F, S, or N"),
    tier: str | None = Query(None, description="critical / managed / watch / rationalise"),
    segment: str | None = Query(None, description="K-Means demand segment label"),
    demand_category: str | None = Query(None, description="fast / slow / intermittent / non-moving"),
    in_ssop: bool | None = Query(None, description="Filter by SSOP presence"),
    limit: int = Query(500, le=5000),
    offset: int = Query(0, ge=0),
) -> ClassificationResponse:
    df = get_classification().copy()

    if abc and "abc" in df.columns:
        df = df[df["abc"] == abc.upper()]
    if xyz and "xyz" in df.columns:
        df = df[df["xyz"] == xyz.upper()]
    if fsn and "fsn" in df.columns:
        df = df[df["fsn"] == fsn.upper()]
    if tier and "policy_tier" in df.columns:
        df = df[df["policy_tier"] == tier]
    if segment and "demand_segment" in df.columns:
        df = df[df["demand_segment"] == segment]
    if demand_category and "demand_category" in df.columns:
        df = df[df["demand_category"] == demand_category]
    if in_ssop is not None and "in_ssop" in df.columns:
        df = df[df["in_ssop"] == in_ssop]

    total = len(df)
    page  = df.iloc[offset : offset + limit]

    full = get_classification()

    def _vc(col: str) -> dict[str, int]:
        return {k: int(v) for k, v in full[col].value_counts().items()} if col in full.columns else {}

    return ClassificationResponse(
        total=total,
        rows=[_to_row(r) for _, r in page.iterrows()],
        abc_counts=_vc("abc"),
        xyz_counts=_vc("xyz"),
        fsn_counts=_vc("fsn"),
        segment_counts=_vc("demand_segment"),
        demand_category_counts=_vc("demand_category"),
        tier_counts=_vc("policy_tier"),
    )
