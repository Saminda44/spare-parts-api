"""Yamaha catalogue remark grammar parser.

Resolves colour applicability from a remarks string using the grammar defined
in the catalogue foreword. Used by the CatalogueAgent assembly engine.

Grammar (formal-ish):
    remark        := clause (";" clause)*
    clause        := (EXCEPT)? FOR? colourlist | freetext
    colourlist    := CODE ("," CODE)*
    CODE          := token present in foreword colour legend
    EXCEPT        := "EXCEPT" | "EXCEPT FOR"

Catalogue abbreviations (UR / UN / AP / LM) are stripped before colour matching.
A code appearing BEFORE "FOR" is the *part's own paint colour* and is ignored for
applicability determination — only codes AFTER "FOR" determine which bike colour
this part belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# Catalogue abbreviation tokens that prefix colour clauses — not colour codes
_ABBR_TOKENS = frozenset({"UR", "UN", "AP", "LM", "OPT", "STD"})

# Tokeniser: split on whitespace and commas, keep meaningful tokens
_SPLIT_PAT = re.compile(r"[\s,]+")


@dataclass
class ApplicabilityResult:
    """Colour applicability for a single parts-table row.

    Business meaning:
    - ``is_universal=True``  → part applies to ALL colours (no colour restriction).
    - ``is_except_mode=True`` → part applies to all colours EXCEPT those in ``applies_to``.
    - otherwise              → part applies only to the colours in ``applies_to``.
    """

    is_universal: bool
    applies_to: set[str] = field(default_factory=set)
    is_except_mode: bool = False

    def matches(self, colour: str) -> bool:
        """Return True if this row belongs to the given colour variant."""
        if self.is_universal:
            return True
        colour = colour.upper()
        if self.is_except_mode:
            return colour not in self.applies_to
        return colour in self.applies_to


_UNIVERSAL = ApplicabilityResult(is_universal=True)


def parse_applicability(remarks: str, colour_abbrs: set[str]) -> ApplicabilityResult:
    """Parse a remarks string and return its colour applicability.

    Args:
        remarks:      Raw remarks text from the parts table (e.g. ``"LLGS6 FOR DBNM8"``).
        colour_abbrs: Set of known colour abbreviations from the foreword legend
                      (upper-case, e.g. ``{"DBNM8", "MBL2", "YB", ...}``).

    Returns:
        ApplicabilityResult describing which colours this part applies to.
    """
    colour_abbrs = {c.upper() for c in colour_abbrs}

    if not remarks or not remarks.strip():
        return _UNIVERSAL

    upper = remarks.upper().strip()

    applies_codes: set[str] = set()
    except_codes: set[str] = set()
    has_restriction = False

    # Process each semicolon-delimited clause independently
    for clause in upper.split(";"):
        clause = clause.strip()
        if not clause:
            continue

        # --- EXCEPT FOR / EXCEPT pattern ---
        m = re.match(r"EXCEPT(?:\s+FOR)?\s+(.*)", clause)
        if m:
            tokens = [t for t in _SPLIT_PAT.split(m.group(1).strip()) if t]
            codes = [t for t in tokens if t in colour_abbrs]
            if codes:
                except_codes.update(codes)
                has_restriction = True
            continue

        # --- FOR pattern ---
        for_pos = clause.find(" FOR ")
        if for_pos >= 0:
            # Everything after " FOR " is the applicable colour list.
            # Everything before " FOR " is the part's own paint — discard.
            after_for = clause[for_pos + 5:].strip()
            tokens = [t for t in _SPLIT_PAT.split(after_for) if t]
            codes = [t for t in tokens if t in colour_abbrs]
            if codes:
                applies_codes.update(codes)
                has_restriction = True
            continue

        # --- Standalone colour code (no FOR, no EXCEPT) ---
        tokens = [t for t in _SPLIT_PAT.split(clause) if t]
        for token in tokens:
            if token in _ABBR_TOKENS:
                continue
            if token in colour_abbrs:
                applies_codes.add(token)
                has_restriction = True

    if not has_restriction:
        return _UNIVERSAL

    if except_codes and not applies_codes:
        return ApplicabilityResult(
            is_universal=False, applies_to=except_codes, is_except_mode=True
        )

    # If both except and applies (unusual), treat as direct inclusion
    return ApplicabilityResult(is_universal=False, applies_to=applies_codes, is_except_mode=False)
