"""Purchase Recommendation endpoints — Module 6 Decision Support.

All endpoints load from saved parquet / JSON files in data/processed/.
They never re-run the pipeline — call python -m scripts.run_module 6 --save first.

Endpoints:
  GET /purchase-recommendation/summary
  GET /purchase-recommendation/recommendations
  GET /purchase-recommendation/stockout-risk
  GET /purchase-recommendation/overstock
  GET /purchase-recommendation/fill-rate
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from src.config.paths import DATA_PROCESSED

router = APIRouter(prefix="/purchase-recommendation", tags=["Purchase Recommendation"])

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

_REC_PATH = DATA_PROCESSED / "m6_recommendations.parquet"
_RISK_PATH = DATA_PROCESSED / "m6_stockout_risk.parquet"
_OVER_PATH = DATA_PROCESSED / "m6_overstock_risk.parquet"
_FR_PATH = DATA_PROCESSED / "m6_fill_rate.parquet"
_SUMMARY_PATH = DATA_PROCESSED / "m6_summary.json"

_503_MSG = (
    "Module 6 outputs not found. "
    "Run pipeline first: python -m scripts.run_module 6 --save"
)

# ---------------------------------------------------------------------------
# Internal loaders (no LRU cache — files are small and freshness matters)
# ---------------------------------------------------------------------------


def _load_parquet(path: Path) -> pd.DataFrame:
    """Load a parquet file, raising HTTP 503 when the file does not exist.

    Business meaning: a 503 signals to the frontend that the pipeline has not
    been run yet, prompting the user to run Module 6.
    """
    if not path.exists():
        logger.warning(f"Module 6 parquet missing: {path}")
        raise HTTPException(status_code=503, detail=_503_MSG)
    return pd.read_parquet(path)


def _load_summary() -> dict[str, Any]:
    """Load the Module 6 summary JSON, raising HTTP 503 when missing."""
    if not _SUMMARY_PATH.exists():
        logger.warning(f"Module 6 summary JSON missing: {_SUMMARY_PATH}")
        raise HTTPException(status_code=503, detail=_503_MSG)
    with open(_SUMMARY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """Return the Module 6 KPI summary for the dashboard header cards.

    Business meaning: provides the at-a-glance inventory health snapshot —
    how many SKUs need ordering, urgency breakdown, stockout risk count,
    overstock count, weighted fill rate, and container utilisation estimate.

    Returns:
        dict with keys:
            total_skus_to_order, critical_count, high_count,
            stockout_risk_count, overstock_count,
            weighted_fill_rate_pct, container_utilization_pct
    """
    return _load_summary()


@router.get("/recommendations")
def get_recommendations(
    priority: int | None = Query(None, ge=1, le=4, description="1=Critical 2=High 3=Medium 4=Low"),
    demand_class: str | None = Query(None, description="A, B, or C (first character of ABC class)"),
    search: str | None = Query(None, description="Part number contains search string (case-insensitive)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return paginated purchase recommendations.

    Business meaning: the main decision-support table for the procurement team.
    Returns SKUs that have been flagged for ordering (order_qty > 0 after all
    Module 5 constraints), annotated with risk level and action directive.

    Query parameters:
        priority:     Filter to a specific priority tier (1–4).
        demand_class: Filter by ABC class (A, B, or C).
        search:       Substring match on part_no (case-insensitive).
        limit:        Page size (default 100, max 1000).
        offset:       Page start position.

    Returns:
        {total, offset, limit, rows: [{part_no, order_qty, priority, urgency_score,
        demand_class, abc, mean_monthly_demand, binding_constraint,
        risk_level, action, fill_rate_pct}]}
    """
    df = _load_parquet(_REC_PATH)

    if priority is not None and "priority" in df.columns:
        df = df[df["priority"] == priority]
    if demand_class and "abc" in df.columns:
        df = df[df["abc"].str.upper() == demand_class.upper()]
    if search and "part_no" in df.columns:
        df = df[df["part_no"].str.contains(search.strip(), case=False, na=False, regex=False)]

    total = len(df)
    page = df.iloc[offset : offset + limit]

    rows: list[dict[str, Any]] = []
    for _, r in page.iterrows():
        rows.append(
            {
                "part_no": str(r.get("part_no", "")),
                "order_qty": float(r.get("order_qty", 0.0)),
                "priority": int(r.get("priority", 4)),
                "urgency_score": round(float(r.get("urgency_score", 0.0)), 4),
                "demand_class": str(r.get("demand_class", "")),
                "abc": str(r.get("abc", "")),
                "mean_monthly_demand": round(float(r.get("mean_monthly_demand", 0.0)), 4),
                "binding_constraint": str(r.get("binding_constraint", "")),
                "risk_level": str(r.get("risk_level", "")),
                "action": str(r.get("action", "")),
                "fill_rate_pct": round(float(r.get("fill_rate_pct", 0.0)), 2),
            }
        )

    return {"total": total, "offset": offset, "limit": limit, "rows": rows}


@router.get("/stockout-risk")
def get_stockout_risk(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return all SKUs ranked by stockout probability, highest risk first.

    Business meaning: the sigmoid transformation converts the Module 4 urgency
    score into a P(stockout) estimate.  SKUs with risk_pct above 30% should be
    reviewed urgently by the procurement team.

    Returns:
        {total, rows: [{part_no, risk_pct, days_until_stockout, urgency_score}]}
        Sorted by risk_pct descending.
    """
    df = _load_parquet(_RISK_PATH)
    total = len(df)
    page = df.iloc[offset : offset + limit]

    rows: list[dict[str, Any]] = [
        {
            "part_no": str(r.get("part_no", "")),
            "risk_pct": round(float(r.get("risk_pct", 0.0)), 2),
            "days_until_stockout": round(float(r.get("days_until_stockout", 0.0)), 1),
            "urgency_score": round(float(r.get("urgency_score", 0.0)), 4),
        }
        for _, r in page.iterrows()
    ]

    return {"total": total, "rows": rows}


@router.get("/overstock")
def get_overstock(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return SKUs with excess inventory (>12 months coverage), most overstocked first.

    Business meaning: overstock ties up working capital.  These SKUs should be
    deprioritised in future orders and may require stock write-down review.

    Returns:
        {total, rows: [{part_no, excess_qty, months_cover, demand_class}]}
        Sorted by months_cover descending.
    """
    df = _load_parquet(_OVER_PATH)
    total = len(df)
    page = df.iloc[offset : offset + limit]

    rows: list[dict[str, Any]] = [
        {
            "part_no": str(r.get("part_no", "")),
            "excess_qty": round(float(r.get("excess_qty", 0.0)), 4),
            "months_cover": round(float(r.get("months_cover", 0.0)), 2),
            "demand_class": str(r.get("demand_class", "")),
        }
        for _, r in page.iterrows()
    ]

    return {"total": total, "rows": rows}


@router.get("/fill-rate")
def get_fill_rate(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return expected fill rate per SKU, worst coverage first.

    Business meaning: fill rate shows the percentage of demand over the next
    4-month planning horizon that can be met from current net inventory position.
    SKUs at the top of this list (lowest fill rate) are most at risk of not
    meeting customer orders.

    Returns:
        {total, rows: [{part_no, fill_rate_pct, stock_qty, monthly_demand}]}
        Sorted by fill_rate_pct ascending (worst first).
    """
    df = _load_parquet(_FR_PATH)  # already sorted ascending by fill_rate_pct
    total = len(df)
    page = df.iloc[offset : offset + limit]

    rows: list[dict[str, Any]] = [
        {
            "part_no": str(r.get("part_no", "")),
            "fill_rate_pct": round(float(r.get("fill_rate_pct", 0.0)), 2),
            "stock_qty": round(float(r.get("stock_qty", 0.0)), 4),
            "monthly_demand": round(float(r.get("monthly_demand", 0.0)), 4),
        }
        for _, r in page.iterrows()
    ]

    return {"total": total, "rows": rows}
