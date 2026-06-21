"""Catalogue Agent — assembles complete parts builds per (variant, colour).

Pipeline:
    Stage 1+2  existing YamahaCatalogueExtractor → rows, variants, colour_codes
    Stage 3    colour roster per variant (which colours appear for each variant)
    Stage 5    assembly engine — per (variant, colour): shared + colour-specific parts
    Stage 6    serialize to AgentResult / JSON

The assembly logic implements the remark grammar from remark_parser.py so that
each colour build is a *complete motorcycle* — shared parts plus the correct
colour-specific parts for that colour, with no duplicates by REF.NO.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.models.master_data.pdf_catalogue_extractor import YamahaCatalogueExtractor
from src.models.master_data.remark_parser import parse_applicability

# ---------------------------------------------------------------------------
# Web colour extraction helpers (module-level, compiled once)
# ---------------------------------------------------------------------------

# Colour words that anchor a phrase as a "colour name"
_WEB_COLOUR_NOUNS = frozenset([
    "black", "white", "blue", "red", "green", "yellow", "silver", "gray",
    "grey", "orange", "gold", "cyan", "brown", "purple", "violet", "pink",
    "cream", "vermillion", "champagne", "magenta", "maroon", "indigo",
])
_WEB_COLOUR_MODIFIERS = frozenset([
    "metallic", "matte", "matt", "mat", "vivid", "deep", "light", "dark",
    "solid", "bright", "pearl", "candy", "flat", "grayish", "greyish",
    "purplish", "bluish", "reddish", "greenish", "leaf", "ice", "racing",
    "fluo", "neon", "chromatic", "luminous",
])
# A "colour phrase" is 2-6 capitalized-or-all-caps tokens that contain at
# least one colour noun
_WEB_PHRASE_PAT = re.compile(
    r'\b([A-Z][A-Za-z0-9]+(?:[\s\-][A-Z][A-Za-z0-9]+){1,5})\b'
)
# Stopwords to strip when normalising colour names for fuzzy comparison
_NORM_STOP = frozenset({"the", "a", "an", "and", "or", "of", "for", "with", "no", "new"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ColourEntry:
    abbreviation: str
    name: str
    code: str           # paint code e.g. "0903"
    is_model_colour: bool


@dataclass
class PartRow:
    figure: str
    ref_no: str
    part_no: str
    description: str
    qty: str
    remarks: str
    kind: str           # "shared" | "colour_specific"


@dataclass
class VariantBuild:
    variant: str
    colour: str
    colour_name: str
    colour_code: str
    part_count: int
    parts: list[PartRow] = field(default_factory=list)


@dataclass
class AgentResult:
    source_pdf: str
    extracted_at: str
    model: str
    manufacture_year: str | None
    variants: list[str]
    colour_legend: dict[str, dict]   # abbr → {name, code, is_model_colour, web_confirmed, has_external_parts}
    rosters: dict[str, list[str]]    # variant → [colour_abbrs]
    builds: list[VariantBuild]
    # Abbreviations confirmed by web search AND having ≥1 external (colour-specific) part.
    # The colour strip in the UI shows ONLY these. Empty = web search unavailable → show all.
    validated_colours: list[str]
    web_colour_names: list[str]       # raw colour names returned by web search
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert VariantBuild list explicitly so PartRow dataclasses serialize
        d["builds"] = [
            {
                **{k: v for k, v in asdict(b).items() if k != "parts"},
                "parts": [asdict(p) for p in b.parts],
            }
            for b in self.builds
        ]
        return d


# ---------------------------------------------------------------------------
# Qty helpers (mirrors the frontend variantQty function)
# ---------------------------------------------------------------------------

def _variant_qty(qty: str, idx: int, num_variants: int) -> str:
    """Extract per-variant qty from slash-encoded string.

    For single-variant PDFs qty is a plain string (e.g. "1").
    For multi-variant PDFs qty is slash-encoded: "1//1/" where each slot
    corresponds to one variant column, padded from the right.
    """
    parts = qty.split("/")
    if len(parts) == 1:
        return qty.strip()
    offset = max(0, len(parts) - num_variants)
    raw = parts[offset + idx] if (offset + idx) < len(parts) else ""
    return raw.strip()


def _has_qty(qty_val: str) -> bool:
    return bool(qty_val and qty_val.strip() not in ("", "0"))


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------

class CatalogueAgent:
    """Run the six-stage catalogue pipeline and return an AgentResult."""

    def __init__(self, max_pages: int = 500) -> None:
        self._extractor = YamahaCatalogueExtractor(max_pages=max_pages)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, pdf_path: Path) -> AgentResult:
        """Extract and assemble a complete AgentResult for one PDF.

        Args:
            pdf_path: Absolute path to the Yamaha parts catalogue PDF.

        Returns:
            AgentResult with model, variants, colour legend, rosters, and builds.
        """
        logger.info(f"CatalogueAgent: starting {pdf_path.name}")

        # ── Stage 1 + 2 ── existing extractor ──────────────────────────
        result = self._extractor.extract(pdf_path)
        if result.error:
            raise RuntimeError(f"Extraction failed: {result.error}")

        variants: list[str] = result.variants or []
        colour_codes: list[dict] = result.colour_codes or []
        rows: list[dict] = result.rows or []

        colour_abbrs: set[str] = {c["abbreviation"].upper() for c in colour_codes}
        colour_legend: dict[str, dict] = {
            c["abbreviation"].upper(): c for c in colour_codes
        }

        logger.info(
            f"{pdf_path.name}: {len(rows)} rows, "
            f"{len(variants)} variants, {len(colour_codes)} colours"
        )

        # ── Stage 3 ── colour roster per variant ───────────────────────
        rosters = self._build_rosters(rows, variants, colour_abbrs)

        # ── Stage 5 ── assemble builds ─────────────────────────────────
        builds = self._assemble_builds(rows, variants, colour_legend, rosters, colour_abbrs)

        warnings = list(result.warnings)
        if not colour_codes:
            warnings.append("No colour table found in foreword; colour builds unavailable.")

        # ── Stage 4 ── web validation + external-parts check ───────────
        # If the PDF already has an "AVAILABLE COLOUR" page, use those captions
        # directly as the colour-name reference — no web search needed.
        pdf_available_colours = result.available_colours or []
        if pdf_available_colours:
            web_colour_names = pdf_available_colours
            logger.info(
                f"{pdf_path.name}: skipping web search — "
                f"using {len(web_colour_names)} colour(s) from PDF available-colour page"
            )
        else:
            web_colour_names = self._search_web_colours(result.model, result.manufacture_year)
            if not web_colour_names:
                warnings.append("Web colour search returned no results; showing all PDF colours.")
                logger.warning(f"{pdf_path.name}: web colour search empty")
            else:
                logger.info(f"{pdf_path.name}: web found {len(web_colour_names)} colour name(s)")

        # Which colour abbreviations have ≥1 external (colour_specific) part
        colours_with_external: set[str] = {
            b.colour for b in builds
            if any(p.kind == "colour_specific" for p in b.parts)
        }

        # Annotate the colour legend with web_confirmed + has_external_parts
        validated_colours: list[str] = []
        for abbr, entry in colour_legend.items():
            pdf_name = entry.get("name", "")
            web_ok = (
                not web_colour_names  # no web results → treat all as confirmed
                or self._web_confirms_colour(pdf_name, web_colour_names)
            )
            ext_ok = abbr in colours_with_external
            colour_legend[abbr] = {
                **entry,
                "web_confirmed": web_ok,
                "has_external_parts": ext_ok,
            }
            if web_ok and ext_ok:
                validated_colours.append(abbr)

        return AgentResult(
            source_pdf=pdf_path.name,
            extracted_at=datetime.now(tz=timezone.utc).isoformat(),
            model=result.model,
            manufacture_year=result.manufacture_year,
            variants=variants,
            colour_legend=colour_legend,
            rosters={v: list(cs) for v, cs in rosters.items()},
            builds=builds,
            validated_colours=validated_colours,
            web_colour_names=web_colour_names,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Stage 3 — colour roster per variant
    # ------------------------------------------------------------------

    def _build_rosters(
        self,
        rows: list[dict],
        variants: list[str],
        colour_abbrs: set[str],
    ) -> dict[str, set[str]]:
        """Return {variant_code: set(colour_abbrs)} from REMARKS across all rows.

        For every row with a non-zero qty in a variant column, parse the
        remarks.  If colour codes are found → those colours are available for
        that variant.
        """
        rosters: dict[str, set[str]] = {v: set() for v in variants}
        num_v = len(variants)

        for row in rows:
            remarks = row.get("remarks", "") or ""
            appl = parse_applicability(remarks, colour_abbrs)
            if appl.is_universal:
                continue  # shared row — no colour info for the roster

            qty_raw = row.get("qty", "") or ""

            for idx, variant in enumerate(variants):
                v_qty = _variant_qty(qty_raw, idx, num_v) if num_v > 1 else qty_raw
                if not _has_qty(v_qty):
                    continue
                # This row applies to this variant — record the colour codes
                if appl.is_except_mode:
                    # "EXCEPT FOR C" — the variant has all colours except C
                    # We don't know the full colour set yet so defer; add all
                    # colours that ARE explicitly found from other rows instead.
                    pass
                else:
                    rosters[variant].update(appl.applies_to)

        return rosters

    # ------------------------------------------------------------------
    # Stage 5 — assemble complete builds per (variant, colour)
    # ------------------------------------------------------------------

    def _assemble_builds(
        self,
        rows: list[dict],
        variants: list[str],
        colour_legend: dict[str, dict],
        rosters: dict[str, set[str]],
        colour_abbrs: set[str],
    ) -> list[VariantBuild]:
        """Build complete parts list per (variant, colour).

        For each variant, for each colour in that variant's roster:
        1. Shared parts     — rows where the variant has qty and no colour code
                              in remarks → always included.
        2. Colour-specific  — rows where remarks resolves to include THIS colour.
        3. Dedup by REF.NO  — when several rows share one ref_no (same balloon
                              number, different colour options) pick exactly the
                              one matching the selected colour.
        """
        num_v = len(variants)
        builds: list[VariantBuild] = []

        for idx, variant in enumerate(variants):
            colours = rosters.get(variant, set())
            if not colours and not colour_legend:
                # Single-colour catalogue — make one build with all rows
                colours = {"_ALL"}

            for colour in sorted(colours):
                legend_entry = colour_legend.get(colour, {})
                colour_name = legend_entry.get("name", colour)
                colour_code = legend_entry.get("code", "")

                parts: list[PartRow] = []
                # Track ref_no → list[PartRow] for dedup
                by_ref: dict[str, list[PartRow]] = {}

                for row in rows:
                    qty_raw = row.get("qty", "") or ""
                    v_qty = _variant_qty(qty_raw, idx, num_v) if num_v > 1 else qty_raw
                    if not _has_qty(v_qty):
                        continue  # this variant has no qty for this row

                    remarks = row.get("remarks", "") or ""
                    appl = parse_applicability(remarks, colour_abbrs)

                    if colour == "_ALL":
                        kind = "shared"
                        matches = True
                    elif appl.is_universal:
                        kind = "shared"
                        matches = True
                    else:
                        kind = "colour_specific"
                        matches = appl.matches(colour)

                    if not matches:
                        continue

                    pr = PartRow(
                        figure=row.get("section", "") or "",
                        ref_no=row.get("ref_no", "") or "",
                        part_no=row.get("part_no", "") or "",
                        description=row.get("description", "") or "",
                        qty=v_qty,
                        remarks=remarks,
                        kind=kind,
                    )

                    ref = pr.ref_no or pr.part_no
                    by_ref.setdefault(ref, []).append(pr)

                # Dedup: for rows that share a ref_no, prefer colour_specific
                # over shared; among colour_specific rows pick the one that
                # exactly matches (already filtered above, so all are valid).
                seen_refs: set[str] = set()
                for ref, candidates in by_ref.items():
                    # Prefer colour_specific over shared if both exist
                    specific = [p for p in candidates if p.kind == "colour_specific"]
                    chosen = specific[0] if specific else candidates[0]
                    parts.append(chosen)
                    seen_refs.add(ref)

                builds.append(VariantBuild(
                    variant=variant,
                    colour=colour if colour != "_ALL" else "",
                    colour_name=colour_name if colour != "_ALL" else "All colours",
                    colour_code=colour_code,
                    part_count=len(parts),
                    parts=parts,
                ))

        return builds

    # ------------------------------------------------------------------
    # Web colour validation helpers
    # ------------------------------------------------------------------

    def _search_web_colours(self, model: str, year: str | None) -> list[str]:
        """DuckDuckGo HTML search → colour names mentioned for this model/year.

        Uses only stdlib (urllib) — no external dependencies.
        Returns a list of candidate colour-name strings (may be empty if the
        request fails or no colour phrases are found).
        """
        query_parts = [model, "color", "colour", "variant"]
        if year:
            query_parts.insert(1, year)
        q = urllib.parse.urlencode({"q": " ".join(query_parts), "ia": "web"})
        url = f"https://html.duckduckgo.com/html/?{q}"

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Web colour search failed for {model!r}: {exc}")
            return []

        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", html)
        return self._extract_colour_names_from_text(text)

    @staticmethod
    def _extract_colour_names_from_text(text: str) -> list[str]:
        """Extract colour-name phrases from a block of text.

        A phrase qualifies when it contains at least one word from
        _WEB_COLOUR_NOUNS (with optional _WEB_COLOUR_MODIFIERS).
        Returns deduplicated names in discovery order.
        """
        seen: set[str] = set()
        results: list[str] = []
        for m in _WEB_PHRASE_PAT.finditer(text):
            phrase = m.group(1).strip()
            words_lower = [w.lower() for w in re.split(r"[\s\-]+", phrase)]
            # Must contain at least one colour noun
            if not any(w in _WEB_COLOUR_NOUNS for w in words_lower):
                continue
            # All words must be colour nouns, modifiers, or short (≤2 char) tokens
            meaningful = [
                w for w in words_lower
                if w not in _NORM_STOP and len(w) > 2
            ]
            if not meaningful:
                continue
            key = phrase.lower()
            if key not in seen:
                seen.add(key)
                results.append(phrase)
        return results

    @staticmethod
    def _web_confirms_colour(pdf_colour_name: str, web_names: list[str]) -> bool:
        """Return True if the PDF colour name overlaps sufficiently with any web name.

        Uses a word-overlap ratio: if ≥40% of the meaningful words in
        pdf_colour_name appear in a web name, it's considered confirmed.
        """
        if not web_names:
            return True  # no web data → don't filter anything

        def _words(s: str) -> set[str]:
            return {
                w.lower() for w in re.split(r"[\s\-_]+", s)
                if len(w) > 2 and w.lower() not in _NORM_STOP
            }

        pdf_words = _words(pdf_colour_name)
        if not pdf_words:
            return True  # can't judge → include

        for web_name in web_names:
            web_words = _words(web_name)
            overlap = pdf_words & web_words
            ratio = len(overlap) / len(pdf_words)
            if ratio >= 0.40:
                return True
        return False
