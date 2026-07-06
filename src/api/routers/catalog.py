"""Catalog endpoints — browse and serve PDF parts catalogues from data/raw/pdf_catalogues."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.api.deps import get_catalog_parts
from src.api.schemas import (
    CatalogCoverageResponse,
    CatalogCoverageRow,
    CatalogFile,
    CatalogModel,
    CatalogPartRow,
    CatalogResponse,
)
from src.models.master_data.catalogue_agent import CatalogueAgent
from src.models.master_data.catalogue_part_master import (
    build_part_master,
)
from src.models.master_data.pdf_catalogue_extractor import (
    DISPLAY_HEADERS,
    YamahaCatalogueExtractor,
)

PDF_ROOT = Path("data/raw/pdf_catalogues").resolve()
PDF_EXTS = {".pdf", ".PDF"}
RAW_ROOT = Path("data/raw").resolve()
XLSX_EXTS = {".xlsx", ".xls"}
AGENT_CACHE = Path("data/outputs/agent_builds").resolve()

router = APIRouter(prefix="/catalog", tags=["Catalog"])


@router.get("", response_model=CatalogResponse)
def list_catalog() -> CatalogResponse:
    models: list[CatalogModel] = []
    total = 0

    if not PDF_ROOT.exists():
        return CatalogResponse(models=[], total_pdfs=0)

    for folder in sorted(PDF_ROOT.iterdir(), key=lambda p: p.name.upper()):
        if not folder.is_dir():
            continue
        files: list[CatalogFile] = []
        for pdf in sorted(folder.rglob("*"), key=lambda p: p.name.upper()):
            if pdf.suffix in PDF_EXTS and pdf.is_file():
                rel = pdf.relative_to(PDF_ROOT)
                files.append(
                    CatalogFile(
                        filename=pdf.name,
                        rel_path=rel.as_posix(),
                        size_kb=round(pdf.stat().st_size / 1024, 1),
                    )
                )
        if files:
            total += len(files)
            models.append(CatalogModel(model=folder.name, pdf_count=len(files), files=files))

    return CatalogResponse(models=models, total_pdfs=total)


@router.get("/file/{file_path:path}")
def serve_pdf(file_path: str) -> FileResponse:
    target = (PDF_ROOT / file_path).resolve()
    # Path traversal guard
    if not str(target).startswith(str(PDF_ROOT)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.exists() or target.suffix not in PDF_EXTS:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        str(target), media_type="application/pdf", headers={"Content-Disposition": "inline"}
    )


@router.get("/folders")
def list_folders() -> list[str]:
    """Return all folder names inside pdf_catalogues (for the upload folder picker)."""
    if not PDF_ROOT.exists():
        return []
    return sorted(f.name for f in PDF_ROOT.iterdir() if f.is_dir())


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),  # noqa: B008
    folder: str = Form(...),
) -> dict[str, str]:
    """Upload a PDF catalogue into pdf_catalogues/{folder}/. Creates the folder if needed."""
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    folder_clean = folder.strip().strip("/\\")
    if not folder_clean or ".." in folder_clean or "/" in folder_clean or "\\" in folder_clean:
        raise HTTPException(status_code=400, detail="Invalid folder name")

    target_dir = (PDF_ROOT / folder_clean).resolve()
    if not str(target_dir).startswith(str(PDF_ROOT)):
        raise HTTPException(status_code=403, detail="Forbidden")

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / Path(file.filename).name

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    return {
        "rel_path": dest.relative_to(PDF_ROOT).as_posix(),
        "filename": dest.name,
        "folder": folder_clean,
    }


_EXTRACTOR = YamahaCatalogueExtractor(max_pages=500)
_INTERIM = Path("data/interim")
_OUTPUTS = Path("data/outputs")
_PARTS_OUT = _INTERIM / "catalog_parts.parquet"
_PARTS_XLSX = _OUTPUTS / "catalog_parts.xlsx"
_EXTRACT_STATUS: dict[str, Any] = {"running": False, "last_result": None}


@router.get("/tables/{file_path:path}")
def extract_pdf_tables(
    file_path: str,
    max_pages: int = Query(200, le=500),
) -> dict[str, Any]:
    """Extract parts from one Yamaha PDF using YamahaCatalogueExtractor.

    Returns up to nine columns depending on PDF type:
        Section | Ref. No. | Part No. | Description | Q'ty
        | 9 Digit Part No. | Superseded Part No. | Remarks
    The extra columns are only populated for India-market PDFs that carry them.
    """
    target = (PDF_ROOT / file_path).resolve()
    if not str(target).startswith(str(PDF_ROOT)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.exists() or target.suffix not in PDF_EXTS:
        raise HTTPException(status_code=404, detail="File not found")

    ex = YamahaCatalogueExtractor(max_pages=max_pages)
    result = ex.extract(target)

    if result.error:
        raise HTTPException(status_code=500, detail=result.error)

    rows = [
        [
            r[c]
            for c in [
                "section",
                "ref_no",
                "part_no",
                "description",
                "qty",
                "nine_digit_part_no",
                "superseded_part_no",
                "remarks",
            ]
        ]
        for r in result.rows
    ]
    sections = sorted({r[0] for r in rows if r[0]})

    # Drop optional columns (indices 5, 6) when this PDF has no data in them.
    # nine_digit_part_no=5, superseded_part_no=6 are India-market only.
    headers: list[str] = list(DISPLAY_HEADERS)
    optional_cols = [5, 6]
    drop = [c for c in optional_cols if not any(row[c].strip() for row in rows)]
    if drop:
        keep = [i for i in range(len(headers)) if i not in drop]
        headers = [headers[i] for i in keep]
        rows = [[row[i] for i in keep] for row in rows]

    return {
        "headers": headers,
        "rows": rows,
        "total": len(rows),
        "sections": sections,
        "variants": result.variants,
        "colour_codes": result.colour_codes,
        "available_colours": result.available_colours,
        "manufacture_year": result.manufacture_year,
        "pages_scanned": result.pages_scanned,
        "sections_found": result.sections_found,
        "ocr_flagged": result.ocr_flagged,
        "warnings": result.warnings,
        # NEW: Colour matching metadata for debugging
        "colour_extraction_metadata": {
            "source": "pdf_cover" if result.available_colours else "agent_web",
            "unmapped_count": len(result.available_colours or [])
            - len(result.available_colour_map),
            "total_available": len(result.available_colours or []),
            "matched_count": len(result.available_colour_map),
            "has_warnings": any(
                "colour" in w.lower() or "unmapped" in w.lower() for w in result.warnings
            ),
        },
    }


@router.post("/run-extraction")
def run_batch_extraction(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Trigger batch extraction of all PDF catalogues → data/interim/catalog_parts.parquet.

    Runs in the background so the request returns immediately.
    Poll GET /catalog/extraction-status for progress.
    """
    if _EXTRACT_STATUS["running"]:
        return {"queued": False, "message": "Extraction already running"}

    def _run() -> None:
        _EXTRACT_STATUS["running"] = True
        _EXTRACT_STATUS["last_result"] = None
        try:
            from src.db import save_catalog_parts  # noqa: PLC0415

            _INTERIM.mkdir(parents=True, exist_ok=True)
            _OUTPUTS.mkdir(parents=True, exist_ok=True)
            df = _EXTRACTOR.extract_all(PDF_ROOT, save_path=_PARTS_OUT)
            # Also save Excel for easy download / business review
            if not df.empty:
                df.to_excel(_PARTS_XLSX, index=False, engine="openpyxl")
            # Persist to PostgreSQL when DATA_BACKEND=postgres (non-fatal if unavailable)
            save_catalog_parts(df)
            _EXTRACT_STATUS["last_result"] = {
                "ok": True,
                "total_rows": len(df),
                "distinct_parts": int(df["part_no"].nunique()) if not df.empty else 0,
                "models": int(df["model"].nunique()) if not df.empty else 0,
                "parquet": str(_PARTS_OUT),
                "excel": str(_PARTS_XLSX) if not df.empty else None,
            }
        except Exception as exc:  # noqa: BLE001
            _EXTRACT_STATUS["last_result"] = {"ok": False, "error": str(exc)}
        finally:
            _EXTRACT_STATUS["running"] = False

    background_tasks.add_task(_run)
    return {"queued": True, "message": "Extraction started in background"}


@router.get("/extraction-status")
def get_extraction_status() -> dict[str, Any]:
    """Return current state of the batch extraction job."""
    return {
        "running": _EXTRACT_STATUS["running"],
        "last_result": _EXTRACT_STATUS["last_result"],
        "parquet_exists": _PARTS_OUT.exists(),
        "parquet_size_kb": (
            round(_PARTS_OUT.stat().st_size / 1024, 1) if _PARTS_OUT.exists() else 0
        ),
        "excel_exists": _PARTS_XLSX.exists(),
    }


@router.get("/download")
def download_catalog_excel() -> FileResponse:
    """Download extracted catalog parts as Excel (.xlsx).

    The file is regenerated from the current catalog_parts.parquet on each call
    so it always reflects the latest extraction run.
    Triggers a re-generate if the parquet is newer than the cached Excel.
    """
    if not _PARTS_OUT.exists():
        raise HTTPException(
            status_code=404,
            detail="No catalog data found. Run POST /catalog/run-extraction first.",
        )
    # Re-generate Excel if parquet is newer than the saved xlsx (or xlsx missing)
    if not _PARTS_XLSX.exists() or _PARTS_OUT.stat().st_mtime > _PARTS_XLSX.stat().st_mtime:
        import pandas as pd  # noqa: PLC0415

        _OUTPUTS.mkdir(parents=True, exist_ok=True)
        pd.read_parquet(_PARTS_OUT).to_excel(_PARTS_XLSX, index=False, engine="openpyxl")
    return FileResponse(
        path=_PARTS_XLSX,
        filename="catalog_parts.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/coverage", response_model=CatalogCoverageResponse)
def get_catalog_coverage() -> CatalogCoverageResponse:
    """Stage 6.1 — Parts extracted from PDF catalogues, grouped by model.

    Returns data from catalog_parts.parquet (written by stage06 run).
    If the parquet does not exist, returns extracted=False with empty rows.
    """
    df = get_catalog_parts()

    if df.empty:
        return CatalogCoverageResponse(
            extracted=False,
            total_part_references=0,
            distinct_parts=0,
            distinct_models=0,
            rows=[],
        )

    # Count PDFs per model from the live file system (for pdf_count)
    pdf_counts: dict[str, int] = {}
    if PDF_ROOT.exists():
        for folder in PDF_ROOT.iterdir():
            if folder.is_dir():
                pdf_counts[folder.name] = sum(1 for f in folder.rglob("*") if f.suffix in PDF_EXTS)

    pn_col = "part_no" if "part_no" in df.columns else "part_number"
    rows: list[CatalogCoverageRow] = []
    if "model" in df.columns and pn_col in df.columns:
        agg_dict: dict[str, Any] = {"distinct_parts": (pn_col, "nunique")}
        if "ocr_used" in df.columns:
            agg_dict["ocr_pages"] = ("ocr_used", "sum")
        grp = (
            df.groupby("model")
            .agg(**agg_dict)
            .reset_index()
            .sort_values("distinct_parts", ascending=False)
        )
        for _, r in grp.iterrows():
            model_name = str(r["model"])
            rows.append(
                CatalogCoverageRow(
                    model=model_name,
                    pdf_count=pdf_counts.get(model_name, 0),
                    distinct_parts=int(r["distinct_parts"]),
                    ocr_pages=int(r.get("ocr_pages", 0)),
                )
            )

    return CatalogCoverageResponse(
        extracted=True,
        total_part_references=len(df),
        distinct_parts=int(df[pn_col].nunique()) if pn_col in df.columns else 0,
        distinct_models=int(df["model"].nunique()) if "model" in df.columns else 0,
        rows=rows,
    )


@router.get("/excel")
def list_excel_catalogues() -> list[dict[str, Any]]:
    """List all Excel parts-catalogue files found directly in data/raw/."""
    result = []
    if RAW_ROOT.exists():
        for f in sorted(RAW_ROOT.glob("*"), key=lambda p: p.name.upper()):
            if f.is_file() and f.suffix in XLSX_EXTS and "catalogue" in f.stem.lower():
                result.append(
                    {
                        "filename": f.name,
                        "stem": f.stem,
                        "size_kb": round(f.stat().st_size / 1024, 1),
                    }
                )
    return result


@router.get("/excel/{filename}")
def get_excel_catalogue(
    filename: str,
    section: str | None = Query(None),
    search: str | None = Query(None),
) -> dict[str, Any]:
    """Return all rows from an Excel parts catalogue in data/raw/.

    Optionally filter by section and/or a search string (matched against
    Part No. and Description columns).
    """
    import openpyxl

    target = (RAW_ROOT / filename).resolve()
    if not str(target).startswith(str(RAW_ROOT)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.exists() or target.suffix not in XLSX_EXTS:
        raise HTTPException(status_code=404, detail="File not found")

    wb = openpyxl.load_workbook(str(target), read_only=True, data_only=True)
    ws = wb.active

    headers: list[str] = []
    rows: list[list[str]] = []

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if not any(cells):
            continue
        if i == 0:
            headers = cells
            continue
        rows.append(cells)

    # Drop "Fig. No." column
    if "Fig. No." in headers:
        drop_idx = headers.index("Fig. No.")
        headers = [h for j, h in enumerate(headers) if j != drop_idx]
        rows = [[c for j, c in enumerate(r) if j != drop_idx] for r in rows]

    # Derive column indices for filtering
    try:
        sec_idx = headers.index("Section")
    except ValueError:
        sec_idx = 0
    try:
        pn_idx = headers.index("Part No.")
    except ValueError:
        pn_idx = 2
    try:
        desc_idx = headers.index("Description")
    except ValueError:
        desc_idx = 3

    if section:
        rows = [r for r in rows if r[sec_idx].upper() == section.upper()]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r[pn_idx].lower() or q in r[desc_idx].lower()]

    sections = sorted({r[sec_idx] for r in rows if r[sec_idx]})

    return {
        "filename": filename,
        "headers": headers,
        "rows": rows,
        "total": len(rows),
        "sections": sections,
    }


@router.get("/parts/{model}", response_model=list[CatalogPartRow])
def get_parts_for_model(
    model: str,
    limit: int = Query(500, le=5000),
) -> list[CatalogPartRow]:
    """Return part numbers extracted from PDF catalogues for a specific model."""
    df = get_catalog_parts()

    if df.empty or "model" not in df.columns:
        return []

    sub = df[df["model"].str.lower() == model.lower()].head(limit)
    pn_col = "part_no" if "part_no" in df.columns else "part_number"
    result: list[CatalogPartRow] = []
    for _, r in sub.iterrows():
        result.append(
            CatalogPartRow(
                part_number=str(r.get(pn_col, "")),
                source_file=str(r.get("source_file", "")),
                ocr_used=bool(r.get("ocr_used", False)),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Catalogue Agent endpoints
# ---------------------------------------------------------------------------

_AGENT = CatalogueAgent(max_pages=500)
_AGENT_STATUS: dict[str, Any] = {}  # file_path → {running, cached, error}


def _cache_path(rel: str) -> Path:
    """Return the disk-cache path for a given PDF rel_path."""
    safe = rel.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return AGENT_CACHE / f"{safe}.json"


@router.get("/agent/{file_path:path}")
def run_agent(
    file_path: str,
    refresh: bool = Query(False, description="Force re-run even if cached"),
) -> dict[str, Any]:
    """Run the CatalogueAgent on one PDF and return assembled builds.

    Results are cached to data/outputs/agent_builds/ so subsequent calls
    are instant.  Pass ?refresh=true to force re-extraction.

    Response schema (summary):
        model, variants[], colour_legend{}, rosters{}, builds[{variant, colour,
        colour_name, colour_code, part_count, parts[{figure, ref_no, part_no,
        description, qty, remarks, kind}]}], warnings[]
    """
    target = (PDF_ROOT / file_path).resolve()
    if not str(target).startswith(str(PDF_ROOT)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target.exists() or target.suffix not in PDF_EXTS:
        raise HTTPException(status_code=404, detail="File not found")

    cache = _cache_path(file_path)

    if not refresh and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    try:
        result = _AGENT.run(target)
        out = result.to_dict()
        AGENT_CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/agent/{file_path:path}")
def clear_agent_cache(file_path: str) -> dict[str, str]:
    """Delete the cached agent build for a PDF so the next GET re-runs it."""
    cache = _cache_path(file_path)
    if cache.exists():
        cache.unlink()
        return {"status": "cleared", "path": str(cache)}
    return {"status": "not_cached"}


# ---------------------------------------------------------------------------
# Part-master rebuild from agent builds
# ---------------------------------------------------------------------------

_PM_STATUS: dict[str, Any] = {
    "running": False,
    "last_result": None,
}


def _run_part_master_rebuild(run_missing_agents: bool) -> None:
    """Background task: optionally run agent on unprocessed PDFs, then build master."""
    _PM_STATUS["running"] = True
    _PM_STATUS["last_result"] = None
    processed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    try:
        # ── 1. Run agent on PDFs that have no cached build ─────────────────
        if run_missing_agents and PDF_ROOT.exists():
            agent = CatalogueAgent(max_pages=500)
            for pdf_path in sorted(PDF_ROOT.rglob("*")):
                if pdf_path.suffix not in PDF_EXTS or not pdf_path.is_file():
                    continue
                rel = pdf_path.relative_to(PDF_ROOT).as_posix()
                cache = _cache_path(rel)
                if cache.exists():
                    skipped.append(pdf_path.name)
                    continue
                try:
                    result = agent.run(pdf_path)
                    out = result.to_dict()
                    AGENT_CACHE.mkdir(parents=True, exist_ok=True)
                    cache.write_text(
                        json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    processed.append(pdf_path.name)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{pdf_path.name}: {exc}")

        # ── 2. Build part master — agent JSONs + extractor fallback ────────
        df = build_part_master(pdf_root=PDF_ROOT)

        _PM_STATUS["last_result"] = {
            "ok": True,
            "total_parts": len(df),
            "total_pdfs": len(list(AGENT_CACHE.glob("*.json"))),
            "agents_run": len(processed),
            "agents_skipped": len(skipped),
            "agent_errors": errors,
        }
    except Exception as exc:  # noqa: BLE001
        _PM_STATUS["last_result"] = {"ok": False, "error": str(exc)}
    finally:
        _PM_STATUS["running"] = False


@router.post("/part-master/rebuild")
def rebuild_part_master(
    background_tasks: BackgroundTasks,
    run_missing_agents: bool = Query(
        True,
        description=(
            "If true, run the CatalogueAgent on any PDFs that do not yet have "
            "a cached build before aggregating."
        ),
    ),
) -> dict[str, Any]:
    """Rebuild the catalogue part master from all agent build caches.

    Scans every PDF in data/raw/pdf_catalogues/:
    - If ``run_missing_agents=true`` (default): runs CatalogueAgent on PDFs
      that have no cached JSON yet (may take several minutes per PDF).
    - Aggregates all cached build JSONs into a unified part master:
        data/interim/catalogue_part_master.parquet
        data/outputs/catalogue_part_master.xlsx

    Poll ``GET /catalog/part-master/status`` for progress.
    """
    if _PM_STATUS["running"]:
        return {"queued": False, "message": "Rebuild already running"}

    background_tasks.add_task(_run_part_master_rebuild, run_missing_agents)
    return {
        "queued": True,
        "message": (
            "Part master rebuild started — agents will run on unprocessed PDFs "
            "then aggregate all builds."
            if run_missing_agents
            else "Aggregating existing agent builds into part master."
        ),
    }


@router.get("/part-master/status")
def part_master_status() -> dict[str, Any]:
    """Return the status of the last part master rebuild."""
    pm_parquet = Path("data/interim/catalogue_part_master.parquet").resolve()
    return {
        "running": _PM_STATUS["running"],
        "last_result": _PM_STATUS["last_result"],
        "parquet_exists": pm_parquet.exists(),
        "parquet_size_kb": (
            round(pm_parquet.stat().st_size / 1024, 1) if pm_parquet.exists() else 0
        ),
        "cached_pdfs": len(list(AGENT_CACHE.glob("*.json"))),
    }
