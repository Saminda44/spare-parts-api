"""Stage 6.1 — Yamaha PDF Catalogue Extractor.

Extracts the seven-column parts table from Yamaha motorcycle PDF catalogues:
    Section | Fig. No. | Ref. No. | Part No. | Description | Q'ty | Remarks

Output matches the structure of GPD155D-A_Parts_Catalogue.xlsx exactly.

Strategy
--------
Primary: word-position clustering.
  pdfplumber extracts each word with its (x, y) coordinates.  Words on the
  same Y band are grouped into a logical row, then each word is assigned to a
  column by its X position relative to auto-detected column boundaries.

Fallback: text-line regex.
  If a page yields no positioned words (image-based) the page is flagged;
  a plain-text regex pass is attempted and, if still empty, the page is
  logged as requiring OCR (not implemented here — flag only).

Column boundary detection
--------------------------
Each "parts page" starts with a header band containing the words
  "PART NO." (or "12 DIGIT") and "REMARKS".
The extractor reads the first 25 word-rows of every page to locate these
anchors and derive the five column X-ranges:
    [ref_start, pn_start, desc_start, qty_start, rem_start]

If the header isn't found (e.g., continuation pages), the previous page's
bounds are re-used.

Part-number pattern
-------------------
Yamaha standard:  XYZ-ABCDE-NN  or  XYZ-ABCDE-NN-CC  (last two-char segment
always digits for the revision, optional two-char colour/variant suffix).
The regex is strict enough to avoid matching model-version stamps (e.g. 1BV2).
"""

from __future__ import annotations

import re
from collections import Counter as _Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLUMNS: list[str] = [
    "section", "fig_no", "ref_no", "part_no",
    "description", "qty", "remarks",
]
DISPLAY_HEADERS: list[str] = [
    "Section", "Ref. No.", "Part No.",
    "Description", "Q'ty", "Remarks",
]

# Yamaha part numbers.  Some PDFs encode hyphens as U+2013 (en-dash) instead
# of U+002D (ASCII hyphen), so both are accepted as separators.
# Formats supported:
#   3-part: XXX-XXXXX-NN         e.g. 5HK-14147-00
#   4-part: XXX-XXXXX-NN-XX      e.g. 5HK-14147-00-P0
#   2-part all-digit: NNNNN-NNNNN    e.g. 93210-29800 (standard Yamaha OEM)
#   2-part alphanumeric second seg:  e.g. 93306-302YP, 93306-255X5
#       (bearing/seal codes where the suffix encodes type+size with letters)
#   2-part mixed first seg:          e.g. 95E32-06010 (Yamaha fastener series)
#   12-char: XXXXXXXXXXXX            e.g. 5DGE35860100 (ENTICER/YBX125)
_DASH = r'[-–]'
_PN_PAT = re.compile(
    r'\b('
    # 3- or 4-segment: XXX-XXXXX-NN[-XX]
    # Third segment is [A-Z0-9]{2} (not [0-9]{2}) because Yamaha graphic/decal parts
    # use letter-prefixed revision codes: B65-F174G-C0, B65-F174H-D0, etc.
    r'[A-Z0-9]{2,5}' + _DASH + r'[A-Z0-9]{3,8}' + _DASH + r'[A-Z0-9]{2}(?:' + _DASH + r'[A-Z0-9]{2})?'
    # 2-segment with 5-char alphanumeric first segment, 3-8 char alphanumeric second
    # covers: NNNNN-NNNNN, NNNNN-NNNYX, XNNNN-NNNNN, etc.
    r'|[A-Z0-9]{5}' + _DASH + r'[A-Z0-9]{3,8}'
    r'|[A-Z0-9]{12}'
    r')\b'
)
# FIG. section header — "FIG. 1 (1D0) CYLINDER HEAD"
_FIG_PAT = re.compile(
    r'^FIG\.\s+(\d+[A-Z]?)\s*(?:\([^)]+\)\s*)?(.+)$', re.IGNORECASE
)
# Lines to skip (header labels, noise)
_SKIP_PAT = re.compile(
    r'^(REF\.?|PART\s+NO\.?|PART\s+NAME|DESCRIPTION|REMARKS|NO\.?|'
    r'CONTENTS?|Q\'?TY\.?|12\s+DIGIT|9\s+DIGIT)$',
    re.IGNORECASE,
)
# Model-code stamps at top of page, e.g. "1BV2", "21C1", "5AK5"
_MODEL_STAMP_PAT = re.compile(r'^[A-Z0-9]{2,5}\d[A-Z]?$')
# Copyright line injected by some PDFs
_COPYRIGHT_PAT = re.compile(r'\s*No part of this Publication.*', re.IGNORECASE)
# Page-type skips — these page formats are not FIG parts tables
# KITS pages: "KITS-5YY6", "KITS 5YY6" — kit assembly lists
_KITS_PAGE_PAT = re.compile(r'\bKITS[-\s]\S', re.IGNORECASE)
# Cross-reference index pages: "PART NO PART NO PART NO…" repeated 3+ times as column headers
_XREF_PAGE_PAT = re.compile(r'(?:PART\s+NO[\s.]*){3}', re.IGNORECASE)
# CID glyph fallback artifact from pdfplumber font decoding (e.g. "(cid:2)")
_CID_PAT = re.compile(r'\s*\(cid:\d+\)', re.IGNORECASE)

# Cover-page variant code pattern: Yamaha writes each variant as "( B65J )"
# on the title page listing all models covered by the catalogue.
_COVER_VARIANT_PAT = re.compile(r'\(\s*([A-Z0-9]{3,5})\s*\)')
# Slash-separated pair pattern for catalogues that list two variants as
# "B811/B821" or "(B9E1 / B9E2)" without separate paren wrapping.
_SLASH_VARIANT_PAT = re.compile(r'\b([A-Z0-9]{3,5})\s*/\s*([A-Z0-9]{3,5})\b')

# Leading model/variant stamp that may precede "FIG." on the same pdfplumber
# word-row, e.g. "B65J FIG. 14 FRAME" or "21C1 FIG. 1 CYLINDER HEAD".
_STAMP_PREFIX_PAT = re.compile(r'^[A-Z0-9]{2,6}\s+', re.IGNORECASE)

# Partial FIG line — just the number, section name absent or on the next row.
_FIG_PARTIAL_PAT = re.compile(r'(?:^|.*\s)(FIG\.\s+(\d+[A-Z]?))\s*$', re.IGNORECASE)


def _try_fig_match(
    joined: str,
    word_rows: list[tuple[float, list[dict]]],
    row_idx: int,
) -> re.Match | None:
    """Try all FIG-header matching strategies, returning the first hit.

    Three strategies in order:
    1. Direct ``_FIG_PAT.match`` — the normal case.
    2. Strip a leading model/variant-stamp prefix then ``_FIG_PAT.match`` —
       handles lines like "B65J FIG. 14 FRAME" that pdfplumber combines.
    3. Two-row match — the current row is "FIG. N" only (section name absent),
       and the very next word-row contains the section name.  The returned
       match object's group(2) will be the section name from the next row.
    """
    # Strategy 1: standard match
    m = _FIG_PAT.match(joined)
    if m:
        return m

    # Strategy 2: strip stamp prefix
    stripped = _STAMP_PREFIX_PAT.sub("", joined)
    m = _FIG_PAT.match(stripped)
    if m:
        return m

    # Strategy 3: "FIG. N" only on this row — peek at the next for section name
    partial = _FIG_PARTIAL_PAT.match(joined)
    if partial and row_idx + 1 < len(word_rows):
        next_text = " ".join(w["text"] for w in word_rows[row_idx + 1][1]).strip()
        if next_text and not _PN_PAT.search(next_text) and not _SKIP_PAT.match(next_text):
            combined = f"FIG. {partial.group(2)} {next_text}"
            m = _FIG_PAT.match(combined)
            if m:
                return m

    return None

# Y-tolerance for grouping words into the same logical row (points).
# Using a sequential-window approach (not fixed buckets) so small baseline
# variations (e.g. BB8-E1392-00 rendered 4 pts above its description text)
# don't silently split one visual row into two word-rows.
_Y_TOL = 5
# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ColBounds:
    """X-coordinate boundaries for the right-hand columns of a Yamaha parts page.

    rem_start    — x-start of the REMARKS column; used to strip trailing
                   cross-reference codes from the Q'ty zone.
    qty_col_xs   — x-centre of each variant Q'ty column, in left-to-right order.
                   Empty for single-variant PDFs.  When present, digit words in a
                   data row are assigned to the nearest column so that
                   partial-coverage parts (where some variant cells are blank)
                   produce a "/" -separated qty string with "" for absent variants,
                   e.g. "1//1/" for a part that only applies to variants 0 and 2.
    """
    rem_start:   float      = 9999.0
    qty_col_xs:  list[float] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Output of extracting one PDF file."""
    pdf_path:       Path
    model:          str
    rows:           list[dict[str, str]]
    pages_scanned:  int
    sections_found: int
    ocr_flagged:    int    # pages that needed OCR but couldn't be handled
    variants:          list[str] = field(default_factory=list)  # e.g. ["B65J","B65L","B65M","B65N"]
    # colour_codes: [{abbreviation, name, code, is_model_colour}] from the PDF's colour table
    colour_codes:      list[dict] = field(default_factory=list)
    manufacture_year:  str | None = None   # e.g. "2019" from ©2019 on the cover page
    warnings:          list[str] = field(default_factory=list)
    error:             str | None = None

    @property
    def df(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame(columns=COLUMNS)
        return pd.DataFrame(self.rows, columns=COLUMNS)

    def summary(self) -> str:
        return (
            f"{self.pdf_path.name}: "
            f"{len(self.rows)} rows | {self.sections_found} sections | "
            f"{self.pages_scanned} pages | "
            f"ocr_flagged={self.ocr_flagged}"
            + (f" | WARN: {self.warnings[0]}" if self.warnings else "")
        )


# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------

class YamahaCatalogueExtractor:
    """Extract parts tables from Yamaha PDF catalogues.

    Business meaning: turns unstructured PDF text into the same seven-column
    table produced by hand in GPD155D-A_Parts_Catalogue.xlsx so that all
    Yamaha model catalogues can be searched, filtered, and joined uniformly.

    Usage::

        ex = YamahaCatalogueExtractor()
        result = ex.extract(Path("data/raw/pdf_catalogues/FZ & FZS/FZ16 21C1.pdf"), model="FZ & FZS")
        df = result.df  # pandas DataFrame with COLUMNS

        all_df = ex.extract_all(Path("data/raw/pdf_catalogues"))
    """

    def __init__(self, max_pages: int = 500) -> None:
        self.max_pages = max_pages

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, pdf_path: Path, model: str = "") -> ExtractionResult:
        """Extract parts table from a single PDF catalogue."""
        import pdfplumber  # lazy — not needed at import time

        rows: list[dict[str, str]] = []
        sections_seen: set[str] = set()
        ocr_flagged = 0
        warnings: list[str] = []
        pages_scanned = 0

        current_section = ""
        current_fig = ""
        last_bounds: ColBounds | None = None
        variant_counter: _Counter[tuple[str, ...]] = _Counter()

        # Pre-detect variant count from cover page so _split_after pops exactly
        # N qty digits per row (preventing trailing description digits from being
        # consumed, e.g. "GUIDE, VALVE 1" kept intact instead of "GUIDE, VALVE").
        pre_variants = self._resolve_variants(pdf_path, _Counter())
        num_variants = len(pre_variants)

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[: self.max_pages]:
                    pages_scanned += 1
                    words = page.extract_words(
                        x_tolerance=4, y_tolerance=4,
                        keep_blank_chars=False, use_text_flow=False,
                    )

                    if not words:
                        ocr_flagged += 1
                        continue

                    word_rows = self._group_by_y(words)

                    # Skip KITS pages and cross-reference index pages
                    page_header = " ".join(
                        " ".join(w["text"] for w in ws)
                        for _, ws in word_rows[:8]
                    )
                    if _KITS_PAGE_PAT.search(page_header) or _XREF_PAGE_PAT.search(page_header):
                        continue

                    # Collect variant codes from column-header rows on this page
                    self._collect_variant_row(word_rows, variant_counter)

                    # Merge newly-detected bounds with last_bounds rather than
                    # replacing.  Without this, a continuation page that has
                    # variant column-header rows (→ qty_col_xs) but no REMARKS
                    # keyword returns ColBounds(rem_start=9999, qty_col_xs=[…])
                    # which overwrites the rem_start found on the header page,
                    # silently killing remark extraction for all later pages.
                    new_bounds = self._detect_col_bounds(word_rows)
                    if new_bounds is None:
                        bounds = last_bounds
                    else:
                        if last_bounds is not None:
                            if new_bounds.rem_start >= 9000:
                                new_bounds.rem_start = last_bounds.rem_start
                            if not new_bounds.qty_col_xs:
                                new_bounds.qty_col_xs = last_bounds.qty_col_xs
                        bounds = new_bounds

                    page_rows, new_section, new_fig, page_sects = (
                        self._parse_word_rows(word_rows, bounds, current_section, current_fig, num_variants)
                    )
                    rows.extend(page_rows)
                    sections_seen.update(page_sects)

                    if new_section:
                        current_section = new_section
                    if new_fig:
                        current_fig = new_fig
                    if bounds:
                        last_bounds = bounds

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Extraction failed for {pdf_path}: {exc}")
            return ExtractionResult(
                pdf_path=pdf_path, model=model, rows=rows,
                pages_scanned=pages_scanned, sections_found=len(sections_seen),
                ocr_flagged=ocr_flagged, warnings=warnings, error=str(exc),
            )

        # Fallback: if positional extraction found nothing, try text-line parser
        if not rows:
            logger.warning(f"{pdf_path.name}: positional pass empty — trying text fallback")
            warnings.append("positional extraction empty; used text fallback")
            rows, sections_seen = self._text_fallback(pdf_path)

        # Resolve model variants: prefer cover-page pattern, fall back to column headers
        variants = self._resolve_variants(pdf_path, variant_counter)
        if variants:
            logger.info(f"{pdf_path.name}: detected variants {variants}")

        # Extract colour code table from PDF intro pages
        colour_codes = self._extract_colour_codes(pdf_path)
        if colour_codes:
            logger.info(f"{pdf_path.name}: found {len(colour_codes)} colour codes")

        # Extract manufacture year from cover page (©YYYY / "1st edition, May YYYY")
        manufacture_year = self._extract_manufacture_year(pdf_path)
        if manufacture_year:
            logger.info(f"{pdf_path.name}: manufacture year {manufacture_year}")

        return ExtractionResult(
            pdf_path=pdf_path, model=model, rows=rows,
            pages_scanned=pages_scanned, sections_found=len(sections_seen),
            ocr_flagged=ocr_flagged, variants=variants,
            colour_codes=colour_codes, manufacture_year=manufacture_year,
            warnings=warnings,
        )

    def extract_all(
        self,
        pdf_root: Path,
        save_path: Path | None = None,
    ) -> pd.DataFrame:
        """Batch-extract every PDF under pdf_root and return a merged DataFrame.

        Args:
            pdf_root:  Root folder containing model sub-directories.
            save_path: Optional path to write the result as Parquet.

        Returns:
            DataFrame with COLUMNS + ["model", "source_file"] columns.
        """
        all_dfs: list[pd.DataFrame] = []

        # Use case-insensitive .pdf suffix to avoid double-counting on Windows
        pdf_files = sorted(
            {f for f in pdf_root.rglob("*") if f.suffix.lower() == ".pdf"},
            key=lambda p: (p.parent.name.upper(), p.name.upper()),
        )

        for pdf_file in pdf_files:
            # Derive model name from direct parent folder
            model_name = pdf_file.parent.name
            logger.info(f"Extracting [{model_name}] {pdf_file.name}")

            result = self.extract(pdf_file, model=model_name)
            logger.debug(result.summary())

            if result.error:
                logger.error(f"  → skipped: {result.error}")
                continue

            if not result.rows:
                logger.warning(f"  → no rows extracted")
                continue

            df = result.df.copy()
            df["model"] = model_name
            df["source_file"] = pdf_file.name
            df["ocr_used"] = False
            all_dfs.append(df)

        if not all_dfs:
            empty = pd.DataFrame(
                columns=COLUMNS + ["model", "source_file", "ocr_used"]
            )
            if save_path:
                empty.to_parquet(save_path, index=False)
            return empty

        merged = pd.concat(all_dfs, ignore_index=True)
        merged = merged.drop_duplicates(
            subset=["model", "source_file", "part_no", "ref_no", "fig_no"],
        )

        logger.info(
            f"Extraction complete: {len(merged)} total rows | "
            f"{merged['part_no'].nunique()} distinct part numbers | "
            f"{merged['model'].nunique()} models"
        )

        if save_path:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            merged.to_parquet(save_path, index=False)
            logger.info(f"Saved → {save_path}")

        return merged

    # ------------------------------------------------------------------
    # Word-position helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group_by_y(words: list[dict[str, Any]]) -> list[tuple[float, list[dict]]]:
        """Group words into rows by Y coordinate using a sequential sliding window.

        Words are sorted by `top`, then each word joins the current row if its
        `top` is within _Y_TOL pts of the row's representative Y.  This avoids
        the fixed-bucket rounding artifact where two words 4 pts apart can fall
        into different buckets even though they're on the same visual line.
        """
        if not words:
            return []
        sorted_w = sorted(words, key=lambda w: w["top"])
        groups: list[tuple[float, list[dict]]] = []
        row_y = sorted_w[0]["top"]
        row_ws: list[dict] = [sorted_w[0]]
        for w in sorted_w[1:]:
            if abs(w["top"] - row_y) <= _Y_TOL:
                row_ws.append(w)
            else:
                groups.append((row_y, sorted(row_ws, key=lambda ww: ww["x0"])))
                row_y = w["top"]
                row_ws = [w]
        groups.append((row_y, sorted(row_ws, key=lambda ww: ww["x0"])))
        return groups

    @staticmethod
    def _detect_col_bounds(
        word_rows: list[tuple[float, list[dict]]],
    ) -> ColBounds | None:
        """Detect REMARKS and Q'ty column boundaries from the page header rows.

        Scans the first 25 word-rows for:
        - "REMARKS" keyword  → sets rem_start (rightmost column anchor)
        - Variant-code tokens (e.g. "J56B", "L56B", "M56B", "N56B") on header
          rows (rows without part numbers) → accumulates x-centres as qty_col_xs

        Some PDFs split the variant-code header across two Y-rows (e.g. AEROX:
        "L56B M56B N56B" on y=45, "J56B" on y=50).  The previous single-row
        detection missed the second row.  We now accumulate codes from ALL
        header rows and sort by x-position, so every qty column is captured.
        """
        rem_start = 9999.0
        raw_vc_xs: list[float] = []   # accumulate variant-code x-centres

        for _y, ws in word_rows[:25]:
            texts = [w["text"].upper() for w in ws]
            joined_up = " ".join(texts)

            # REMARKS anchor
            rem_idx = next((i for i, t in enumerate(texts) if t == "REMARKS"), None)
            if rem_idx is not None:
                rem_start = ws[rem_idx]["x0"]

            # CRUX-style "9 DIGIT" header
            digit_indices = [i for i, t in enumerate(texts) if t == "DIGIT"]
            if len(digit_indices) >= 2:
                rem_start = ws[digit_indices[-1] - 1]["x0"]

            # Skip data rows (contain part numbers) — only collect codes from
            # column-header rows.  Remark qualifiers (DBNM8, MBL2, etc.) appear
            # on data rows and would otherwise pollute qty_col_xs.
            if _PN_PAT.search(joined_up):
                continue

            # Accumulate variant-code column headers from every header row
            for w in ws:
                if YamahaCatalogueExtractor._is_variant_code(w["text"].upper()):
                    raw_vc_xs.append((w["x0"] + w["x1"]) / 2)

        # Sort and deduplicate near-identical x-centres (within 8 px)
        qty_col_xs: list[float] = []
        for cx in sorted(raw_vc_xs):
            if not qty_col_xs or cx - qty_col_xs[-1] > 8.0:
                qty_col_xs.append(cx)

        if rem_start < 9000 or qty_col_xs:
            return ColBounds(rem_start=rem_start, qty_col_xs=qty_col_xs)
        return None

    @staticmethod
    def _is_variant_code(s: str) -> bool:
        """True for short alphanumeric tokens that mix letters and digits (variant codes)."""
        return (3 <= len(s) <= 5 and s.isalnum()
                and any(c.isdigit() for c in s)
                and any(c.isalpha() for c in s))

    @staticmethod
    def _collect_variant_row(
        word_rows: list[tuple[float, list[dict]]],
        counter: _Counter,
    ) -> None:
        """Scan word-rows for a QTY column-header row containing 3+ variant codes.

        In multi-variant catalogues Yamaha prints the variant codes as rotated
        column headers (e.g. J56B L56B M56B N56B for AEROX variants B65J–B65N).
        The rotated rendering means pdfplumber may read the chars bottom-to-top,
        but the tokens are still short mixed alphanumeric strings — same shape as
        real variant codes.  We count co-occurring tuples across pages so the most
        frequent combination wins.
        """
        for _y, ws in word_rows:
            texts = [w["text"].upper() for w in ws]
            vc = [t for t in texts if YamahaCatalogueExtractor._is_variant_code(t)]
            if len(vc) >= 3:
                counter[tuple(vc)] += 1

    @staticmethod
    def _extract_manufacture_year(pdf_path: Path) -> str | None:
        """Extract manufacture / publication year from the PDF cover page.

        Scans the first 3 pages for Yamaha copyright patterns:
          ©2019 by Yamaha ...
          1st edition, May 2019
          PARTS CATALOGUE  2019

        Business meaning: identifies the model year this parts catalogue covers.
        Returns the 4-digit year string, e.g. "2019", or None if not found.
        """
        import pdfplumber as _plumber

        # ©YYYY or ©YYYY (full-width ©)
        _COPYRIGHT = re.compile(r'[©©]\s*(\d{4})')
        # "edition, Month YYYY" or "edition YYYY"
        _EDITION   = re.compile(r'edition[,\s]+\w+\s+(\d{4})', re.IGNORECASE)
        # Standalone 4-digit year that looks plausible (avoid part numbers etc.)
        _YEAR_BARE = re.compile(r'\b(19[89]\d|20[012]\d)\b')

        try:
            with _plumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[:3]:
                    text = page.extract_text() or ""
                    for pat in (_COPYRIGHT, _EDITION, _YEAR_BARE):
                        m = pat.search(text)
                        if m:
                            yr = int(m.group(1))
                            if 1990 <= yr <= 2040:
                                return str(yr)
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _extract_colour_codes(pdf_path: Path) -> list[dict]:
        """Extract the colour-variant table from PDF intro pages.

        Yamaha catalogues include a table mapping colour abbreviation → colour
        name → paint code, e.g. "CM6(*) | CYAN METALLIC 6 | 1344".
        The (*) suffix marks the model's primary colour variant.

        Returns list of dicts: {abbreviation, name, code, is_model_colour}.
        Empty list if not found.
        """
        import pdfplumber as _plumber

        # Abbreviation: 2-8 alphanumeric chars, optionally followed by (*) or *
        _ABBR_PAT = re.compile(r'^([A-Z0-9]{2,8})(\(\s*\*\s*\)|\*)?$')
        # Paint code: 3-5 alphanumeric chars (e.g. "1344", "00V9", "0098", "SMX")
        _CODE_PAT = re.compile(r'^[A-Z0-9]{3,5}$')
        # Words that indicate a header row — skip these
        _HEADER_WORDS = frozenset({
            'ABBREVIATION', 'ABBR', 'COLOUR', 'COLOR', 'NAME',
            'CODE', 'COLOURNAME', 'COLOURCODE',
        })

        def _parse_row(abbr_raw: str, name: str, code_raw: str) -> dict | None:
            abbr_clean = abbr_raw.replace(' ', '').upper()
            code_clean = code_raw.replace(' ', '').upper()
            m = _ABBR_PAT.match(abbr_clean)
            if not m or m.group(1) in _HEADER_WORDS:
                return None
            if not _CODE_PAT.match(code_clean):
                return None
            name = name.strip()
            if len(name) < 3:
                return None
            # Reject rows where "name" looks like a header or column label
            if name.upper() in _HEADER_WORDS:
                return None
            return {
                "abbreviation": m.group(1),
                "name": name,
                "code": code_clean,
                "is_model_colour": bool(m.group(2)),
            }

        try:
            with _plumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[:15]:
                    # --- Strategy 1: pdfplumber ruled-table extraction ---
                    for table in (page.extract_tables() or []):
                        if not table or len(table) < 3:
                            continue
                        entries: list[dict] = []
                        for row in table:
                            if not row or len(row) < 3:
                                continue
                            e = _parse_row(
                                str(row[0] or ''),
                                str(row[1] or ''),
                                str(row[2] or ''),
                            )
                            if e:
                                entries.append(e)
                        if len(entries) >= 3:
                            return entries

                    # --- Strategy 2: word-position row clustering ---
                    words = page.extract_words() or []
                    if not words:
                        continue

                    # Group words into Y-rows (same tolerance as main extractor)
                    rows_by_y: dict[float, list[dict]] = {}
                    for w in words:
                        y_key = round(float(w.get('top', 0)) / _Y_TOL) * _Y_TOL
                        rows_by_y.setdefault(y_key, []).append(w)

                    entries2: list[dict] = []
                    for y_key in sorted(rows_by_y):
                        ws = sorted(rows_by_y[y_key], key=lambda w: w['x0'])
                        if len(ws) < 3:
                            continue

                        # Left token may be split: "CM6" + "(*)" → join if next is a marker
                        left_parts = [ws[0]['text'].strip()]
                        rest_start = 1
                        if len(ws) > 1 and ws[1]['text'].strip() in ('(*)', '*', '(*)'):
                            left_parts.append(ws[1]['text'].strip())
                            rest_start = 2

                        left = ''.join(left_parts).replace(' ', '').upper()
                        right = ws[-1]['text'].strip()
                        # Middle words form the colour name
                        middle_ws = ws[rest_start:-1]
                        middle = ' '.join(w['text'] for w in middle_ws).strip()
                        if not middle:
                            middle = ws[rest_start]['text'].strip() if rest_start < len(ws) - 1 else ''

                        e = _parse_row(left, middle, right)
                        if e:
                            entries2.append(e)

                    if len(entries2) >= 3:
                        return entries2

        except Exception:  # noqa: BLE001
            pass

        return []

    @staticmethod
    def _resolve_variants(
        pdf_path: Path,
        col_counter: _Counter,
    ) -> list[str]:
        """Return the ordered list of model variant codes for this PDF.

        Primary source: cover page text pattern ``( B65J )``.
        Fallback: most-common column-header tuple collected during extraction.
        """
        import pdfplumber  # already imported at call site, but safe to repeat

        def _is_vc(s: str) -> bool:
            return (3 <= len(s) <= 5 and s.isalnum()
                    and any(c.isdigit() for c in s)
                    and any(c.isalpha() for c in s))

        best_single: str | None = None  # single variant code found in parens across all pages

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[:8]:
                    text = page.extract_text() or ""

                    # Primary: explicit "( CODE )" per variant — e.g. "( B65J ) ( B65L )"
                    found = [m for m in _COVER_VARIANT_PAT.findall(text) if _is_vc(m)]
                    seen: dict[str, None] = {}
                    for v in found:
                        seen[v] = None
                    if len(seen) >= 2:
                        return list(seen)
                    # Record single parens code from cover pages only (pages 0-1).
                    # Body pages may have figure references like "(1I0)" that are not variants.
                    if len(seen) == 1 and best_single is None and page.page_number <= 2:
                        best_single = next(iter(seen))

                    # Secondary: slash-separated pair — e.g. "B811/B821" or "(B9E1 / B9E2)"
                    # Collect all unique codes that appear as either half of a slash pair.
                    slash_seen: dict[str, None] = {}
                    for m in _SLASH_VARIANT_PAT.finditer(text):
                        a, b = m.group(1), m.group(2)
                        if _is_vc(a) and _is_vc(b) and a != b:
                            slash_seen[a] = None
                            slash_seen[b] = None
                    if len(slash_seen) >= 2:
                        return list(slash_seen)

        except Exception:
            pass

        # Return single variant identified from parens (e.g. "( B627 ) INDIA")
        if best_single:
            return [best_single]

        # Scan filename tokens for first variant code — handles covers without parens
        # (e.g. "FZ S 21C6.pdf" → 21C6, "YBR 110 5TSK.pdf" → 5TSK)
        import re as _re
        for token in _re.split(r"[\s_\-]+", pdf_path.stem):
            if _is_vc(token.upper()):
                return [token.upper()]

        # Fallback: column-header scan results (collected during extraction)
        if col_counter:
            best, _ = col_counter.most_common(1)[0]
            return list(best)

        return []

    def _parse_word_rows(
        self,
        word_rows: list[tuple[float, list[dict]]],
        bounds: ColBounds | None,
        current_section: str,
        current_fig: str,
        num_variants: int = 0,
    ) -> tuple[list[dict], str, str, set[str]]:
        """Parse word-rows into structured dicts.

        Two extraction modes, selected automatically per page:

        Positional mode (when bounds.qty_col_xs is available):
            Every word after the part number is classified by its x-centre into
            description / qty-slot / remarks.  This correctly handles both
            partial-coverage parts (blank variant cells) and description names
            that end in a digit (e.g. "STAY 1") without confusing them with qty.

        Text-based fallback (single-variant or no column bounds):
            Uses _split_after heuristics — same behaviour as before.

        Ref-number carry-forward:
            Yamaha prints the ref number only on the first part of a shared-ref
            group.  The last seen ref is propagated to subsequent blank-ref rows
            within the same FIG section.
        """
        rows: list[dict[str, str]] = []
        sections_seen: set[str] = set()

        # Pre-scan: find the first FIG. header on this page and use it to seed
        # new_section/new_fig before the main loop. This handles pages where data
        # rows appear above the FIG. header in Y-order (e.g. AEROX continuation
        # pages where rows 1-4 sit at a lower Y than the FIG. stamp).
        new_section = current_section
        new_fig = current_fig
        for _pi, (_y, ws) in enumerate(word_rows):
            joined_pre = " ".join(w["text"] for w in ws).strip()
            fig_m = _try_fig_match(joined_pre, word_rows, _pi)
            if fig_m:
                raw = _CID_PAT.sub("", fig_m.group(2)).strip()
                new_section = raw if raw else f"FIG. {fig_m.group(1)}"
                new_fig = f"FIG. {fig_m.group(1)}"
                break

        # last_ref_no carries the most recent non-blank ref number forward.
        # Yamaha PDFs print the ref number only on the FIRST part of a shared-ref
        # group (e.g. B65-F8356-20 and B65-F8356-30 both use ref 20, but only
        # the first row has "20" in the ref cell).
        last_ref_no = ""

        # Index-based loop so we can peek ahead when a row has a part number
        # but an empty description (e.g. BB8-E1392-00 where the PDF renders the
        # description text at a Y coordinate just outside the grouping window).
        idx = 0
        while idx < len(word_rows):
            _y, ws = word_rows[idx]
            idx += 1
            joined = " ".join(w["text"] for w in ws).strip()

            if not joined or len(joined) < 2:
                continue
            if _MODEL_STAMP_PAT.match(joined):
                continue

            # Detect FIG. header (must scan pre-filter joined).
            # _try_fig_match handles three cases:
            #   (a) standard "FIG. N SECTION" on one line
            #   (b) model-stamp prefix before FIG on same line
            #   (c) "FIG. N" alone on this row, section name on the next row
            # For case (c) the helper peeks at word_rows[idx] (already advanced).
            fig_m = _try_fig_match(joined, word_rows, idx - 1)
            if fig_m:
                # Case (c): section name came from word_rows[idx], advance past it
                partial_check = _FIG_PARTIAL_PAT.match(joined)
                if (partial_check
                        and not _FIG_PAT.match(joined)
                        and not _FIG_PAT.match(_STAMP_PREFIX_PAT.sub("", joined))):
                    idx += 1
                raw = _CID_PAT.sub("", fig_m.group(2)).strip()
                new_fig = f"FIG. {fig_m.group(1)}"
                new_section = raw if raw else new_fig  # fallback for CID-corrupt names
                sections_seen.add(new_section)
                last_ref_no = ""  # reset ref carry at every new figure
                continue

            if _SKIP_PAT.match(joined):
                continue

            # Quick check for part number before expensive filtering
            if not _PN_PAT.search(joined):
                continue

            # ── Positional filtering ────────────────────────────────────────
            # Find the part number word's x0 to anchor the column layout.
            # Remove any words that are >50 pts to the left of it — these are
            # figure-illustration numbers printed in the margin (e.g. CRUX PDFs).
            pn_word = next(
                (w for w in ws if _PN_PAT.match(w["text"])), None
            )
            if pn_word:
                x_cutoff = pn_word["x0"] - 50
                ws_filtered = [w for w in ws if w["x0"] >= x_cutoff]
                joined = " ".join(w["text"] for w in ws_filtered).strip()
            else:
                ws_filtered = ws

            pn_m = _PN_PAT.search(joined)
            if not pn_m:
                continue
            pn = pn_m.group(1).replace("–", "-")  # normalize en-dash to hyphen
            if pn.startswith("X") or not new_section:
                continue

            # Everything before PN = ref number text
            before = joined[: pn_m.start()].strip()

            # ── Column classification ───────────────────────────────────────
            # Two modes depending on whether variant column positions are known.
            #
            # POSITIONAL MODE (qty_col_xs available):
            #   Classify every word after the part number by its x-centre:
            #     • x0 ≥ rem_start              → remarks
            #     • x_centre ≥ qty_zone_x AND digit → qty (assigned to nearest col)
            #     • everything else              → description
            #   This is the only reliable way to keep description digits (e.g. the
            #   "1" in "STAY 1") from being confused with qty digits, because their
            #   x-positions place them firmly in the description zone.
            #
            # TEXT-BASED FALLBACK (no column bounds / single-variant):
            #   Use _split_after heuristics (existing behaviour).

            if bounds and bounds.qty_col_xs and pn_word:
                n_cols     = len(bounds.qty_col_xs)
                qty_zone_x = min(bounds.qty_col_xs) - 15.0
                max_qty_cx = max(bounds.qty_col_xs)   # x-centre of rightmost qty col
                slots:         list[str] = [""] * n_cols
                desc_parts:    list[str] = []
                rem_parts:     list[str] = []
                # Words in the gap between the last qty column and the REMARKS
                # column header (e.g. colour codes like "DBNM8", market qualifiers).
                # They are appended AFTER explicit-remarks words so that the reading
                # order is preserved: "FOR YB" (from REMARKS zone) + "DBNM8" (gap).
                pre_rem_parts: list[str] = []
                # Only use the gap-zone when the REMARKS boundary is known;
                # without rem_start we cannot define the gap reliably.
                use_gap_zone = bounds.rem_start < 9000

                for w in ws_filtered:
                    if w["x0"] < pn_word["x1"]:          # ref-no / part-number
                        continue
                    if use_gap_zone and w["x0"] >= bounds.rem_start:
                        rem_parts.append(w["text"])
                        continue
                    w_cx = (w["x0"] + w["x1"]) / 2
                    if w_cx >= qty_zone_x and re.match(r'^\d+$', w["text"]):
                        nearest = min(
                            range(n_cols),
                            key=lambda i: abs(bounds.qty_col_xs[i] - w_cx),
                        )
                        slots[nearest] = w["text"]
                    elif use_gap_zone and w_cx > max_qty_cx:
                        # Non-digit word past the last qty column: colour/market
                        # qualifier that belongs in remarks, not description.
                        pre_rem_parts.append(w["text"])
                    else:
                        desc_parts.append(w["text"])

                # Lookahead: pn and description on different Y-rows.
                # Only needed when the current row has no description AND no
                # gap-zone qualifiers (i.e. effectively empty after the pn).
                if not desc_parts and not pre_rem_parts and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (next_joined
                            and not _PN_PAT.search(next_joined)
                            and not _FIG_PAT.match(next_joined)
                            and not _SKIP_PAT.match(next_joined)):
                        desc_parts = [_COPYRIGHT_PAT.sub("", next_joined).strip()]
                        idx += 1

                desc = " ".join(desc_parts)
                qty  = "/".join(slots)
                filled = [s for s in slots if s]
                # Collapse "1/1/1/1" → "1" only when EVERY slot is filled identically
                if filled and len(set(filled)) == 1 and len(filled) == n_cols:
                    qty = filled[0]
                # Combine gap-zone words with explicit-remarks words.
                # Gap-zone words (pre_rem_parts) appear to the LEFT of the
                # REMARKS column in the PDF, so they always come first.
                # e.g. gap=["DBNM8"] rem=["FOR","YB"] → "DBNM8 FOR YB"
                # e.g. gap=["FOR"]   rem=["SM12"]     → "FOR SM12"
                remarks = " ".join(pre_rem_parts + rem_parts)

            else:
                # ── Text-based fallback ─────────────────────────────────────
                after = _COPYRIGHT_PAT.sub("", joined[pn_m.end():]).strip()

                # Strip remarks-zone words and save them
                positional_remarks = ""
                if bounds and bounds.rem_start < 9000:
                    rem_words = [w["text"] for w in ws_filtered if w["x0"] >= bounds.rem_start]
                    if rem_words:
                        rem_suffix = " ".join(rem_words)
                        if after.endswith(rem_suffix):
                            after = after[: -len(rem_suffix)].strip()
                            positional_remarks = rem_suffix

                # Lookahead: pn and description on different Y-rows
                if not after and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (next_joined
                            and not _PN_PAT.search(next_joined)
                            and not _FIG_PAT.match(next_joined)
                            and not _SKIP_PAT.match(next_joined)):
                        after = _COPYRIGHT_PAT.sub("", next_joined).strip()
                        idx += 1

                desc, qty, remarks = self._split_after(after, num_variants)
                if not remarks and positional_remarks:
                    remarks = positional_remarks

            # ── Ref number ─────────────────────────────────────────────────
            if re.match(r'^[\d\s]+$', before):
                last_int = re.search(r'(\d{1,3})\s*$', before)
                ref_no = last_int.group(1) if last_int else before.strip()
            else:
                ref_no = before

            # Carry forward: when the PDF leaves ref blank for the 2nd+ part
            # in a shared-ref group, reuse the last seen ref number.
            if ref_no:
                last_ref_no = ref_no
            elif last_ref_no:
                ref_no = last_ref_no

            rows.append({
                "section":     new_section,
                "fig_no":      new_fig,
                "ref_no":      ref_no,
                "part_no":     pn,
                "description": desc.strip(),
                "qty":         qty,
                "remarks":     remarks,
            })

        return rows, new_section, new_fig, sections_seen

    # ------------------------------------------------------------------
    # Text-line fallback (no positional data)
    # ------------------------------------------------------------------

    def _text_fallback(
        self, pdf_path: Path
    ) -> tuple[list[dict[str, str]], set[str]]:
        """Text-line regex parser used when positional extraction yields nothing."""
        import pdfplumber

        rows: list[dict[str, str]] = []
        sections_seen: set[str] = set()
        current_section = ""
        current_fig = ""

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[: self.max_pages]:
                    text = page.extract_text() or ""

                    # Skip KITS pages and cross-reference index pages
                    page_head = text[:400]
                    if _KITS_PAGE_PAT.search(page_head) or _XREF_PAGE_PAT.search(page_head):
                        continue

                    for line in text.split("\n"):
                        line = line.strip()
                        if not line or len(line) < 3:
                            continue
                        if _MODEL_STAMP_PAT.match(line):
                            continue
                        if _SKIP_PAT.match(line):
                            continue

                        fig_m = _FIG_PAT.match(line)
                        if fig_m:
                            current_fig = f"FIG. {fig_m.group(1)}"
                            current_section = _CID_PAT.sub("", fig_m.group(2)).strip()
                            sections_seen.add(current_section)
                            continue

                        pn_m = _PN_PAT.search(line)
                        if not pn_m:
                            continue
                        pn = pn_m.group(1).replace("–", "-")  # normalize en-dash
                        if pn.startswith("X") or not current_section:
                            continue

                        before = line[: pn_m.start()].strip()
                        after = _COPYRIGHT_PAT.sub("", line[pn_m.end():]).strip()

                        ref_m = re.match(r'^(\d{1,3})\s*$', before)
                        ref_no = ref_m.group(1) if ref_m else before
                        desc, qty, remarks = self._split_after(after, num_variants=0)

                        rows.append({
                            "section":     current_section,
                            "fig_no":      current_fig,
                            "ref_no":      ref_no,
                            "part_no":     pn,
                            "description": desc,
                            "qty":         qty,
                            "remarks":     remarks,
                        })
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Text fallback failed for {pdf_path}: {exc}")

        return rows, sections_seen

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_after(after: str, num_variants: int = 0) -> tuple[str, str, str]:
        """Split 'DESCRIPTION tokens qty... [remarks]' into (desc, qty, remarks).

        Algorithm (right-to-left):
        1. If last token is non-numeric → it is remarks; pop it.
        2. Pop qty digit tokens from the right:
           - If num_variants > 0: pop exactly num_variants digits. This prevents
             trailing description digits (e.g. "GUIDE, VALVE 1") from being
             consumed as a qty column when all variant qtys are the same value.
           - If num_variants == 0 (unknown): greedy — pop all trailing digits.
        3. Collapse identical multi-values: "1/1/1/1" → "1".
        4. Everything remaining is the description.
        """
        tokens = list(after.split())
        if not tokens:
            return "", "", ""

        # Step 1: extract optional remarks (last non-digit token)
        remarks = ""
        if not re.match(r'^\d+$', tokens[-1]):
            remarks = tokens.pop()

        if not tokens:
            return "", "", remarks

        # Step 2: collect qty tokens
        qty_vals: list[str] = []
        if num_variants > 0:
            # Known variant count — pop at most num_variants digits from the right
            count = 0
            while tokens and count < num_variants and re.match(r'^\d+$', tokens[-1]):
                qty_vals.insert(0, tokens.pop())
                count += 1
        else:
            # Unknown — greedy: pop all trailing digit tokens
            while tokens and re.match(r'^\d+$', tokens[-1]):
                qty_vals.insert(0, tokens.pop())

        qty = "/".join(qty_vals) if qty_vals else ""
        # Collapse "1/1/1/1" → "1" when all variant qtys are identical
        if qty and len(set(qty_vals)) == 1:
            qty = qty_vals[0]
        return " ".join(tokens), qty, remarks
