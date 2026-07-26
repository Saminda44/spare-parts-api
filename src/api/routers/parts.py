"""Part Master endpoints — Stage 6 (catalog extraction, supersession)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.api.deps import get_catalog_parts, get_part_master, get_supersession_map
from src.api.schemas import PartMasterResponse, PartMasterRow, SupersessionRow
from src.models.master_data.catalogue_part_master import load_part_master

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
            mask = df["part_number"].str.lower().str.contains(q, na=False) | df[
                "description"
            ].str.lower().str.contains(q, na=False)
            df = df[mask]
        if has_supersession is not None:
            df = df[df["has_supersession"] == has_supersession]

        supersession_count = int(pm["has_supersession"].sum())

        for _, r in df.head(limit).iterrows():
            rows.append(
                PartMasterRow(
                    part_number=str(r.get("part_number", "")),
                    description=str(r.get("description", "")),
                    compatible_models=(
                        str(r["compatible_models"]) if r.get("compatible_models") else None
                    ),
                    order_qty=int(r.get("order_qty", 0)),
                    eod_rate=float(r.get("eod_rate", 0.0)),
                    stock=float(r.get("stock", 0.0)),
                    on_order=float(r.get("on_order", 0.0)),
                    revised_order_qty=int(r.get("revised_order_qty", 0)),
                    forecast_monthly_qty=float(r.get("forecast_monthly_qty", 0.0)),
                    superseded_from=str(r["superseded_from"]) if r.get("superseded_from") else None,
                    has_supersession=bool(r.get("has_supersession", False)),
                )
            )

    if not ss.empty:
        for _, r in ss.iterrows():
            supersessions.append(
                SupersessionRow(
                    requested_pn=str(r.get("requested_pn", "")),
                    current_pn=str(r.get("current_pn", "")),
                    hops=int(r.get("hops", 1)),
                    current_description=str(r.get("current_description", "")),
                    old_description=str(r.get("old_description", "")),
                )
            )

    return PartMasterResponse(
        total=len(pm),
        supersession_count=supersession_count,
        rows=rows,
        supersessions=supersessions,
    )


@router.get("/from-catalog")
def parts_from_catalog(
    search: str = Query("", description="Filter by part number or description"),
    model: str = Query("", description="Filter by model folder name"),
    kind: str = Query("", description="Filter by kind: 'shared' or 'colour_specific'"),
    limit: int = Query(50000, le=100000),
) -> dict[str, Any]:
    """Return all unique parts from the catalogue part master (agent-derived).

    Reads data/interim/catalogue_part_master.parquet (written by
    POST /catalog/part-master/rebuild).  Falls back to the old
    catalog_parts.parquet when the agent-derived master is absent.

    Each row has:
        part_no, description, section, compatible_models,
        variant_count, source_count, kind
    """
    # ── Prefer agent-derived part master ─────────────────────────────────────
    df = load_part_master()
    is_agent_master = not df.empty and "section" in df.columns

    if not is_agent_master:
        # Fall back to old extraction-based parquet
        df = get_catalog_parts()
        if df.empty or "part_no" not in df.columns:
            return {"indexed": False, "total": 0, "total_models": 0, "rows": [], "models": []}
        df = df[df["part_no"].str.strip().astype(bool)].copy()
        grouped = (
            df.groupby("part_no", sort=True)
            .agg(
                description=(
                    "description",
                    lambda x: x.mode().iloc[0] if not x.mode().empty else "",
                ),
                compatible_models=(
                    "model",
                    lambda x: ", ".join(sorted(x.dropna().unique())),
                ),
                source_count=("source_file", "count"),
            )
            .reset_index()
        )
        grouped["section"] = ""
        grouped["variant_count"] = 1
        grouped["kind"] = "shared"
        all_models = sorted(df["model"].dropna().unique().tolist())
    else:
        grouped = df.copy()
        # Derive model list from compatible_models strings
        all_model_set: set[str] = set()
        for cm in grouped["compatible_models"].dropna():
            for entry in cm.split(", "):
                # "AEROX B65J" → "AEROX"; "AEROX B65J/DBNM8" → "AEROX"
                model_part = entry.split(" ")[0].split("/")[0].strip()
                if model_part:
                    all_model_set.add(model_part)
        all_models = sorted(all_model_set)

    # ── Filtering ─────────────────────────────────────────────────────────────
    if search:
        q = search.lower()
        mask = grouped["part_no"].str.lower().str.contains(q, na=False) | grouped[
            "description"
        ].str.lower().str.contains(q, na=False)
        grouped = grouped[mask]

    if model:
        grouped = grouped[
            grouped["compatible_models"].str.contains(model, case=False, na=False, regex=False)
        ]

    if kind in ("shared", "colour_specific") and "kind" in grouped.columns:
        grouped = grouped[grouped["kind"] == kind]

    rows = [
        {
            "part_no": str(r["part_no"]),
            "description": str(r.get("description", "")),
            "section": str(r.get("section", "")),
            "compatible_models": str(r.get("compatible_models", "")),
            "variant_count": int(r.get("variant_count", 1)),
            "source_count": int(r.get("source_count", 1)),
            "kind": str(r.get("kind", "shared")),
        }
        for _, r in grouped.head(limit).iterrows()
    ]

    return {
        "indexed": True,
        "agent_master": is_agent_master,
        "total": len(grouped),
        "total_models": len(all_models),
        "rows": rows,
        "models": all_models,
    }
