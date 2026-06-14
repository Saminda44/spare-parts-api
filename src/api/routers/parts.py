"""Part Master endpoints — Stage 6 (catalog extraction, supersession)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.api.deps import get_catalog_parts, get_part_master, get_supersession_map
from src.api.schemas import PartMasterResponse, PartMasterRow, SupersessionRow

router = APIRouter(prefix="/parts", tags=["Part Master — Stage 6"])


@router.get("", response_model=PartMasterResponse)
def get_parts(
    search: str = Query("", description="Filter by part number or description"),
    has_supersession: bool | None = Query(None),
    limit: int = Query(500, le=2000),
) -> PartMasterResponse:
    pm = get_part_master()
    ss = get_supersession_map()

    rows: list[PartMasterRow] = []
    supersessions: list[SupersessionRow] = []
    supersession_count = 0

    if not pm.empty:
        df = pm.copy()
        if search:
            q = search.lower()
            mask = (
                df["part_number"].str.lower().str.contains(q, na=False) |
                df["description"].str.lower().str.contains(q, na=False)
            )
            df = df[mask]
        if has_supersession is not None:
            df = df[df["has_supersession"] == has_supersession]

        supersession_count = int(pm["has_supersession"].sum())

        for _, r in df.head(limit).iterrows():
            rows.append(PartMasterRow(
                part_number=str(r.get("part_number", "")),
                description=str(r.get("description", "")),
                compatible_models=str(r["compatible_models"]) if r.get("compatible_models") else None,
                order_qty=int(r.get("order_qty", 0)),
                eod_rate=float(r.get("eod_rate", 0.0)),
                stock=float(r.get("stock", 0.0)),
                on_order=float(r.get("on_order", 0.0)),
                revised_order_qty=int(r.get("revised_order_qty", 0)),
                forecast_monthly_qty=float(r.get("forecast_monthly_qty", 0.0)),
                superseded_from=str(r["superseded_from"]) if r.get("superseded_from") else None,
                has_supersession=bool(r.get("has_supersession", False)),
            ))

    if not ss.empty:
        for _, r in ss.iterrows():
            supersessions.append(SupersessionRow(
                requested_pn=str(r.get("requested_pn", "")),
                current_pn=str(r.get("current_pn", "")),
                hops=int(r.get("hops", 1)),
                current_description=str(r.get("current_description", "")),
                old_description=str(r.get("old_description", "")),
            ))

    return PartMasterResponse(
        total=len(pm),
        supersession_count=supersession_count,
        rows=rows,
        supersessions=supersessions,
    )


@router.get("/from-catalog")
def parts_from_catalog(
    search: str = Query("", description="Filter by part number or description"),
    model: str  = Query("", description="Filter by model folder name"),
    limit: int  = Query(2000, le=10000),
) -> dict[str, Any]:
    """Return all unique parts derived from the PDF catalogue index.

    Groups catalog_parts.parquet by part_no, collecting:
    - description: most frequently seen description for that part number
    - compatible_models: sorted comma-separated list of model folder names
    - source_count: total rows (PDF pages) in which the part appeared

    Returns indexed=False when the parquet has not been built yet.
    """
    df = get_catalog_parts()

    if df.empty or "part_no" not in df.columns:
        return {"indexed": False, "total": 0, "total_models": 0, "rows": [], "models": []}

    # Drop blank part numbers
    df = df[df["part_no"].str.strip().astype(bool)].copy()

    # Aggregate: one row per unique part number
    grouped = (
        df.groupby("part_no", sort=True)
        .agg(
            description      =("description", lambda x: x.mode().iloc[0] if not x.mode().empty else ""),
            compatible_models=("model",       lambda x: ", ".join(sorted(x.dropna().unique()))),
            source_count     =("source_file", "count"),
        )
        .reset_index()
    )

    all_models: list[str] = sorted(df["model"].dropna().unique().tolist())

    # Filtering
    if search:
        q = search.lower()
        mask = (
            grouped["part_no"].str.lower().str.contains(q, na=False)
            | grouped["description"].str.lower().str.contains(q, na=False)
        )
        grouped = grouped[mask]

    if model:
        grouped = grouped[
            grouped["compatible_models"].str.contains(model, case=False, na=False, regex=False)
        ]

    rows = [
        {
            "part_no":          str(r["part_no"]),
            "description":      str(r["description"]),
            "compatible_models": str(r["compatible_models"]),
            "source_count":     int(r["source_count"]),
        }
        for _, r in grouped.head(limit).iterrows()
    ]

    return {
        "indexed":      True,
        "total":        len(grouped),
        "total_models": len(all_models),
        "rows":         rows,
        "models":       all_models,
    }
