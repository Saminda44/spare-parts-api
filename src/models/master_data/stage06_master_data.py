"""Stage 6 — Master data: supersession resolution + catalog extraction.

Inputs:
  data/raw/SSOP.xlsx                  — 2 024-row supersession & order sheet
  data/raw/pdf_catalogues/**/*.pdf    — model-specific Yamaha parts PDFs

Outputs:
  data/interim/part_master.parquet    — authoritative master (used by Stages 7-14)
  data/interim/supersession_map.parquet
  data/outputs/stage06_master_data.xlsx (4 sheets)
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # pymupdf — renders pages to images for OCR
import pandas as pd
import pdfplumber
import pytesseract
from loguru import logger
from PIL import Image

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

_SSOP_RAW = DATA_RAW / "SSOP.xlsx"
_PDF_DIR = DATA_RAW / "pdf_catalogues"
_MASTER_PARQUET = DATA_INTERIM / "part_master.parquet"
_SUPERSESSION_PARQUET = DATA_INTERIM / "supersession_map.parquet"
_CATALOG_PARTS_PARQUET = DATA_INTERIM / "catalog_parts.parquet"
_OUTPUT_XLSX = DATA_OUTPUTS / "stage06_master_data.xlsx"

# Tesseract binary location (Windows default install path)
_TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

# Yamaha part number patterns:
#   9-digit:  XXX-YYYYY-ZZ      e.g. 2FS-E1111-10, 21C-E3440-01
#   12-digit: XXX-YYYYY-ZZ-WW   e.g. 5KA-E1400-11-00
_PART_RE = re.compile(r"\b([A-Z0-9]{2,5}-[A-Z0-9]{4,7}-\d{2}(?:-\d{2})?)\b")

# Dash-like Unicode characters that some PDF fonts emit instead of ASCII hyphen
_DASH_RE = re.compile(r"[�−–—‐‑]")

# Trigger OCR when extracted text is too short (image page)
_OCR_TEXT_THRESHOLD = 50
# Trigger OCR when >5% of characters are Unicode replacement chars (bad font encoding)
_OCR_UFFFD_RATIO = 0.05

# 12-digit suffix pattern: ends in -NN-NN
_12DIGIT_RE = re.compile(r"-\d{2}-\d{2}$")

# Columns to keep from SSOP with normalised names
_SSOP_RENAME = {
    "Requested P/No":                        "requested_pn",
    "sss":                                   "intermediate_pn",
    "Latest Part No /JAN":                   "latest_pn",
    "Description":                           "description",
    "Qty":                                   "order_qty",
    "EOD ":                                  "eod_rate",
    "Stock ":                                "stock",
    "On orders w.repl ":                     "on_order",
    "Revised Order Qty ":                    "revised_order_qty",
    "Forcasted Monthly sales qty - B2B / WS": "forecast_monthly_qty",
    "Compatible models":                     "compatible_models",
}


# ---------------------------------------------------------------------------
# 6.3 — Supersession resolution
# ---------------------------------------------------------------------------

def load_ssop() -> pd.DataFrame:
    """Load and normalise SSOP.xlsx.

    Business meaning: SSOP is the authoritative supersession + order sheet
    maintained by the Yamaha spare-parts team. One row per requested part number.
    """
    df = pd.read_excel(_SSOP_RAW, dtype=str)
    df = df.rename(columns={c: _SSOP_RENAME[c] for c in _SSOP_RENAME if c in df.columns})

    numeric_cols = ["order_qty", "eod_rate", "stock", "on_order",
                    "revised_order_qty", "forecast_monthly_qty"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    for col in ["requested_pn", "intermediate_pn", "latest_pn", "description",
                "compatible_models"]:
        if col in df.columns:
            df[col] = df[col].fillna("").str.strip()

    logger.info(f"SSOP loaded: {len(df):,} rows")
    return df


def resolve_supersessions(
    df: pd.DataFrame,
) -> tuple[dict[str, str], list[dict]]:
    """Walk supersession chains to find each part's terminal (current) part number.

    Returns:
        mapping: {old_pn: current_pn}  — identity entries included
        cycles:  list of cycle-alert dicts  (should be empty in clean data)

    Business meaning: when a part is superseded (A → B → C), all orders for A
    or B must be fulfilled with C. This function resolves multi-hop chains.
    """
    # Build directed adjacency: requested → latest (one hop from SSOP)
    edge: dict[str, str] = {}
    for _, row in df.iterrows():
        req = row.get("requested_pn", "")
        lat = row.get("latest_pn", "")
        if req and lat and req != lat:
            edge[req] = lat

    mapping: dict[str, str] = {}
    cycles: list[dict] = []

    all_parts = set(df["requested_pn"].dropna()) | set(df["latest_pn"].dropna())
    for start in all_parts:
        if start in mapping:
            continue
        visited: list[str] = []
        current = start
        while current in edge:
            if current in visited:
                # Cycle detected
                cycle_str = " → ".join(visited[visited.index(current):] + [current])
                cycles.append({"start": start, "cycle": cycle_str})
                logger.warning(f"Supersession cycle detected: {cycle_str}")
                break
            visited.append(current)
            current = edge[current]
        mapping[start] = current

    logger.info(
        f"Supersession resolved: {sum(1 for k, v in mapping.items() if k != v)} "
        f"superseded parts → terminal; {len(cycles)} cycles"
    )
    if cycles:
        logger.warning(f"{len(cycles)} supersession cycle(s) found — review stage06 output")

    return mapping, cycles


def build_part_master(
    df: pd.DataFrame,
    mapping: dict[str, str],
) -> pd.DataFrame:
    """Build the canonical part master from SSOP rows.

    Each row in the master represents the *current* (latest) part number.
    Where a part has been superseded, the original part number is recorded
    in `superseded_from`.

    Business meaning: downstream stages (stock, classification, forecast)
    must use canonical part numbers. This table is the single source of truth.
    """
    df = df.copy()
    df["current_pn"] = df["requested_pn"].map(mapping).fillna(df["requested_pn"])
    df["is_superseded"] = df["requested_pn"] != df["current_pn"]

    # For superseded parts, surface the original pn in a note column
    df["superseded_from"] = df.apply(
        lambda r: r["requested_pn"] if r["is_superseded"] else "", axis=1
    )

    # One canonical row per current_pn (latest takes precedence for all attrs)
    # Aggregate: keep latest attrs from latest_pn rows; collect superseded_from list
    master = (
        df.sort_values("is_superseded")   # non-superseded rows first
        .groupby("current_pn", as_index=False)
        .agg(
            description=("description", "first"),
            compatible_models=("compatible_models", "first"),
            order_qty=("order_qty", "sum"),
            eod_rate=("eod_rate", "max"),
            stock=("stock", "sum"),
            on_order=("on_order", "sum"),
            revised_order_qty=("revised_order_qty", "sum"),
            forecast_monthly_qty=("forecast_monthly_qty", "sum"),
            superseded_from=("superseded_from", lambda x: "; ".join(v for v in x if v)),
        )
        .reset_index(drop=True)
    )

    master = master.rename(columns={"current_pn": "part_number"})
    master["has_supersession"] = master["superseded_from"] != ""

    logger.info(f"Part master built: {len(master):,} canonical parts")
    return master


def build_supersession_map(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Flat lookup table: every superseded requested_pn → current canonical pn."""
    # Rebuild edge dict for accurate hop counting (not the resolved terminal mapping)
    edge: dict[str, str] = {
        row["requested_pn"]: row["latest_pn"]
        for _, row in df.iterrows()
        if row.get("requested_pn") != row.get("latest_pn")
        and row.get("requested_pn") and row.get("latest_pn")
    }
    rows = [
        {"requested_pn": k, "current_pn": v, "hops": _chain_length(k, edge)}
        for k, v in mapping.items()
        if k != v
    ]
    sup_map = pd.DataFrame(rows, columns=["requested_pn", "current_pn", "hops"])

    # Enrich with description of the current part
    desc_lookup = dict(zip(df["requested_pn"], df["description"]))
    sup_map["current_description"] = sup_map["current_pn"].map(desc_lookup).fillna("")
    sup_map["old_description"] = sup_map["requested_pn"].map(desc_lookup).fillna("")

    logger.info(f"Supersession map: {len(sup_map):,} superseded entries")
    return sup_map


def _chain_length(start: str, mapping: dict[str, str]) -> int:
    """Count hops from start to terminal."""
    hops = 0
    seen: set[str] = set()
    current = start
    while current in mapping and mapping[current] != current and current not in seen:
        seen.add(current)
        current = mapping[current]
        hops += 1
        if hops > 20:  # guard against undetected cycles
            break
    return hops


# ---------------------------------------------------------------------------
# 6.1 — PDF catalog extraction
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Replace non-ASCII dash variants with plain hyphen for regex matching."""
    return _DASH_RE.sub("-", text)


def _needs_ocr(text: str) -> bool:
    """True when pdfplumber extraction is likely insufficient."""
    stripped = text.strip()
    if len(stripped) < _OCR_TEXT_THRESHOLD:
        return True
    # High ratio of replacement chars indicates font encoding failure
    if len(text) > 0 and text.count("�") / len(text) > _OCR_UFFFD_RATIO:
        return True
    return False


def _ocr_page(fitz_page: fitz.Page) -> str:
    """Render a single PDF page at 300 DPI and extract text with Tesseract."""
    mat = fitz.Matrix(300 / 72, 300 / 72)
    pix = fitz_page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", [pix.width, pix.height], pix.samples)
    # psm 6 = assume uniform block of text (best for catalog page grids)
    return pytesseract.image_to_string(img, config="--psm 6")


def scan_catalog_pdf(pdf_path: Path, model_name: str) -> list[dict]:
    """Extract Yamaha part numbers (9-digit and 12-digit) from a catalog PDF.

    Strategy per page:
      1. Extract text with pdfplumber.
      2. If text is sparse (<50 chars) or has high � ratio (font encoding
         failure), fall back to Tesseract OCR via pymupdf page rendering.
      3. Normalize non-ASCII dash variants to ASCII hyphen.
      4. Apply regex to collect all matching part numbers.

    Business meaning: PDF catalogs are the authoritative bill-of-materials per
    model variant. Extracting them lets us know which parts are applicable to
    which fleet segment.
    """
    parts: list[dict] = []
    ocr_pages = 0
    text_pages = 0
    try:
        fitz_doc = fitz.open(str(pdf_path))
        with pdfplumber.open(pdf_path) as plumber_doc:
            if len(plumber_doc.pages) > 200:
                logger.warning(f"Large PDF ({len(plumber_doc.pages)} pages): {pdf_path.name}")
            for plumber_page, fitz_page in zip(plumber_doc.pages, fitz_doc):
                raw_text = plumber_page.extract_text() or ""
                if _needs_ocr(raw_text):
                    text = _ocr_page(fitz_page)
                    ocr_pages += 1
                else:
                    text = _normalize_text(raw_text)
                    text_pages += 1
                for match in _PART_RE.finditer(text):
                    parts.append({
                        "part_number": match.group(1),
                        "model": model_name,
                        "source_file": pdf_path.name,
                        "ocr_used": ocr_pages > 0,
                    })
        fitz_doc.close()
    except Exception as exc:
        logger.warning(f"PDF extraction failed for {pdf_path.name}: {exc}")
        return parts

    if ocr_pages:
        logger.debug(
            f"{pdf_path.name}: {len(parts)} parts "
            f"(text={text_pages}p / OCR={ocr_pages}p)"
        )
    return parts


def extract_catalog_parts(pdf_dir: Path) -> pd.DataFrame:
    """Scan all PDFs under pdf_dir and return a deduplicated model-to-part table.

    Returns:
        DataFrame with columns: part_number_12, model, source_file
    """
    all_parts: list[dict] = []
    pdf_files = sorted(pdf_dir.rglob("*.pdf"))
    logger.info(f"Scanning {len(pdf_files)} catalog PDFs under {pdf_dir}")

    for pdf_path in pdf_files:
        # Model name = immediate parent folder name
        model_name = pdf_path.parent.name
        parts = scan_catalog_pdf(pdf_path, model_name)
        all_parts.extend(parts)
        if parts:
            logger.debug(f"{pdf_path.name}: {len(parts)} part references")

    if not all_parts:
        logger.warning("No part numbers extracted from any catalog PDF")
        return pd.DataFrame(columns=["part_number", "model", "source_file", "ocr_used"])

    catalog_df = pd.DataFrame(all_parts).drop_duplicates(
        subset=["part_number", "model"]
    )
    ocr_files = catalog_df[catalog_df["ocr_used"]]["source_file"].nunique()
    logger.info(
        f"Catalog extraction complete: {len(catalog_df):,} unique part-model pairs "
        f"({catalog_df['part_number'].nunique():,} distinct part numbers, "
        f"{ocr_files} PDFs required OCR)"
    )
    return catalog_df


def merge_catalog_coverage(
    master: pd.DataFrame,
    catalog_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add catalog_models column: which model folders contain each part number.

    Matching strategy (SSOP uses 9-digit; PDFs may return 9-digit or 12-digit):
      - 12-digit catalog part → strip trailing -WW to get 9-digit → match SSOP
      - 9-digit catalog part  → match SSOP directly

    Business meaning: knowing which models a part appears in lets the team
    prioritise stock for high-UIO models and identify slow-movers tied to
    discontinued models.
    """
    if catalog_df.empty:
        master = master.copy()
        master["catalog_models"] = ""
        return master

    catalog_df = catalog_df.copy()
    # Normalise all catalog part numbers to 9-digit for SSOP matching
    catalog_df["part_number_9"] = catalog_df["part_number"].apply(
        lambda pn: pn[:-3] if _12DIGIT_RE.search(pn) else pn
    )

    model_map = (
        catalog_df.groupby("part_number_9")["model"]
        .apply(lambda x: "; ".join(sorted(set(x))))
        .to_dict()
    )

    master = master.copy()
    master["catalog_models"] = master["part_number"].map(model_map).fillna("")
    in_catalog = (master["catalog_models"] != "").sum()
    logger.info(
        f"Catalog coverage: {in_catalog:,}/{len(master):,} master parts "
        f"found in at least one PDF catalog"
    )
    return master


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

def _write_excel(
    master: pd.DataFrame,
    sup_map: pd.DataFrame,
    cycles: list[dict],
    catalog_df: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(_OUTPUT_XLSX, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white"})
        num = wb.add_format({"num_format": "#,##0"})
        dec = wb.add_format({"num_format": "#,##0.00"})

        def write_sheet(df: pd.DataFrame, sheet: str, col_widths: list[int]) -> None:
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            for i, (col, w) in enumerate(zip(df.columns, col_widths)):
                ws.set_column(i, i, w)
                ws.write(0, i, col, hdr)

        # Sheet 1 — Part Master
        write_sheet(
            master,
            "Part Master",
            [20, 40, 30, 10, 8, 10, 10, 12, 16, 12, 40],
        )

        # Sheet 2 — Supersession Map
        write_sheet(
            sup_map if not sup_map.empty else pd.DataFrame(
                columns=["requested_pn", "current_pn", "hops",
                         "old_description", "current_description"]
            ),
            "Supersession Map",
            [20, 20, 6, 35, 35],
        )

        # Sheet 3 — Cycle Alerts
        cycles_df = pd.DataFrame(cycles) if cycles else pd.DataFrame(
            columns=["start", "cycle"]
        )
        write_sheet(cycles_df, "Cycle Alerts", [20, 60])

        # Sheet 4 — Catalog Coverage (parts per model)
        if not catalog_df.empty:
            coverage = (
                catalog_df.groupby("model")
                .agg(
                    distinct_parts=("part_number", "nunique"),
                    pdf_files=("source_file", "nunique"),
                )
                .reset_index()
                .sort_values("distinct_parts", ascending=False)
            )
        else:
            coverage = pd.DataFrame(columns=["model", "distinct_parts", "pdf_files"])
        write_sheet(coverage, "Catalog Coverage", [30, 16, 12])

    logger.info(f"Excel report written → {_OUTPUT_XLSX}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(refresh: bool = False) -> None:
    """Stage 6 entry point: build part master and supersession map.

    Args:
        refresh: if True, re-run even if cached outputs exist.
    """
    if not refresh and _MASTER_PARQUET.exists():
        logger.info("Stage 6 cached — skipping (use --refresh to force)")
        return

    logger.info("Stage 6: Master Data — supersession + catalog extraction")

    # 6.3 — Supersession + master
    ssop = load_ssop()
    mapping, cycles = resolve_supersessions(ssop)
    master = build_part_master(ssop, mapping)
    sup_map = build_supersession_map(ssop, mapping)

    # 6.1 — PDF catalog extraction
    catalog_df = extract_catalog_parts(_PDF_DIR)

    # Merge catalog coverage into master
    master = merge_catalog_coverage(master, catalog_df)

    # Persist
    master.to_parquet(_MASTER_PARQUET, index=False)
    logger.info(f"Part master saved → {_MASTER_PARQUET} ({len(master):,} rows)")

    sup_map.to_parquet(_SUPERSESSION_PARQUET, index=False)
    logger.info(f"Supersession map saved → {_SUPERSESSION_PARQUET} ({len(sup_map):,} rows)")

    if not catalog_df.empty:
        catalog_df.to_parquet(_CATALOG_PARTS_PARQUET, index=False)
        logger.info(f"Catalog parts saved → {_CATALOG_PARTS_PARQUET} ({len(catalog_df):,} rows)")

    _write_excel(master, sup_map, cycles, catalog_df)

    # Summary
    superseded_count = master["has_supersession"].sum()
    in_catalog = (master["catalog_models"] != "").sum()
    logger.info(
        f"Stage 6 complete | "
        f"Canonical parts: {len(master):,} | "
        f"Superseded chains: {superseded_count} | "
        f"Cycle alerts: {len(cycles)} | "
        f"In catalog: {in_catalog:,} / {len(master):,}"
    )

    if cycles:
        logger.warning(
            f"{len(cycles)} supersession cycle(s) detected — "
            "review 'Cycle Alerts' sheet before downstream processing"
        )
