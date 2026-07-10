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
from pathlib import Path  # noqa: TCH003
from typing import Any

import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLUMNS: list[str] = [
    "section",
    "fig_no",
    "ref_no",
    "part_no",
    "description",
    "qty",
    "nine_digit_part_no",
    "escort_part_no",
    "superseded_part_no",
    "remarks",
]
DISPLAY_HEADERS: list[str] = [
    "Section",
    "Ref. No.",
    "Part No.",
    "Description",
    "Q'ty",
    "9 Digit Part No.",
    "Escort Part No.",
    "Superseded Part No.",
    "Remarks",
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
#   10–12-char nodash: XXXXXXXXXX    e.g. 2LPWE11100 (Saluto 2LP2), 5DGE35860100 (ENTICER/YBX125)
_DASH = r"[-–]"
_PN_PAT = re.compile(
    r"\b("
    # 3- or 4-segment: XXX-XXXXX-NN[-XX]
    # Third segment is [A-Z0-9]{2} (not [0-9]{2}) because Yamaha graphic/decal parts
    # use letter-prefixed revision codes: B65-F174G-C0, B65-F174H-D0, etc.
    r"[A-Z0-9]{2,5}"
    + _DASH
    + r"[A-Z0-9]{3,8}"
    + _DASH
    + r"[A-Z0-9]{2}(?:"
    + _DASH
    + r"[A-Z0-9]{2})?"
    # 2-segment with 5-char alphanumeric first segment, 3-8 char alphanumeric second
    # covers: NNNNN-NNNNN, NNNNN-NNNYX, XNNNN-NNNNN, etc.
    r"|[A-Z0-9]{5}" + _DASH + r"[A-Z0-9]{3,8}"
    r"|(?=[A-Z0-9]*\d)[A-Z0-9]{10,12}"
    r")\b"
)
# FIG. section header — "FIG. 1 (1D0) CYLINDER HEAD" or "FIG.1 CYLINDER" (no space after dot)
_FIG_PAT = re.compile(r"^FIG\.\s*(\d+[A-Z]?)\s*(?:\([^)]+\)\s*)?(.+)$", re.IGNORECASE)
# Lines to skip (header labels, noise)
_SKIP_PAT = re.compile(
    r"^(REF\.?|PART\s+NO\.?|PART\s+NAME|DESCRIPTION|REMARKS|NO\.?|"
    r"CONTENTS?|Q\'?TY\.?|12\s+DIGITS?|9\s+DIGITS?)$",
    re.IGNORECASE,
)
# Model-code stamps at top of page, e.g. "1BV2", "21C1", "5AK5"
_MODEL_STAMP_PAT = re.compile(r"^[A-Z0-9]{2,5}\d[A-Z]?$")
# Copyright line injected by some PDFs
_COPYRIGHT_PAT = re.compile(r"\s*No part of this Publication.*", re.IGNORECASE)
# Page-type skips — these page formats are not FIG parts tables
# KITS pages: "KITS-5YY6", "KITS 5YY6" — kit assembly lists
_KITS_PAGE_PAT = re.compile(r"\bKITS[-\s]\S", re.IGNORECASE)
# Cross-reference index pages: "PART NO." appears 3+ times, NOT necessarily consecutive —
# the numerical index layout is "PART NO. | REF. NO. | PART NO. | REF. NO. | …" so the
# old consecutive pattern never matched (REF. NO. broke the run).
_XREF_PAGE_PAT = re.compile(
    r"PART\s+NO.{0,60}PART\s+NO.{0,60}PART\s+NO",
    re.IGNORECASE | re.DOTALL,
)
# Index-page title guard: catches "NUMERICAL INDEX", "ALPHABETICAL INDEX", etc.
_INDEX_PAGE_PAT = re.compile(
    r"(?:NUMERICAL|ALPHABETICAL|COLOUR|GENERAL)\s+INDEX",
    re.IGNORECASE,
)
# Foreword / intro pages: always front matter, never genuine parts pages.
# These pages may contain a mini "Applicable Serial No. and Color Code" parts table
# in a side column that leaks into extraction if not skipped.
_FOREWORD_PAGE_PAT = re.compile(r"\bFOREWORD\b", re.IGNORECASE)
# Bilingual (Spanish/English) catalogues use "DESCRIPCION" (Spanish, no T) as the
# first header row for the description column. English "DESCRIPTION" follows below.
_BILINGUAL_HDR_PAT = re.compile(r"\bDESCRIPCION\b", re.IGNORECASE)
# CID glyph fallback artifact from pdfplumber font decoding (e.g. "(cid:2)")
_CID_PAT = re.compile(r"\s*\(cid:\d+\)", re.IGNORECASE)
# Trailing colour-variant annotation on description text, e.g. " -YB(black)", " -DBNM8(gray)".
# Yamaha bodywork catalogues append a paint-code abbreviation + lowercase colour name in parens.
# Lowercase inside parens distinguishes colour names from structural qualifiers like "(STD)".
_COLOUR_SUFFIX_PAT = re.compile(r"\s*[-.]?\s*[A-Z][A-Z0-9]{0,7}\([a-z][^)]*\)\s*$")

# Cover-page variant code pattern: Yamaha writes each variant as "( B65J )"
# on the title page listing all models covered by the catalogue.
_COVER_VARIANT_PAT = re.compile(r"\(\s*([A-Z0-9]{3,5})\s*\)")
# Slash-separated pair pattern for catalogues that list two variants as
# "B811/B821" or "(B9E1 / B9E2)" without separate paren wrapping.
_SLASH_VARIANT_PAT = re.compile(r"\b([A-Z0-9]{3,5})\s*/\s*([A-Z0-9]{3,5})\b")

# Leading model/variant stamp that may precede "FIG." on the same pdfplumber
# word-row, e.g. "B65J FIG. 14 FRAME" or "21C1 FIG. 1 CYLINDER HEAD".
_STAMP_PREFIX_PAT = re.compile(r"^[A-Z0-9]{2,6}\s+", re.IGNORECASE)

# Partial FIG line — just the number, section name absent or on the next row.
_FIG_PARTIAL_PAT = re.compile(r"(?:^|.*\s)(FIG\.?\s*(\d+[A-Z]?))\s*$", re.IGNORECASE)

# Option-section page header: "PARTS OPTION (KICK STARTER)" — no FIG. prefix.
# Captures the label inside the parentheses as the section name.
_PARTS_OPTION_PAT = re.compile(r"^PARTS?\s+OPTION\s*\(([^)]+)\)", re.IGNORECASE)

# "AVAILABLE COLOUR" page header — these pages list bike colour variants as image captions,
# e.g. "Yamaha Alpha Cygnus Black" / "Yamaha Alpha Cygnus Cyan".
_AVAIL_COLOUR_PAT = re.compile(r"AVAILABLE\s+COLOU?RS?", re.IGNORECASE)
# Colour nouns used to validate caption lines on available-colour pages.
_CAPTION_COLOUR_NOUNS: frozenset[str] = frozenset(
    {
        "black",
        "white",
        "blue",
        "red",
        "green",
        "yellow",
        "silver",
        "gray",
        "grey",
        "orange",
        "gold",
        "cyan",
        "brown",
        "purple",
        "violet",
        "pink",
        "cream",
        "champagne",
        "magenta",
        "maroon",
        "metallic",
        "matte",
        "matt",
        "mat",
        "vivid",
        "racing",
        "bright",
        "dull",
        "bluish",
        "purplish",
        "reddish",
        "greenish",
        "cocktail",
        "rally",
        "navy",
        "cobalt",
        "candy",
        "sparkle",
        "pearl",
        # "dark", "deep", "light" intentionally omitted — too generic;
        # colours that use them (Dark Grey, Deep Blue) also have a specific colour noun.
    }
)
# Subset of colour nouns valid as standalone single-word colour names.
# Modifiers ("metallic", "mat", "vivid", ...) must be accompanied by a named colour.
_STANDALONE_COLOUR_NOUNS: frozenset[str] = frozenset(
    {
        "black",
        "white",
        "blue",
        "red",
        "green",
        "yellow",
        "silver",
        "gray",
        "grey",
        "orange",
        "gold",
        "cyan",
        "brown",
        "purple",
        "violet",
        "pink",
        "cream",
        "champagne",
        "magenta",
        "maroon",
        "cobalt",
        "navy",
    }
)
# Variant-code prefix that some catalogues use in colour captions:
# "2SP3-Gold", "BP16-Cyan", "BP16-Black Metallic" etc.
_VARIANT_CODE_PREFIX_PAT = re.compile(r"^([A-Z0-9]{2,6})-(.*)", re.IGNORECASE)


def _bilingual_fig_section(
    ws: list[dict],
    fallback: str,
    bounds: ColBounds,
) -> str:
    """Return the English-only section name from a bilingual FIG header row.

    In Spanish/English catalogues the FIG line is "FIG. N SpanishName EnglishName".
    The English words always start at x ≈ desc_start + 79 (consistently x ≥ 613
    for the SZ series), while Spanish section words cluster at x ≤ desc_start + 36.
    A fixed offset of +70 from desc_start gives a reliable split threshold that
    keeps all English words and discards all Spanish words.
    """
    thresh = (bounds.desc_start + 70.0) if bounds.desc_start < 9000 else 460.0
    eng_ws = sorted([w for w in ws if w["x0"] >= thresh], key=lambda w: w["x0"])
    if not eng_ws:
        return fallback
    return _CID_PAT.sub("", " ".join(w["text"] for w in eng_ws)).strip() or fallback


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
    """X-coordinate boundaries for ALL columns of a Yamaha parts page.

    The extractor first identifies columns from the header row, then uses
    these positions to classify each word into the correct column.

    pn_start          — x-start of the PART NO. (or PART NAME) column.
    desc_start        — x-start of the DESCRIPTION column.
    qty_start         — x-start of the Q'TY column (single-variant PDFs only;
                        multi-variant PDFs use qty_col_xs instead).
    rem_start         — x-start of the REMARKS column.
    qty_col_xs        — x-centre of each variant Q'ty column, left-to-right.
                        Empty for single-variant PDFs.
    nine_digit_start  — x-start of the "9 DIGIT PART NO." column present in
                        some India-market PDFs (e.g. YBX125).  9999.0 when absent.
    superseded_start  — x-start of the "SUPERSEDED PART NO." column.  9999.0 when absent.
    header_detected   — True when a column-header row was found and all
                        positions above come from it (more reliable).
    """

    pn_start: float = 9999.0
    desc_start: float = 9999.0
    qty_start: float = 9999.0
    rem_start: float = 9999.0
    qty_col_xs: list[float] = field(default_factory=list)
    nine_digit_start: float = 9999.0
    escort_start: float = 9999.0  # "ESCORT 12 DIGIT PART NO." column (ENTICER-family)
    superseded_start: float = 9999.0
    header_detected: bool = False
    # True when "REMARKS (9 DIGIT)" header means the REMARKS column IS the 9-digit part column.
    # In this layout nine_digit_start is cleared (9999) so content routes via rem_start → remarks.
    remarks_is_nine_digit: bool = False
    # True when the catalogue is bilingual (Spanish first, English second).
    # Each data row's Spanish description is immediately followed by an English
    # description on the next Y row; extraction should take the English row.
    is_bilingual: bool = False
    # Maps logical column name → the PDF's actual header label for that column.
    # e.g. {"part_no": "Existing Part No.", "description": "Part Name"}
    # Only populated for columns whose PDF label differs from the default.
    col_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """Output of extracting one PDF file."""

    pdf_path: Path
    model: str
    rows: list[dict[str, str]]
    pages_scanned: int
    sections_found: int
    ocr_flagged: int  # pages that needed OCR but couldn't be handled
    variants: list[str] = field(default_factory=list)  # e.g. ["B65J","B65L","B65M","B65N"]
    # colour_codes: [{abbreviation, name, code, is_model_colour}] from the PDF's colour table
    colour_codes: list[dict] = field(default_factory=list)
    # available_colours: captions from "AVAILABLE COLOUR" page, e.g.
    # ["Yamaha Alpha Cygnus Black", "Yamaha Alpha Cygnus Cyan"]
    available_colours: list[str] = field(default_factory=list)
    # available_colour_map: populated by CatalogueAgent after web/PDF colour matching;
    # empty when result comes directly from the extractor without agent post-processing.
    available_colour_map: dict[str, str] = field(default_factory=dict)
    manufacture_year: str | None = None  # e.g. "2019" from ©2019 on the cover page
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    # column_layout: detected column names in left-to-right order for display
    column_layout: list[str] = field(default_factory=list)
    # column_display_labels: maps logical field name → PDF's actual column header text.
    # Only populated for columns whose PDF label differs from the default DISPLAY_HEADERS.
    # e.g. {"part_no": "Existing Part No.", "description": "Part Name",
    #        "escort_part_no": "Escort 12 Digit Part No."}
    column_display_labels: dict[str, str] = field(default_factory=dict)

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
        result = ex.extract(
            Path("data/raw/pdf_catalogues/FZ & FZS/FZ16 21C1.pdf"), model="FZ & FZS"
        )
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
                        x_tolerance=4,
                        y_tolerance=4,
                        keep_blank_chars=False,
                        use_text_flow=False,
                    )

                    if not words:
                        ocr_flagged += 1
                        continue

                    word_rows = self._group_by_y(words)

                    # Skip KITS pages, cross-reference index pages, and named index pages.
                    # Guard _XREF_PAGE_PAT with a FIG-header check: some catalogues
                    # (e.g. ENTICER 5US1) have "PART NO. … PART NO. … PART NO." in
                    # their column-header row, which is NOT an index page.  Genuine
                    # cross-reference index pages never contain "FIG." section markers.
                    page_header = " ".join(
                        " ".join(w["text"] for w in ws) for _, ws in word_rows[:8]
                    )
                    _has_fig = bool(_FIG_PAT.search(page_header))
                    if (
                        _KITS_PAGE_PAT.search(page_header)
                        or (_XREF_PAGE_PAT.search(page_header) and not _has_fig)
                        or _INDEX_PAGE_PAT.search(page_header)
                        or _FOREWORD_PAGE_PAT.search(page_header)
                    ):
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
                            # Propagate remarks_is_nine_digit: once a page establishes
                            # that REMARKS == 9-digit column, all subsequent pages must
                            # respect that layout even if their partial header row only
                            # has the REMARKS keyword (not the "(9 DIGIT)" qualifier).
                            if not new_bounds.remarks_is_nine_digit:
                                new_bounds.remarks_is_nine_digit = last_bounds.remarks_is_nine_digit
                            if new_bounds.rem_start >= 9000:
                                new_bounds.rem_start = last_bounds.rem_start
                            # Only inherit qty_col_xs when this page has no column layout
                            # of its own.  A detected desc_start (<9000) means the page
                            # is a real data page; don't let a spurious qty_col_xs from a
                            # foreword page (e.g. "21C 1CK" example text passing
                            # _is_variant_code) corrupt the single-variant positional mode.
                            if not new_bounds.qty_col_xs and new_bounds.desc_start >= 9000:
                                new_bounds.qty_col_xs = last_bounds.qty_col_xs
                            if new_bounds.nine_digit_start >= 9000:
                                new_bounds.nine_digit_start = last_bounds.nine_digit_start
                            if new_bounds.escort_start >= 9000:
                                new_bounds.escort_start = last_bounds.escort_start
                            if new_bounds.superseded_start >= 9000:
                                new_bounds.superseded_start = last_bounds.superseded_start
                            if not new_bounds.col_labels:
                                new_bounds.col_labels = last_bounds.col_labels
                            if not new_bounds.is_bilingual:
                                new_bounds.is_bilingual = last_bounds.is_bilingual
                        bounds = new_bounds

                    page_rows, new_section, new_fig, page_sects = self._parse_word_rows(
                        word_rows, bounds, current_section, current_fig, num_variants
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
                pdf_path=pdf_path,
                model=model,
                rows=rows,
                pages_scanned=pages_scanned,
                sections_found=len(sections_seen),
                ocr_flagged=ocr_flagged,
                warnings=warnings,
                error=str(exc),
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

        # Lift colour names from description parentheses (older-format PDFs that
        # encode variants as 'DESCRIPTION (COLOUR NAME)' instead of a foreword
        # colour table + FOR/EXCEPT remarks).  Only activates when colour_codes
        # is empty — never overwrites a proper foreword-extracted colour table.
        if not colour_codes:
            rows, colour_codes = self._lift_desc_colours(rows)
            if colour_codes:
                logger.info(
                    f"{pdf_path.name}: lifted {len(colour_codes)} desc-colour(s): "
                    f"{[c['abbreviation'] for c in colour_codes]}"
                )

        # Extract available colour captions from the "AVAILABLE COLOUR" page
        available_colours = self._extract_available_colours(pdf_path)
        if available_colours:
            logger.info(
                f"{pdf_path.name}: found {len(available_colours)} available colour(s) from page"
            )

        # Extract manufacture year from cover page (©YYYY / "1st edition, May YYYY")
        manufacture_year = self._extract_manufacture_year(pdf_path)
        if manufacture_year:
            logger.info(f"{pdf_path.name}: manufacture year {manufacture_year}")

        # Build column_layout: ordered list of detected column names (left → right)
        # for display in the catalogue viewer.
        column_layout: list[str] = []
        if last_bounds is not None:
            b = last_bounds
            # Collect (x_start, display_name) pairs for every detected column
            _detected: list[tuple[float, str]] = [
                (0.0, "Ref. No."),  # ref is always present, starts at 0
                (b.pn_start if b.pn_start < 9000 else 50.0, "Part No."),
                (b.desc_start if b.desc_start < 9000 else 150.0, "Description"),
            ]
            if b.qty_col_xs:
                _detected.append((min(b.qty_col_xs), "Q'ty (per variant)"))
            elif b.qty_start < 9000:
                _detected.append((b.qty_start, "Q'ty"))
            if b.nine_digit_start < 9000:
                _detected.append((b.nine_digit_start, "9 Digit Part No."))
            if b.escort_start < 9000:
                escort_label = b.col_labels.get("escort_part_no", "Escort Part No.")
                _detected.append((b.escort_start, escort_label))
            if b.superseded_start < 9000:
                _detected.append((b.superseded_start, "Superseded Part No."))
            if b.rem_start < 9000:
                label = "Remarks (9 Digit)" if b.remarks_is_nine_digit else "Remarks"
                _detected.append((b.rem_start, label))

            # Override display names with PDF-native labels for part_no / description
            _label_overrides = b.col_labels
            _display_map: dict[str, str] = {}
            for _x, _name in _detected:
                _display_map[_x] = _name
            if "part_no" in _label_overrides:
                for i, (_x, _n) in enumerate(_detected):
                    if _n == "Part No.":
                        _detected[i] = (_x, _label_overrides["part_no"])
                        break
            if "description" in _label_overrides:
                for i, (_x, _n) in enumerate(_detected):
                    if _n == "Description":
                        _detected[i] = (_x, _label_overrides["description"])
                        break

            column_layout = [name for _, name in sorted(_detected, key=lambda t: t[0])]
            column_display_labels = dict(b.col_labels)
        else:
            column_display_labels = {}

        return ExtractionResult(
            pdf_path=pdf_path,
            model=model,
            rows=rows,
            pages_scanned=pages_scanned,
            sections_found=len(sections_seen),
            ocr_flagged=ocr_flagged,
            variants=variants,
            colour_codes=colour_codes,
            available_colours=available_colours,
            manufacture_year=manufacture_year,
            warnings=warnings,
            column_layout=column_layout,
            column_display_labels=column_display_labels,
        )

    def extract_all(
        self,
        pdf_root: Path,
        save_path: Path | None = None,
        max_workers: int = 4,
    ) -> pd.DataFrame:
        """Batch-extract every PDF under pdf_root and return a merged DataFrame.

        Uses parallel extraction (ThreadPoolExecutor) to process multiple PDFs
        concurrently, improving throughput for large batches.

        Args:
            pdf_root:    Root folder containing model sub-directories.
            save_path:   Optional path to write the result as Parquet.
            max_workers: Maximum concurrent workers for parallel extraction (default 4).

        Returns:
            DataFrame with COLUMNS + ["model", "source_file"] columns.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_dfs: list[pd.DataFrame] = []

        # Use case-insensitive .pdf suffix to avoid double-counting on Windows
        pdf_files = sorted(
            {f for f in pdf_root.rglob("*") if f.suffix.lower() == ".pdf"},
            key=lambda p: (p.parent.name.upper(), p.name.upper()),
        )

        if not pdf_files:
            empty = pd.DataFrame(columns=COLUMNS + ["model", "source_file", "ocr_used"])
            if save_path:
                empty.to_parquet(save_path, index=False)
            return empty

        logger.info(f"extract_all: {len(pdf_files)} PDFs, {max_workers} workers")

        def extract_one(pdf_file: Path) -> tuple[Path, str, pd.DataFrame | None]:
            """Extract one PDF, returning (path, model_name, dataframe or None)."""
            model_name = pdf_file.parent.name
            try:
                logger.info(f"Extracting [{model_name}] {pdf_file.name}")
                result = self.extract(pdf_file, model=model_name)
                logger.debug(result.summary())

                if result.error:
                    logger.error(f"  → skipped: {result.error}")
                    return (pdf_file, model_name, None)

                if not result.rows:
                    logger.warning("  → no rows extracted")
                    return (pdf_file, model_name, None)

                df = result.df.copy()
                df["model"] = model_name
                df["source_file"] = pdf_file.name
                df["ocr_used"] = False
                return (pdf_file, model_name, df)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Extract failed for {pdf_file}: {exc}")
                return (pdf_file, model_name, None)

        # Parallel extraction using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(extract_one, pf): pf for pf in pdf_files}
            for completed, future in enumerate(as_completed(futures), start=1):
                try:
                    pdf_file, model_name, df = future.result()
                    if df is not None:
                        all_dfs.append(df)
                    logger.debug(f"Completed {completed}/{len(pdf_files)}: {pdf_file.name}")
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Future failed: {exc}")

        if not all_dfs:
            empty = pd.DataFrame(columns=COLUMNS + ["model", "source_file", "ocr_used"])
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

        if not all_dfs:
            empty = pd.DataFrame(columns=COLUMNS + ["model", "source_file", "ocr_used"])
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
        """Detect all column boundaries from the page header rows.

        Pass 1 — explicit header identification (_detect_header_row):
          Scans for the header row (containing ≥3 column keywords such as PART,
          DESCRIPTION, REMARKS, QTY) and records the x-start of every column
          found there.  This gives us pn_start, desc_start, and qty_start which
          the old code never detected.

        Pass 2 — specialised detection (existing logic, unchanged):
          - "REMARKS" keyword  → rem_start (rightmost column anchor)
          - Variant-code tokens on header rows → qty_col_xs (multi-variant)
          - "9 DIGIT PART NO." / "SUPERSEDED PART NO." → extra column starts

        Pass-2 results take precedence over Pass-1 for the columns they cover
        (rem_start, nine_digit_start, superseded_start) because the existing
        detection is tuned for split-row headers and CRUX-style double-headers.
        """
        # ── Pass 1: full header row scan ──────────────────────────────────────
        header = YamahaCatalogueExtractor._detect_header_row(word_rows)
        # Unpack col_labels injected under the "_labels" sentinel key.
        col_labels: dict[str, str] = header.pop("_labels", {})  # type: ignore[arg-type]
        pn_start = header.get("part_no", 9999.0)
        desc_start = header.get("description", 9999.0)
        qty_start = header.get("qty", 9999.0)
        escort_start = header.get("escort", 9999.0)
        # Seed from header; Pass 2 may refine these three below.
        rem_start = header.get("remarks", 9999.0)
        nine_digit_start = header.get("nine_digit", 9999.0)
        superseded_start = header.get("superseded", 9999.0)

        # ── Pass 2: specialised detection (original logic) ────────────────────
        raw_vc_xs: list[float] = []  # accumulate variant-code x-centres

        for _y, ws in word_rows[:25]:
            texts = [w["text"].upper() for w in ws]
            joined_up = " ".join(texts)

            # REMARKS anchor
            rem_idx = next((i for i, t in enumerate(texts) if t == "REMARKS"), None)
            if rem_idx is not None:
                rem_start = ws[rem_idx]["x0"]

            # CRUX-style "12 DIGIT" + "9 DIGIT" double-header — both show "DIGIT".
            # Strip parentheses so "(9 DIGIT)" also counts (India-market catalogues).
            # NOTE: do NOT set rem_start here.  CRUX has no REMARKS column; the 9 DIGIT
            # column is correctly detected via the flat-scan nine_digit_start path below.
            # Setting rem_start from the "9" token caused remarks_is_nine_digit=True, which
            # cleared nine_digit_start and routed 9-digit values into the remarks field.
            # Skip data rows (contain part numbers) — only collect codes from
            # column-header rows.  Remark qualifiers (DBNM8, MBL2, etc.) appear
            # on data rows and would otherwise pollute qty_col_xs.
            if _PN_PAT.search(joined_up):
                continue

            # Accumulate variant-code column headers from every header row.
            # Require ≥2 variant codes on the same row: a model stamp (e.g. "1SU5")
            # satisfies _is_variant_code on its own and would create a spurious
            # single-entry qty_col_xs, forcing multi-variant positional mode
            # with the wrong column positions (root cause for ENTICER 5US1).
            row_vcs = [
                w for w in ws if YamahaCatalogueExtractor._is_variant_code(w["text"].upper())
            ]
            if len(row_vcs) >= 2:
                # Deduplicate by code text within a single row.  Some PDFs (e.g.
                # FASINO foreword) print the same variant code in BOTH a "Q'ty
                # Column" cell and a "Model Code" cell on the same row, giving two
                # x-positions for one variant.  Keep only the leftmost occurrence
                # so we don't create a phantom second qty-column slot.
                seen_in_row: set[str] = set()
                for w in row_vcs:
                    code = w["text"].upper()
                    if code not in seen_in_row:
                        seen_in_row.add(code)
                        raw_vc_xs.append((w["x0"] + w["x1"]) / 2)

        # Sort and deduplicate near-identical x-centres (within 8 px)
        qty_col_xs: list[float] = []
        for cx in sorted(raw_vc_xs):
            if not qty_col_xs or cx - qty_col_xs[-1] > 8.0:
                qty_col_xs.append(cx)

        # ── India-market extra column detection ───────────────────────────────
        # "9 DIGIT PART NO." and "SUPERSEDED PART NO." headers may be split
        # across different Y-rows (pdfplumber groups words by Y-band, so "9" at
        # y=40 and "DIGIT" at y=44 land in separate word-rows).  A flat x-proximity
        # scan across header-only rows handles this reliably: we pair each "DIGIT"
        # token with any "9" token whose x0 is within 50 pts to its left.
        #
        # IMPORTANT: exclude data rows.  A part-list row with ref_no=9 (e.g.
        # "9@441") would pair with the "DIGIT@470" from the "12 DIGIT" column
        # header (gap=29 px < 50 threshold), falsely setting nine_digit_start to
        # the ref-no column (~441 px) and routing ALL content into nine_digit.
        flat_words: list[dict] = []
        for _, ws in word_rows[:25]:
            if not _PN_PAT.search(" ".join(w["text"] for w in ws)):
                flat_words.extend(ws)
        # Strip parentheses so "(9" matches "9" and "DIGIT)" matches "DIGIT".
        # Handles "REMARKS (9 DIGIT)" column headers in India-market catalogues.
        digit_x_list = [
            w["x0"] for w in flat_words if w["text"].upper().strip("()") in ("DIGIT", "DIGITS")
        ]
        nine_x_list = [w["x0"] for w in flat_words if w["text"].strip("()") == "9"]
        sup_words = [w for w in flat_words if w["text"].upper() == "SUPERSEDED"]

        for dig_x in digit_x_list:
            near_nines = [x for x in nine_x_list if x < dig_x and dig_x - x < 50]
            if near_nines and nine_digit_start >= 9000:
                nine_digit_start = max(near_nines)  # nearest "9" left of "DIGIT"
                break

        if sup_words and superseded_start >= 9000:
            superseded_start = min(w["x0"] for w in sup_words)

        # Right-aligned column data sits to the left of the header word's left edge.
        # Shift nine_digit_start inward so data values that start just left of the
        # detected header x-position are routed correctly.
        # Observed offsets: 6 px in ENTICER 5US1, ~9 px in CRUX-S PC5KA5.
        # Use 15 px to cover both catalogues with margin.
        if nine_digit_start < 9000:
            nine_digit_start = max(0.0, nine_digit_start - 15.0)

        # Detect "REMARKS (9 DIGIT)" layout: rem_start and nine_digit_start land at
        # the same physical column (within 40 pts).  In this layout the REMARKS
        # column IS the 9-digit part number column — content routes to rem_start
        # (the remarks field) so nine_digit_start is cleared to avoid double-routing.
        remarks_is_nine_digit = False
        if nine_digit_start < 9000 and rem_start < 9000 and abs(nine_digit_start - rem_start) < 40:
            remarks_is_nine_digit = True
            # Use the leftmost anchor of the merged "REMARKS (9 DIGIT)" column header
            # so data values aligned with either token are captured.
            rem_start = min(rem_start, nine_digit_start)
            nine_digit_start = 9999.0  # content routes via rem_start → remarks field

        # ── India-market single-variant qty column (e.g. GLADIATOR 5 SPEED) ──
        # India-market PDFs (remarks_is_nine_digit=True) have one variant-code
        # column header (e.g. "5YY8") between description and REMARKS columns.
        # The ≥2-variant guard skips single-code rows, leaving qty_col_xs empty.
        # Without a qty zone, _split_after in greedy mode pops description-suffix
        # digits ("COVER, CYLINDER HEAD SIDE 3 1" → qty="3/1") instead of the
        # actual qty "1".  Re-scan header rows for a single variant code in the
        # desc–rem gap; enabling multi-variant positional mode then lets the
        # existing footnote-marker guard route the trailing "3" back to desc.
        if remarks_is_nine_digit and not qty_col_xs and desc_start < 9000 and rem_start < 9000:
            for _, _ws_india in word_rows[:25]:
                if _PN_PAT.search(" ".join(w["text"] for w in _ws_india)):
                    continue  # data row — skip
                _india_vcs = [
                    w
                    for w in _ws_india
                    if YamahaCatalogueExtractor._is_variant_code(w["text"].upper())
                    and w["x0"] > desc_start  # must be past description column
                    and w["x0"] < rem_start  # must be before REMARKS column
                ]
                if _india_vcs:
                    qty_col_xs = [(w["x0"] + w["x1"]) / 2 for w in _india_vcs]
                    break

        # ── Single-variant-code qty_start detection (CRUX-style and 45SB-style) ──
        # When a PDF has exactly ONE variant-code token as a column header (e.g.
        # "IAK5" for CRUX, "BS54" for 45SB Final), that token sits on a row above
        # the main anchor row so _detect_header_row misses it.  Scan the first 25
        # word-rows here to find it and record its x-position as qty_start.
        # Guard: skip India-market layouts (remarks_is_nine_digit=True) because
        # their nine_digit_start was intentionally cleared to 9999 and a leftover
        # variant-like token could create a spurious qty_start.
        # Upper bound: use the smaller of nine_digit_start and rem_start so we
        # don't accidentally absorb a 9-digit or remarks column as the qty column.
        if not qty_col_xs and qty_start >= 9000 and desc_start < 9000 and not remarks_is_nine_digit:
            _qty_upper = min(nine_digit_start, rem_start) + 30.0
            for _y2, ws2 in word_rows[:25]:
                if _PN_PAT.search(" ".join(w["text"] for w in ws2)):
                    continue  # data row — skip
                sv_vcs = [
                    w
                    for w in ws2
                    if YamahaCatalogueExtractor._is_variant_code(w["text"].upper())
                    and w["x0"] > desc_start + 15.0  # clearly past description zone
                    and w["x0"] < _qty_upper
                ]
                if len(sv_vcs) == 1:
                    # Subtract a margin for right-aligned data: qty digit x0 can
                    # sit a few px left of the column-header word's x0 (same
                    # right-alignment offset seen in nine_digit_start, fixed with -15).
                    qty_start = max(0.0, sv_vcs[0]["x0"] - 10.0)
                    break

        if nine_digit_start < 9000 or superseded_start < 9000:
            logger.debug(
                "Extra columns detected: nine_digit_start={:.1f} "
                "superseded_start={:.1f} rem_start={:.1f} remarks_is_nine_digit={}",
                nine_digit_start,
                superseded_start,
                rem_start,
                remarks_is_nine_digit,
            )

        any_detected = (
            rem_start < 9000
            or qty_col_xs
            or nine_digit_start < 9000
            or superseded_start < 9000
            or pn_start < 9000
            or desc_start < 9000
            or qty_start < 9000
        )
        # Detect bilingual (Spanish/English) catalogue: "DESCRIPCION" (Spanish, no T)
        # appears in the header block alongside the standard English "DESCRIPTION".
        # These catalogues have two header rows per column — Spanish then English —
        # and two description rows per data row — Spanish then English.
        is_bilingual = any(
            _BILINGUAL_HDR_PAT.search(" ".join(w["text"] for w in ws))
            for _, ws in word_rows[:15]
            if not _PN_PAT.search(" ".join(w["text"] for w in ws))
        )

        if any_detected:
            return ColBounds(
                pn_start=pn_start,
                desc_start=desc_start,
                qty_start=qty_start,
                rem_start=rem_start,
                qty_col_xs=qty_col_xs,
                nine_digit_start=nine_digit_start,
                escort_start=escort_start,
                superseded_start=superseded_start,
                header_detected=bool(header),
                remarks_is_nine_digit=remarks_is_nine_digit,
                is_bilingual=is_bilingual,
                col_labels=col_labels,
            )
        return None

    @staticmethod
    def _detect_header_row(
        word_rows: list[tuple[float, list[dict]]],
    ) -> dict[str, float]:
        """Find the column-header BLOCK and return {logical_col: x_start}.

        Yamaha PDFs frequently split column headers across two Y-rows, e.g.:

            Row A:  REF.   PART NO.   DESCRIPTION   REMARKS
            Row B:  NO.               Q'TY

        The old "first row with ≥3 hits" approach missed Q'TY on Row B,
        causing QTY values to be classified as description.

        New approach — scan the header BLOCK:
        1. Find the first non-data row containing both a part-label keyword
           ("PART" or "DESCRIPTION") and a column-anchor keyword
           ("DESCRIPTION", "REMARKS", or "Q'TY"/"QTY").
        2. From that row onward accumulate column x-positions across every
           subsequent non-data row (up to 6 more rows).
        3. Return the merged column map.

        Returned keys (only present for detected columns):
          "ref_no", "part_no", "description", "qty", "remarks",
          "nine_digit", "superseded"
        """

        def _classify(
            norms: list[str],
            ws_: list[dict],
            col_starts: dict[str, float],
            col_labels: dict[str, str],
        ) -> None:
            """Classify words on one row into col_starts (in-place).

            Also records the PDF's actual header text in col_labels so the UI
            can show "Existing Part No." instead of the generic "Part No." label.
            """
            bare = [n.replace("'", "") for n in norms]
            # Strip parentheses so "(9" matches "9" and "DIGIT)" matches "DIGIT".
            # This handles "REMARKS (9 DIGIT)" headers in India-market catalogues.
            stripped = [n.strip("()") for n in norms]
            for i, (norm, br, w) in enumerate(zip(norms, bare, ws_, strict=False)):
                if norm == "REF" and "ref_no" not in col_starts:
                    col_starts["ref_no"] = w["x0"]
                elif norm == "EXISTING" and "part_no" not in col_starts:
                    # "EXISTING PART NO." — the 12-digit column in ENTICER catalogues.
                    # Mark the label so the UI shows "Existing Part No." not "Part No."
                    col_starts["part_no"] = w["x0"]
                    col_labels["part_no"] = "Existing Part No."
                elif norm == "ESCORT" and "escort" not in col_starts:
                    # "ESCORT 12 DIGIT PART NO." — alternate part number column in ENTICER.
                    col_starts["escort"] = w["x0"]
                    col_labels["escort_part_no"] = "Escort 12 Digit Part No."
                elif norm == "PART" and "part_no" not in col_starts:
                    col_starts["part_no"] = w["x0"]
                elif norm in ("DESCRIPTION", "DESC") and "description" not in col_starts:
                    col_starts["description"] = w["x0"]
                elif norm == "NAME" and "description" not in col_starts:
                    # "PART NAME" header (ENTICER, SZ-old-format): map to description field.
                    # Guard: only accept "NAME" if the previous token was "PART" or it
                    # follows immediately after "EXISTING", to avoid misclassifying lone
                    # "NAME" tokens in foreword sentences.
                    prev_norm = norms[i - 1] if i > 0 else ""
                    if prev_norm in ("PART", "EXISTING"):
                        col_starts["description"] = w["x0"]
                        col_labels["description"] = "Part Name"
                elif br in ("QTY", "QUANTITY") and "qty" not in col_starts:
                    col_starts["qty"] = w["x0"]
                elif (
                    YamahaCatalogueExtractor._is_variant_code(norm)
                    and "qty" not in col_starts
                    and "description" in col_starts
                    and w["x0"] > col_starts.get("description", 0.0)
                ):
                    # Variant-code column header (e.g. "5US1" in ENTICER catalogues)
                    # used in place of "QTY" — record its x-position as qty_start.
                    col_starts["qty"] = w["x0"]
                elif norm == "REMARKS" and "remarks" not in col_starts:
                    col_starts["remarks"] = w["x0"]
                elif norm == "SUPERSEDED" and "superseded" not in col_starts:
                    col_starts["superseded"] = w["x0"]
                elif stripped[i] == "9" and "nine_digit" not in col_starts:
                    for look in range(i + 1, min(i + 3, len(norms))):
                        if stripped[look] in ("DIGIT", "DIGITS"):  # handle both singular and plural
                            col_starts["nine_digit"] = w["x0"]
                            break

        # ── Step 1: find the first row that anchors the header block ──────────
        header_start: int | None = None
        for idx, (_y, ws) in enumerate(word_rows[:30]):
            joined_raw = " ".join(w["text"] for w in ws)
            if _PN_PAT.search(joined_raw):
                continue  # skip data rows

            nset = {w["text"].upper().strip().rstrip(".") for w in ws}
            bare_set = {n.replace("'", "") for n in nset}

            # Standard anchor: row has a part-label AND a column anchor
            has_part_label = "PART" in nset or "DESCRIPTION" in nset or "EXISTING" in nset
            has_col_anchor = (
                "DESCRIPTION" in nset
                or "REMARKS" in nset
                or "QTY" in bare_set
                or "ESCORT" in nset  # ENTICER-family: ESCORT column is a reliable anchor
                or "SUPERSEDED" in nset  # ENTICER-family: SUPERSEDED appears in header
            )

            if has_part_label and has_col_anchor:
                header_start = idx
                break

        if header_start is None:
            return {}

        # ── Step 2: collect from the full header block ────────────────────────
        col_starts: dict[str, float] = {}
        col_labels: dict[str, str] = {}
        for _y, ws in word_rows[header_start : min(header_start + 6, 30)]:
            joined_raw = " ".join(w["text"] for w in ws)
            if _PN_PAT.search(joined_raw):
                break  # data row — header block ended

            norms = [w["text"].upper().strip().rstrip(".") for w in ws]
            _classify(norms, ws, col_starts, col_labels)

        if col_starts:
            logger.debug(
                "Header block (start={}): {}",
                header_start,
                ", ".join(f"{k}={v:.1f}" for k, v in sorted(col_starts.items())),
            )
        # Return col_starts merged with col_labels under a special "_labels" key.
        # The caller (_detect_col_bounds) unpacks them separately.
        if col_labels:
            col_starts["_labels"] = col_labels  # type: ignore[assignment]
        return col_starts

    @staticmethod
    def _is_variant_code(s: str) -> bool:
        """True for short alphanumeric tokens that mix letters and digits (variant codes)."""
        return (
            3 <= len(s) <= 5
            and s.isalnum()
            and any(c.isdigit() for c in s)
            and any(c.isalpha() for c in s)
        )

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

    # Colour words that MUST appear in a parenthetical description suffix for
    # it to be treated as a colour name (not a size, spec, or functional label).
    # Deliberately excludes "LIGHT", "DARK", "DEEP" etc. which appear in
    # non-colour contexts ("LIGHT ON/OFF", "DEEP BORE").
    _DESC_COLOUR_WORDS: frozenset[str] = frozenset(
        {
            "BLACK",
            "WHITE",
            "BLUE",
            "RED",
            "GREEN",
            "YELLOW",
            "ORANGE",
            "PURPLE",
            "PINK",
            "BROWN",
            "GREY",
            "GRAY",
            "SILVER",
            "GOLD",
            "CYAN",
            "MAGENTA",
            "TEAL",
            "NAVY",
            "MAROON",
            "BEIGE",
            "CREAM",
            "BRONZE",
            "COPPER",
            "TURQUOISE",
            "CANDY",
            "METALLIC",
            "MATTE",
            "MAT",
            "GLOSSY",
            "PEARL",
            "LUMINOUS",
            "VIVID",
        }
    )

    @classmethod
    def _lift_desc_colours(cls: type, rows: list[dict]) -> tuple[list[dict], list[dict]]:
        """Lift colour names from description parentheses into remarks.

        Business meaning: some older Yamaha catalogues (e.g. CRUX-S PC5KA5)
        encode colour variants as 'DESCRIPTION (COLOUR NAME)' in the
        Description column rather than via a foreword colour table plus
        FOR/EXCEPT remarks.  This method normalises those rows so the rest of
        the pipeline (roster-builder, frontend filter) can treat them
        identically to the standard FOR/EXCEPT format.

        Detection rules (BOTH must hold):
          1. The parenthetical content contains at least one word from
             _DESC_COLOUR_WORDS (guards against size specs like "STD" /
             "0.50MM O/S" and functional labels like "COVER").
          2. ≥2 rows share the same (section, ref_no, base_description)
             with *different* part numbers — the colour-variant signal.
             A lone parenthetical e.g. 'FLAP (COVER)' is never lifted.

        Side-effect on each affected row:
          • colour_hint → set to the colour abbreviation (e.g. 'YB').
            description, remarks, nine_digit_part_no are left UNCHANGED so
            the user sees the original PDF text in the table display.
            The agent and API use colour_hint for roster/filter logic.

        Returns:
            (updated_rows, synthetic_colour_codes)
            synthetic_colour_codes: [{abbreviation, name, code='',
                                       is_model_colour=False}, ...]
        """
        import re
        from collections import defaultdict

        # Matches 'BASE(COLOUR)' or 'BASE (COLOUR)' with an optional trailing
        # ' -MODEL' suffix.  Space before '(' is optional to handle cases like
        # 'COVER TAIL ASSY.(SILVER-3)' where PDF extraction omits the space.
        # Group 1 = base description, group 2 = parenthetical content.
        _PAT = re.compile(r"^(.+?)\s*\(([^)]+)\)(?:\s*[-–]\s*\S+)?$")

        def _is_colour_content(content: str) -> bool:
            # Normalise hyphens/digits around colour words before checking
            words = set(re.sub(r"[-/]", " ", content).upper().split())
            return bool(words & cls._DESC_COLOUR_WORDS)

        def _normalise_colour(content: str) -> str:
            """Canonical form: hyphens → spaces, collapse whitespace, uppercase."""
            return re.sub(r"\s+", " ", re.sub(r"-", " ", content).strip()).upper()

        # Pass 1: parse descriptions; only keep colour-word-containing content
        groups: dict[tuple, list[int]] = defaultdict(list)
        parsed: dict[int, tuple[str, str]] = {}  # idx → (base_desc, normalised_colour)

        for idx, row in enumerate(rows):
            desc = row.get("description", "")
            m = _PAT.match(desc)
            if not m:
                continue
            # Strip trailing periods/spaces from base for stable group key
            # (e.g. "COVER TAIL 1." and "COVER TAIL 1" group together).
            base = m.group(1).strip().rstrip(". ")
            content = m.group(2).strip()
            if not _is_colour_content(content):
                continue  # size spec / functional label — skip
            norm = _normalise_colour(content)
            key = (row.get("section", ""), row.get("ref_no", ""), base)
            groups[key].append(idx)
            parsed[idx] = (base, norm)

        # Only groups with 2+ different part_nos are confirmed colour variants
        variant_idxs: set[int] = set()
        for _key, idxs in groups.items():
            pns = {rows[i].get("part_no", "") for i in idxs}
            if len(pns) >= 2:
                variant_idxs.update(idxs)

        if not variant_idxs:
            return rows, []

        # Collect unique normalised colour names in first-encounter order
        seen_colours: dict[str, None] = {}
        for idx in sorted(variant_idxs):
            seen_colours.setdefault(parsed[idx][1], None)

        # Generate short, unique abbreviations (initials of colour name words)
        def _make_abbr(name: str, used: set[str]) -> str:
            words = re.sub(r"[^A-Z0-9 ]", "", name).split()
            base = "".join(w[0] if w.isalpha() else w for w in words)[:6]
            if not base or not base[0].isalpha():
                base = "C" + base
            abbr = base
            n = 2
            while abbr in used:
                abbr = base[:5] + str(n)
                n += 1
            used.add(abbr)
            return abbr

        used_abbrs: set[str] = set()
        colour_abbr: dict[str, str] = {}
        for cu in seen_colours:
            colour_abbr[cu] = _make_abbr(cu, used_abbrs)

        synthetic_colour_codes = [
            {
                "abbreviation": colour_abbr[cu],
                "name": cu.title(),
                "code": "",
                "is_model_colour": False,
            }
            for cu in seen_colours
        ]

        # Pass 2: tag affected rows with a colour_hint field.
        # Description, remarks, and nine_digit_part_no are NEVER modified —
        # the user sees exactly what the PDF shows (e.g. "FRONT FENDER (YAMAHA BLACK)"
        # and "5A-WF151-70" remain unchanged).  Only the invisible colour_hint key
        # is added so the agent and frontend can use it for roster/filter logic.
        updated: list[dict] = []
        for idx, row in enumerate(rows):
            if idx not in variant_idxs:
                updated.append(row)
                continue

            _, norm_colour = parsed[idx]
            new_row = dict(row)
            new_row["colour_hint"] = colour_abbr[norm_colour]
            updated.append(new_row)

        return updated, synthetic_colour_codes

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
        _COPYRIGHT = re.compile(r"[©©]\s*(\d{4})")
        # "edition, Month YYYY" or "edition YYYY"
        _EDITION = re.compile(r"edition[,\s]+\w+\s+(\d{4})", re.IGNORECASE)
        # Standalone 4-digit year that looks plausible (avoid part numbers etc.)
        _YEAR_BARE = re.compile(r"\b(19[89]\d|20[012]\d)\b")

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
        _ABBR_PAT = re.compile(r"^([A-Z0-9]{2,8})(\(\s*\*\s*\)|\*)?$")
        # Paint code: Yamaha codes always start with 2+ digits and are 4-5 chars
        # (e.g. "1344", "0390", "0033", "00AL", "00V9").  Rejects pure-letter
        # strings ("THE", "PART") and short page-numbers ("01", "12").
        _CODE_PAT = re.compile(r"^\d{2}[A-Z0-9]{2,3}$")
        # Words that indicate a header row — skip these
        _HEADER_WORDS = frozenset(
            {
                "ABBREVIATION",
                "ABBR",
                "COLOUR",
                "COLOR",
                "NAME",
                "CODE",
                "COLOURNAME",
                "COLOURCODE",
            }
        )
        # Column-swap detectors (compiled once, not per-row)
        _NUMERIC_CODE_RE = re.compile(r"^\d{3,5}$")
        _ALPHA_ABBR_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
        # Vocabulary check: real colour names always contain at least one of these.
        # Prevents TOC/section-name rows ("CYLINDER HEAD", "FIG 1") being accepted.
        _COLOUR_WORDS = frozenset(
            {
                "BLACK",
                "WHITE",
                "BLUE",
                "RED",
                "GREEN",
                "YELLOW",
                "ORANGE",
                "PURPLE",
                "PINK",
                "BROWN",
                "GREY",
                "GRAY",
                "SILVER",
                "GOLD",
                "CYAN",
                "MAGENTA",
                "TEAL",
                "NAVY",
                "MAROON",
                "BEIGE",
                "CREAM",
                "BRONZE",
                "COPPER",
                "TURQUOISE",
                "METALLIC",
                "MATTE",
                "MAT",
                "GLOSSY",
                "PEARL",
                "LUMINOUS",
                "VIVID",
                "DARK",
                "DEEP",
                "LIGHT",
                "BRIGHT",
                "REDDISH",
                "BLUISH",
                "GRAYISH",
                "GREYISH",
                "PURPLISH",
                "YELLOWISH",
                "GREENISH",
                "BLACKISH",
                "WHITISH",
                "COCKTAIL",  # "BLUISH WHITE COCKTAIL 1"
                "YAMAHA",  # "YAMAHA BLACK"
            }
        )
        # Common English words that can never be Yamaha colour abbreviations.
        # Rejects prose false-positives like "BOTH | METALLIC SILVER | 0390".
        _STOP_WORDS = frozenset(
            {
                "TO",
                "AT",
                "BE",
                "OF",
                "IN",
                "ON",
                "BY",
                "OR",
                "SO",
                "AS",
                "IF",
                "DO",
                "GO",
                "IS",
                "AN",
                "IT",
                "NO",
                "UP",
                "FOR",
                "AND",
                "THE",
                "BUT",
                "NOT",
                "YET",
                "NOR",
                "ANY",
                "ALL",
                "BOTH",
                "FROM",
                "WITH",
                "INTO",
                "THAN",
                "THIS",
                "THAT",
                "WILL",
                "HAVE",
                "BEEN",
                "WERE",
                "MORE",
                "ALSO",
                "SUCH",
                "SOME",
                "NOTE",
                "FORE",
                "PART",
                "ONLY",
                "WHEN",
                "THEN",
                "EACH",
                "USED",
            }
        )

        def _parse_row(abbr_raw: str, name: str, code_raw: str) -> dict | None:
            abbr_clean = abbr_raw.replace(" ", "").upper()
            code_clean = code_raw.replace(" ", "").upper()

            # Some Yamaha forewords use "Colour Code | Colour Name | Abbreviation"
            # column order (numeric code on the left, text abbreviation on the right).
            # Detect by: left is purely numeric, right starts with a letter.
            if _NUMERIC_CODE_RE.match(abbr_clean) and _ALPHA_ABBR_RE.match(code_clean):
                abbr_clean, code_clean = code_clean, abbr_clean

            m = _ABBR_PAT.match(abbr_clean)
            if not m or m.group(1) in _HEADER_WORDS:
                return None

            abbr = m.group(1)
            # Reject purely-numeric abbreviations (section/page numbers, quantities)
            if abbr.isdigit():
                return None
            # Yamaha colour abbreviations always start with a letter (YB, CM6, SMX …)
            if not abbr[0].isalpha():
                return None
            # Reject common English function words that can never be colour codes
            if abbr in _STOP_WORDS:
                return None

            if not _CODE_PAT.match(code_clean):
                return None

            name = name.strip()
            if len(name) < 3:
                return None
            if name.upper() in _HEADER_WORDS:
                return None

            # Colour name must contain a recognisable colour word.
            # Eliminates TOC/section rows: "CYLINDER HEAD", "FIG 1 ASSEMBLY" etc.
            name_words = set(name.upper().split())
            if not name_words.intersection(_COLOUR_WORDS):
                return None

            return {
                "abbreviation": abbr,
                "name": name,
                "code": code_clean,
                "is_model_colour": bool(m.group(2)),
            }

        def _dedup(entries: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for e in entries:
                key = e["abbreviation"].upper()
                if key not in seen:
                    seen.add(key)
                    out.append(e)
            return out

        # Best partial result seen across all pages/strategies (handles single-colour PDFs
        # where only 1 entry exists and ">= 3" would never trigger).
        best_partial: list[dict] = []

        try:
            with _plumber.open(str(pdf_path)) as pdf:
                for page in pdf.pages[:15]:
                    # --- Strategy 1: pdfplumber ruled-table extraction ---
                    # Scan ALL tables on the page; keep the one with the most valid
                    # colour entries (avoids premature return on a mixed/fake table).
                    best_page: list[dict] = []
                    for table in page.extract_tables() or []:
                        if not table or len(table) < 2:
                            continue
                        entries: list[dict] = []
                        for row in table:
                            if not row or len(row) < 3:
                                continue
                            e = _parse_row(
                                str(row[0] or ""),
                                str(row[1] or ""),
                                str(row[2] or ""),
                            )
                            if e:
                                entries.append(e)
                        if len(entries) > len(best_page):
                            best_page = entries

                    if len(best_page) >= 3:
                        return _dedup(best_page)  # Confident multi-colour result
                    if len(best_page) > len(best_partial):
                        best_partial = best_page  # Remember for single-colour fallback

                    # --- Strategy 2: word-position row clustering ---
                    words = page.extract_words() or []
                    if not words:
                        continue

                    # Group words into Y-rows (same tolerance as main extractor)
                    rows_by_y: dict[float, list[dict]] = {}
                    for w in words:
                        y_key = round(float(w.get("top", 0)) / _Y_TOL) * _Y_TOL
                        rows_by_y.setdefault(y_key, []).append(w)

                    entries2: list[dict] = []
                    for y_key in sorted(rows_by_y):
                        ws = sorted(rows_by_y[y_key], key=lambda w: w["x0"])
                        if len(ws) < 3:
                            continue

                        # Left token may be split: "CM6" + "(*)" → join if next is a marker
                        left_parts = [ws[0]["text"].strip()]
                        rest_start = 1
                        if len(ws) > 1 and ws[1]["text"].strip() in ("(*)", "*", "(*)"):
                            left_parts.append(ws[1]["text"].strip())
                            rest_start = 2

                        left = "".join(left_parts).replace(" ", "").upper()
                        right = ws[-1]["text"].strip()
                        # Middle words form the colour name
                        middle_ws = ws[rest_start:-1]
                        middle = " ".join(w["text"] for w in middle_ws).strip()
                        if not middle:
                            middle = (
                                ws[rest_start]["text"].strip() if rest_start < len(ws) - 1 else ""
                            )

                        e = _parse_row(left, middle, right)
                        if e:
                            entries2.append(e)

                    if len(entries2) >= 3:
                        return _dedup(entries2)  # Confident multi-colour result
                    if len(entries2) > len(best_partial):
                        best_partial = entries2

        except Exception:  # noqa: BLE001
            pass

        # No page produced >= 3 valid entries (single-colour PDF or non-standard layout).
        # Return best partial so single-colour PDFs still show their one chip.
        return _dedup(best_partial)

    @staticmethod
    def _extract_available_colours(pdf_path: Path) -> list[str]:
        """Extract colour variant captions from a Yamaha parts catalogue cover page.

        Yamaha catalogues display bike images side-by-side on the cover (or on
        a dedicated "AVAILABLE COLOUR" page) with colour names as captions
        beneath each image.  Four caption formats are handled:

            A. "Yamaha Alpha Cygnus Black"  — Yamaha-branded (Alpha, Ray)
            B. "2SP3-Gold", "BP16-Cyan"     — VariantCode-ColourName (Fasino, XC115)
            C. "Cyan Metallic | Mat Black"  — bare colour clusters (Fasino 2SP2)
            D. "Black Metallic Bike"        — [colour] Bike (Fazer 2WS)

        Key discriminators that separate genuine captions from colour-code tables:
        - On a Y-row split by large X-gaps (≥40 pts), every sub-group must
          contain at least one colour noun.  Foreword/two-column pages have one
          sub-group that is plain sentence text (no colour nouns) — those rows
          are rejected.
        - Colour-code table rows start with a short all-caps abbreviation (e.g.
          "CM6(*)", "MBL2") in the first sub-group, which has no colour noun.
        - Paint-code abbreviation codes (2-4 char all-uppercase, e.g. "YB",
          "SMX") as the last word flag a colour-table row, not a caption.

        Returns a deduplicated list of colour name strings.  Empty if none found.
        """
        import pdfplumber as _plumber

        # Large X-gap (pts) between two side-by-side bike images / captions.
        # Normal inter-word spacing: ≤15 pts.  Image-column gap: ≥80 pts.
        _X_GAP = 40.0
        # Captions are short; sentence text in foreword pages is much longer.
        _MAX_WORDS = 8
        # 2–4 char ALL-CAPS endings that are paint-code abbreviations, not colours.
        # Common colour-word endings that are allowed even when all-caps:
        _COLOUR_ENDINGS = frozenset(
            {
                "black",
                "white",
                "blue",
                "red",
                "cyan",
                "gray",
                "grey",
                "gold",
                "pink",
                "green",
                "brown",
                "silver",
                "orange",
                "maroon",
                "purple",
            }
        )

        def _split_by_gap(row_words: list[dict]) -> list[list[dict]]:
            groups: list[list[dict]] = []
            current: list[dict] = [row_words[0]]
            for prev, cur in zip(row_words, row_words[1:], strict=False):
                gap = float(cur.get("x0", 0)) - float(prev.get("x1", 0))
                if gap > _X_GAP:
                    groups.append(current)
                    current = [cur]
                else:
                    current.append(cur)
            groups.append(current)
            return groups

        def _group_has_colour(group: list[dict]) -> bool:
            return any(w["text"].lower() in _CAPTION_COLOUR_NOUNS for w in group)

        def _caption_from_group(group: list[dict]) -> str | None:
            """Extract a colour name from one X-gap-split sub-group, or None."""
            texts = [w["text"] for w in group]

            # Sentence-length sub-groups are foreword text, not image captions.
            if len(texts) > _MAX_WORDS:
                return None

            if _COPYRIGHT_PAT.search(" ".join(texts)):
                return None

            # Format B: VariantCode-ColourName, e.g. "2SP3-Gold", "BP16-Cyan",
            # "5YY6-Dark Blue Pearl".  Also: "BP16_Cyan Metallic" (underscore),
            # "2SP3-Black-Pearl" (hyphenated colours).
            # Tried FIRST: the full token "2SP3-Gold" is a single word that isn't
            # itself a colour noun, so all downstream checks would wrongly reject it.
            m = _VARIANT_CODE_PREFIX_PAT.match(texts[0])

            # Guard: real variant codes (2SP3, BP16) always contain a digit.
            # Plain hyphenated English words (NON-METALLIC, MID-BLUE) must not
            # be mistaken for variant codes and have their suffix returned.
            if m and any(c.isdigit() for c in m.group(1)):
                colour_part = " ".join([m.group(2)] + texts[1:]).strip()
                if any(w.lower() in _CAPTION_COLOUR_NOUNS for w in colour_part.split()):
                    return colour_part
                return None  # variant-code token but no colour → not a caption

            # Format B variant: underscore instead of hyphen ("BP16_Cyan Metallic")
            # or multi-word colour after dash ("5YY6-Dark Blue Pearl").
            if "_" in texts[0] or ("-" in texts[0] and re.match(r"^[A-Z0-9]{2,6}-", texts[0])):
                # Split on underscore or dash
                variant_and_colour = re.split(r"[_-]", texts[0], maxsplit=1)
                if len(variant_and_colour) == 2:
                    variant_code, colour_start = variant_and_colour
                    if any(c.isdigit() for c in variant_code) and variant_code.upper() != "YAMAHA":
                        # Reconstruct: colour_start + remaining words
                        colour_part = " ".join([colour_start] + texts[1:]).strip()
                        if any(w.lower() in _CAPTION_COLOUR_NOUNS for w in colour_part.split()):
                            return colour_part
                        return None

            # Reject figure/code-reference prefixes: "FO/90", "1X/33/P1" etc.
            if "/" in texts[0]:
                return None

            # Reject paint-code table rows with embedded 4-digit paint-code numbers
            # e.g. "A 1124 WHITE METALLIC 6", "C 1177 VIVID PURPLISH BLUE COCKTAIL 5"
            if any(re.match(r"^\d{4,5}$", t) for t in texts):
                return None

            # Must contain at least one colour noun.
            if not any(t.lower() in _CAPTION_COLOUR_NOUNS for t in texts):
                return None

            # Single-word groups must be a named base colour, not a modifier alone.
            # "Black", "Cyan" → OK; "Metallic", "Mat", "Vivid" alone → reject.
            if len(texts) == 1 and texts[0].lower() not in _STANDALONE_COLOUR_NOUNS:
                return None

            # Reject if last word is a short paint-code abbreviation:
            # e.g. "YB", "SMX", "DRMK" — but keep "BLACK", "BLUE", etc.
            last = texts[-1]
            if (
                2 <= len(last) <= 4
                and last == last.upper()
                and last.isalpha()
                and last.lower() not in _COLOUR_ENDINGS
            ):
                return None

            # Reject colour-code-table rows: first token is a short all-caps
            # abbreviation like "MBL2", "CM6(*)", "SMX(*)", "YB".
            first = texts[0]
            if re.match(r"^[A-Z0-9]{2,8}(\([*]\)|\*)?$", first) and first.upper() != "YAMAHA":
                return None

            return " ".join(texts)

        def _page_captions(words: list[dict]) -> list[str]:
            """Extract all colour captions from one page's word list."""
            rows_by_y: dict[float, list[dict]] = {}
            for w in words:
                y_key = round(float(w.get("top", 0)) / _Y_TOL) * _Y_TOL
                rows_by_y.setdefault(y_key, []).append(w)

            found: list[str] = []
            seen_local: set[str] = set()
            for y_key in sorted(rows_by_y):
                row_ws = sorted(rows_by_y[y_key], key=lambda w: w["x0"])
                sub_groups = _split_by_gap(row_ws)

                # Row-level guard: every sub-group with ≥2 words must contain a
                # colour noun.  Foreword two-column rows have one sub-group that
                # is plain sentence text → reject the whole row.
                if any(len(g) >= 2 and not _group_has_colour(g) for g in sub_groups):
                    continue

                # Reject paint-code table rows: any sub-group starting with a
                # 4-5 digit paint-code number ("0918", "0033" etc.).
                if any(re.match(r"^\d{4,5}$", g[0]["text"]) for g in sub_groups):
                    continue

                for group in sub_groups:
                    cap = _caption_from_group(group)
                    if cap and cap not in seen_local:
                        seen_local.add(cap)
                        found.append(cap)
            return found

        captions: list[str] = []
        seen: set[str] = set()

        try:
            with _plumber.open(str(pdf_path)) as pdf:
                # ── Pass 1: dedicated "AVAILABLE COLOUR" page (pages 1-20) ────
                for page in pdf.pages[:20]:
                    text = page.extract_text() or ""
                    if not _AVAIL_COLOUR_PAT.search(text):
                        continue
                    words = page.extract_words(x_tolerance=6, y_tolerance=4) or []
                    for cap in _page_captions(words):
                        if cap not in seen:
                            seen.add(cap)
                            captions.append(cap)
                    if captions:
                        break

                # ── Pass 2: cover/intro image pages (pages 1-5) ────────────
                # Scan early pages for colour caption rows.  These pages have
                # bike images (large whitespace) between sparse caption rows.
                # Restricted to pages 1-5 so foreword text pages are excluded.
                if not captions:
                    for page in pdf.pages[:5]:
                        words = page.extract_words(x_tolerance=6, y_tolerance=4) or []
                        if not words:
                            continue
                        page_caps = _page_captions(words)
                        if page_caps:
                            for cap in page_caps:
                                if cap not in seen:
                                    seen.add(cap)
                                    captions.append(cap)
                            break  # one image page per PDF

        except Exception:  # noqa: BLE001
            pass

        return captions

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
            return (
                3 <= len(s) <= 5
                and s.isalnum()
                and any(c.isdigit() for c in s)
                and any(c.isalpha() for c in s)
            )

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
                if bounds and bounds.is_bilingual:
                    raw = _bilingual_fig_section(ws, raw, bounds)
                new_section = raw if raw else f"FIG. {fig_m.group(1)}"
                new_fig = f"FIG. {fig_m.group(1)}"
                break
            opt_m = _PARTS_OPTION_PAT.match(joined_pre)
            if opt_m:
                new_section = opt_m.group(1).strip()
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
                if (
                    partial_check
                    and not _FIG_PAT.match(joined)
                    and not _FIG_PAT.match(_STAMP_PREFIX_PAT.sub("", joined))
                ):
                    idx += 1
                raw = _CID_PAT.sub("", fig_m.group(2)).strip()
                if bounds and bounds.is_bilingual:
                    raw = _bilingual_fig_section(ws, raw, bounds)
                new_fig = f"FIG. {fig_m.group(1)}"
                new_section = raw if raw else new_fig  # fallback for CID-corrupt names
                sections_seen.add(new_section)
                last_ref_no = ""  # reset ref carry at every new figure
                continue

            opt_m = _PARTS_OPTION_PAT.match(joined)
            if opt_m:
                new_section = opt_m.group(1).strip()
                sections_seen.add(new_section)
                last_ref_no = ""
                continue

            if _SKIP_PAT.match(joined):
                continue

            # Quick check for part number before expensive filtering
            if not _PN_PAT.search(joined):
                continue

            # ── Positional filtering ────────────────────────────────────────
            # Find the part number word's x0 to anchor the column layout.
            # Remove any words that are >80 pts to the left of it — these are
            # figure-illustration numbers printed in the margin (e.g. CRUX PDFs,
            # where illustration numbers sit 200+ pts left of the part number).
            # 80 pts (vs the old 50) lets us capture ref-number columns that sit
            # 60-70 pts left of the part number (e.g. ENTICER 5US2 at 67 pts).
            pn_word = next((w for w in ws if _PN_PAT.match(w["text"])), None)
            if pn_word:
                x_cutoff = pn_word["x0"] - 80
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

            # Initialise extra-column accumulators here so they are always defined
            # regardless of which extraction mode runs below.
            nine_digit_parts: list[str] = []
            escort_parts: list[str] = []
            superseded_parts: list[str] = []

            if bounds and bounds.qty_col_xs and pn_word:
                n_cols = len(bounds.qty_col_xs)
                qty_zone_x = min(bounds.qty_col_xs) - 15.0
                max_qty_cx = max(bounds.qty_col_xs)  # x-centre of rightmost qty col
                slots: list[str] = [""] * n_cols
                desc_parts: list[str] = []
                rem_parts: list[str] = []
                # Words in the gap between the last qty column and the first extra
                # column (or REMARKS when no extra columns exist).
                pre_rem_parts: list[str] = []
                # Only use the gap-zone when the REMARKS boundary is known;
                # without rem_start we cannot define the gap reliably.
                use_gap_zone = bounds.rem_start < 9000
                # Track right edge of last description word to detect footnote markers.
                last_desc_x1 = 0.0

                for w in ws_filtered:
                    if w["x0"] < pn_word["x1"]:  # ref-no / part-number
                        continue
                    if use_gap_zone and w["x0"] >= bounds.rem_start:
                        rem_parts.append(w["text"])
                        continue
                    # Route words into extra columns by x-position (right → left priority)
                    if bounds.superseded_start < 9000 and w["x0"] >= bounds.superseded_start:
                        superseded_parts.append(w["text"])
                        continue
                    if bounds.escort_start < 9000 and w["x0"] >= bounds.escort_start:
                        escort_parts.append(w["text"])
                        continue
                    if bounds.nine_digit_start < 9000 and w["x0"] >= bounds.nine_digit_start:
                        nine_digit_parts.append(w["text"])
                        continue
                    w_cx = (w["x0"] + w["x1"]) / 2
                    # Qty slot: accept 1–5 digit strings only.  India-market PDFs
                    # have 9-char all-digit nine_digit values (e.g. "344010109") to
                    # the right of qty columns; those must NOT contaminate qty slots.
                    if w_cx >= qty_zone_x and re.match(r"^\d{1,5}$", w["text"]):
                        # Guard: Yamaha catalogues suffix descriptions with small
                        # footnote markers (e.g. "COVER, CYLINDER HEAD SIDE 3",
                        # "HEAD, CYLINDER 1") that physically sit within the qty zone
                        # but are immediately adjacent to description text (gap < 15 px).
                        # Distinguish by the x-gap from the last description word's
                        # right edge: < 15 px → footnote → route to description.
                        gap_from_desc = w["x0"] - last_desc_x1
                        if last_desc_x1 > 0 and gap_from_desc < 15.0 and len(w["text"]) <= 2:
                            desc_parts.append(w["text"])
                            last_desc_x1 = max(last_desc_x1, w["x1"])
                        else:
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
                        last_desc_x1 = max(last_desc_x1, w["x1"])

                # Lookahead: pn and description on different Y-rows.
                # Only needed when the current row has no description AND no
                # gap-zone qualifiers (i.e. effectively empty after the pn).
                if not desc_parts and not pre_rem_parts and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (
                        next_joined
                        and not _PN_PAT.search(next_joined)
                        and not _FIG_PAT.match(next_joined)
                        and not _SKIP_PAT.match(next_joined)
                    ):
                        desc_parts = [_COPYRIGHT_PAT.sub("", next_joined).strip()]
                        idx += 1

                # Bilingual catalogue: Spanish description is on the data row;
                # English description is on the immediately following Y row.
                # Replace Spanish with English by consuming that next row.
                elif bounds.is_bilingual and desc_parts and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (
                        next_joined
                        and not _PN_PAT.search(next_joined)
                        and not _FIG_PAT.match(next_joined)
                        and not _SKIP_PAT.match(next_joined)
                    ):
                        # Only consume words in the description column zone
                        # (allow 30 pt left-margin tolerance for data alignment).
                        # Stop at the first qty column so gap-zone qualifiers
                        # like applicability codes ("UR") are not captured.
                        _d_lo = max(0.0, bounds.desc_start - 30.0)
                        _d_hi = (
                            (min(bounds.qty_col_xs) - 15.0)
                            if bounds.qty_col_xs
                            else (bounds.rem_start if bounds.rem_start < 9000 else 9999.0)
                        )
                        eng_words = [
                            w["text"] for w in next_ws if w["x0"] >= _d_lo and w["x0"] < _d_hi
                        ]
                        if eng_words:
                            desc_parts = eng_words
                            idx += 1
                            # Also replace gap-zone (pre_rem_parts) and remarks
                            # with English values, discarding Spanish words like "PARA".
                            _qty_lo = min(bounds.qty_col_xs) if bounds.qty_col_xs else 9999.0
                            _rem_hi = bounds.rem_start if bounds.rem_start < 9000 else 9999.0
                            pre_rem_parts = [
                                w["text"]
                                for w in next_ws
                                if w["x0"] >= _qty_lo
                                and w["x0"] < _rem_hi
                                and not re.match(r"^\d{1,5}$", w["text"])
                            ]
                            if bounds.rem_start < 9000:
                                rem_parts = [
                                    w["text"] for w in next_ws if w["x0"] >= bounds.rem_start
                                ]

                desc_raw = " ".join(desc_parts)
                # Truncate at any embedded PN: indicates a supersession code or
                # merged Y-row from tight typesetting (e.g. CRUX 5KA1 PDF).
                # Yamaha descriptions are plain English — they never legitimately
                # contain a part number.
                _pn_in_desc = _PN_PAT.search(desc_raw)
                desc = desc_raw[: _pn_in_desc.start()].strip() if _pn_in_desc else desc_raw
                qty = "/".join(slots)
                filled = [s for s in slots if s]
                # Collapse "1/1/1/1" → "1" only when EVERY slot is filled identically
                if filled and len(set(filled)) == 1 and len(filled) == n_cols:
                    qty = filled[0]
                # NOTE: do NOT rstrip("/") here.  Trailing slashes like "1/" are
                # semantically meaningful: they signal that this part has no qty
                # for the LAST variant(s), allowing the frontend variantQty filter
                # to hide the row when that variant is selected.  The collapse step
                # above already converts fully-filled identical slots ("1/1") to
                # plain "1", so spurious trailing slashes from genuine duplicates
                # are handled by _detect_col_bounds de-duplication instead.
                # Pattern-based fallback for India-market PDFs where header
                # detection failed to set nine_digit_start / superseded_start.
                # Nine-digit part numbers are exactly 9 alphanumeric chars.
                # Superseded part numbers are exactly 12 alphanumeric chars.
                # This is safe for standard PDFs: their gap-zone tokens (colour
                # codes like "DBNM8", qualifiers like "FOR") are never 9 or 12
                # chars of pure uppercase alphanumeric.
                if bounds.nine_digit_start >= 9000 and pre_rem_parts:
                    leftover: list[str] = []
                    for _tok in pre_rem_parts:
                        if re.match(r"^[A-Z0-9]{9}$", _tok, re.IGNORECASE):
                            nine_digit_parts.append(_tok)
                        elif re.match(r"^[A-Z0-9]{12}$", _tok, re.IGNORECASE):
                            superseded_parts.append(_tok)
                        else:
                            leftover.append(_tok)
                    pre_rem_parts = leftover

                # Combine gap-zone words with explicit-remarks words.
                # Gap-zone words (pre_rem_parts) appear to the LEFT of the
                # REMARKS column in the PDF, so they always come first.
                # e.g. gap=["DBNM8"] rem=["FOR","YB"] → "DBNM8 FOR YB"
                # e.g. gap=["FOR"]   rem=["SM12"]     → "FOR SM12"
                remarks = " ".join(pre_rem_parts + rem_parts)

            elif bounds and bounds.desc_start < 9000 and pn_word:
                # ── Single-variant positional mode ──────────────────────────
                # The header row was detected and gave us explicit column
                # x-starts for DESCRIPTION, Q'TY, and REMARKS.  Classify every
                # word after the part number by its x-position — no heuristics.
                desc_sv: list[str] = []
                qty_sv: list[str] = []
                rem_sv: list[str] = []

                for w in ws_filtered:
                    if w["x0"] < pn_word["x1"]:  # ref-no / part-no zone
                        continue
                    if bounds.rem_start < 9000 and w["x0"] >= bounds.rem_start:
                        rem_sv.append(w["text"])
                        continue
                    if bounds.superseded_start < 9000 and w["x0"] >= bounds.superseded_start:
                        superseded_parts.append(w["text"])
                        continue
                    if bounds.escort_start < 9000 and w["x0"] >= bounds.escort_start:
                        escort_parts.append(w["text"])
                        continue
                    if bounds.nine_digit_start < 9000 and w["x0"] >= bounds.nine_digit_start:
                        nine_digit_parts.append(w["text"])
                        continue
                    if bounds.qty_start < 9000 and w["x0"] >= bounds.qty_start:
                        qty_sv.append(w["text"])
                        continue
                    desc_sv.append(w["text"])

                # Lookahead when description zone is empty
                if not desc_sv and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (
                        next_joined
                        and not _PN_PAT.search(next_joined)
                        and not _FIG_PAT.match(next_joined)
                        and not _SKIP_PAT.match(next_joined)
                    ):
                        desc_sv = [_COPYRIGHT_PAT.sub("", next_joined).strip()]
                        idx += 1

                # Bilingual catalogue: Spanish description captured in desc_sv;
                # English description is on the immediately following Y row.
                elif bounds.is_bilingual and desc_sv and idx < len(word_rows):
                    _ny, next_ws = word_rows[idx]
                    next_joined = " ".join(w["text"] for w in next_ws).strip()
                    if (
                        next_joined
                        and not _PN_PAT.search(next_joined)
                        and not _FIG_PAT.match(next_joined)
                        and not _SKIP_PAT.match(next_joined)
                    ):
                        _d_lo = max(0.0, bounds.desc_start - 30.0)
                        # Upper bound: stop at qty_start so gap-zone qualifiers
                        # like applicability codes ("UR") are not captured.
                        _d_hi = (
                            bounds.qty_start
                            if bounds.qty_start < 9000
                            else (bounds.rem_start if bounds.rem_start < 9000 else 9999.0)
                        )
                        eng_words = [
                            w["text"] for w in next_ws if w["x0"] >= _d_lo and w["x0"] < _d_hi
                        ]
                        if eng_words:
                            desc_sv = eng_words
                            idx += 1
                            # Also replace gap-zone non-digits and remarks with
                            # English values from the same row. This discards
                            # Spanish connector words like "PARA" (= "FOR") and
                            # keeps the English equivalents ("FOR") in the remarks.
                            # Digit qty values come from the data row (correct);
                            # only the non-digit applicability codes are swapped.
                            if bounds.qty_start < 9000:
                                _rem_hi = bounds.rem_start if bounds.rem_start < 9000 else 9999.0
                                _eng_gap = [
                                    w["text"]
                                    for w in next_ws
                                    if bounds.qty_start <= w["x0"] < _rem_hi
                                    and not re.match(r"^\d{1,5}$", w["text"])
                                ]
                                qty_sv = [t for t in qty_sv if re.match(r"^\d{1,5}$", t)] + _eng_gap
                            if bounds.rem_start < 9000:
                                rem_sv = [w["text"] for w in next_ws if w["x0"] >= bounds.rem_start]

                # Recover a qty digit that pdfplumber merged into the last
                # description token (PDF text runs where the gap between ")"
                # and the adjacent qty digit is < x_tolerance, e.g. "HEAD)2"
                # stored as a single glyph sequence).  Guard: only apply when
                # the qty zone was detected but came back empty.
                if bounds.qty_start < 9000 and not qty_sv and desc_sv:
                    _last = desc_sv[-1]
                    _mm = re.match(r"^(.+[^\d])(\d{1,2})$", _last)
                    if _mm:
                        desc_sv[-1] = _mm.group(1)
                        qty_sv.append(_mm.group(2))

                desc_raw_sv = " ".join(desc_sv)
                _pn_in_desc_sv = _PN_PAT.search(desc_raw_sv)
                desc_sv_clean = (
                    desc_raw_sv[: _pn_in_desc_sv.start()].strip() if _pn_in_desc_sv else desc_raw_sv
                )

                if bounds.qty_start < 9000:
                    # Qty zone was detected — use positional result directly.
                    # Non-digit tokens in the qty zone (e.g. applicability codes
                    # like "UR", "S3" that sit between the qty column and the
                    # REMARKS header) are routed to remarks rather than discarded.
                    qty_raw = " ".join(qty_sv)
                    qty_m = re.search(r"\d+", qty_raw)
                    desc = desc_sv_clean
                    qty = qty_m.group() if qty_m else ""
                    non_digit_qty = [t for t in qty_sv if not re.match(r"^\d+$", t)]
                    if non_digit_qty:
                        rem_sv = non_digit_qty + rem_sv
                else:
                    # Qty column not detected — apply _split_after heuristics.
                    # _split_after returns (desc, qty, trailing_remarks); capture
                    # the third value so non-digit trailing tokens reach remarks.
                    desc, qty, _sa_rem = self._split_after(desc_sv_clean, num_variants)
                    if _sa_rem:
                        rem_sv = [_sa_rem] + rem_sv

                remarks = " ".join(rem_sv)

            else:
                # ── Text-based fallback ─────────────────────────────────────
                after = _COPYRIGHT_PAT.sub("", joined[pn_m.end() :]).strip()

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
                    if (
                        next_joined
                        and not _PN_PAT.search(next_joined)
                        and not _FIG_PAT.match(next_joined)
                        and not _SKIP_PAT.match(next_joined)
                    ):
                        after = _COPYRIGHT_PAT.sub("", next_joined).strip()
                        idx += 1

                desc, qty, remarks = self._split_after(after, num_variants)
                if not remarks and positional_remarks:
                    remarks = positional_remarks

            # ── Ref number ─────────────────────────────────────────────────
            if re.match(r"^[\d\s]+$", before):
                last_int = re.search(r"(\d{1,3})\s*$", before)
                ref_no = last_int.group(1) if last_int else before.strip()
            else:
                ref_no = before

            # Carry forward: when the PDF leaves ref blank for the 2nd+ part
            # in a shared-ref group, reuse the last seen ref number.
            if ref_no:
                last_ref_no = ref_no
            elif last_ref_no:
                ref_no = last_ref_no

            rows.append(
                {
                    "section": new_section,
                    "fig_no": new_fig,
                    "ref_no": ref_no,
                    "part_no": pn,
                    "description": _COLOUR_SUFFIX_PAT.sub("", desc).strip(),
                    "qty": qty,
                    "nine_digit_part_no": " ".join(nine_digit_parts),
                    "escort_part_no": " ".join(escort_parts),
                    "superseded_part_no": " ".join(superseded_parts),
                    "remarks": remarks,
                }
            )

        return rows, new_section, new_fig, sections_seen

    # ------------------------------------------------------------------
    # Text-line fallback (no positional data)
    # ------------------------------------------------------------------

    def _text_fallback(self, pdf_path: Path) -> tuple[list[dict[str, str]], set[str]]:
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

                    # Skip KITS pages, cross-reference index pages, and named index pages.
                    # Same FIG-header guard as the positional pass: genuine index pages
                    # never contain "FIG." section markers.
                    page_head = text[:400]
                    _has_fig_text = bool(_FIG_PAT.search(page_head))
                    if (
                        _KITS_PAGE_PAT.search(page_head)
                        or (_XREF_PAGE_PAT.search(page_head) and not _has_fig_text)
                        or _INDEX_PAGE_PAT.search(page_head)
                        or _FOREWORD_PAGE_PAT.search(page_head)
                    ):
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

                        opt_m = _PARTS_OPTION_PAT.match(line)
                        if opt_m:
                            current_section = opt_m.group(1).strip()
                            sections_seen.add(current_section)
                            continue

                        pn_m = _PN_PAT.search(line)
                        if not pn_m:
                            continue
                        pn = pn_m.group(1).replace("–", "-")  # normalize en-dash
                        if pn.startswith("X") or not current_section:
                            continue

                        before = line[: pn_m.start()].strip()
                        after = _COPYRIGHT_PAT.sub("", line[pn_m.end() :]).strip()

                        ref_m = re.match(r"^(\d{1,3})\s*$", before)
                        ref_no = ref_m.group(1) if ref_m else before
                        desc, qty, remarks = self._split_after(after, num_variants=0)

                        rows.append(
                            {
                                "section": current_section,
                                "fig_no": current_fig,
                                "ref_no": ref_no,
                                "part_no": pn,
                                "description": desc,
                                "qty": qty,
                                "nine_digit_part_no": "",
                                "escort_part_no": "",
                                "superseded_part_no": "",
                                "remarks": remarks,
                            }
                        )
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
        if not re.match(r"^\d+$", tokens[-1]):
            remarks = tokens.pop()

        if not tokens:
            return "", "", remarks

        # Step 2: collect qty tokens
        qty_vals: list[str] = []
        if num_variants > 0:
            # Known variant count — pop at most num_variants digits from the right
            count = 0
            while tokens and count < num_variants and re.match(r"^\d+$", tokens[-1]):
                qty_vals.insert(0, tokens.pop())
                count += 1
        else:
            # Unknown — greedy: pop all trailing digit tokens
            while tokens and re.match(r"^\d+$", tokens[-1]):
                qty_vals.insert(0, tokens.pop())

        qty = "/".join(qty_vals) if qty_vals else ""
        # Collapse "1/1/1/1" → "1" when all variant qtys are identical
        if qty and len(set(qty_vals)) == 1:
            qty = qty_vals[0]
        return " ".join(tokens), qty, remarks
