"""Pandera schemas for colour extraction and validation.

Validates:
- Colour legend entries (abbreviation, name, code)
- Available colour captions
- Colour matching results
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import pandera as pa
from pandera import Column, DataFrameModel, Field, Index, ValidationError
from pandera.typing import DataFrame


# Colour noun validators (reuse from catalogue_agent)
_CAPTION_COLOUR_NOUNS = frozenset({
    "black", "white", "blue", "red", "green", "yellow", "silver", "gray",
    "grey", "orange", "gold", "cyan", "brown", "purple", "violet", "pink",
    "cream", "champagne", "magenta", "maroon", "metallic", "matte", "matt",
    "mat", "vivid", "racing", "bright", "dull", "navy", "cobalt", "candy",
    "sparkle", "pearl", "bluish", "purplish", "reddish", "greenish",
})


class ColourLegendSchema(DataFrameModel):
    """Schema for colour legend entries extracted from PDF foreword.

    Each row represents one colour code that applies to a motorcycle model.
    """

    abbreviation: Series[str] = Field(str, description="2-8 alphanumeric colour code (e.g., SMX, DBNM8)")
    name: Series[str] = Field(str, description="Human-readable colour name (≥3 chars, should contain colour noun)")
    code: Series[str] = Field(str, description="Paint code (3-5 alphanumeric) or empty string")
    is_model_colour: Series[bool] = Field(bool, description="True if marked as primary colour for this model")

    class Config:
        """Pandera config."""
        strict = False
        coerce = True

    @pa.check("abbreviation")
    def abbreviation_valid(cls, series: pd.Series) -> pd.Series:
        """Validate abbreviation format: 2-8 alphanumeric chars."""
        return series.str.match(r"^[A-Z0-9]{2,8}$")

    @pa.check("name")
    def name_has_minimum_length(cls, series: pd.Series) -> pd.Series:
        """Validate name has at least 3 characters."""
        return series.str.len() >= 3

    @pa.check("name")
    def name_contains_colour_noun(cls, series: pd.Series) -> pd.Series:
        """Validate that name contains at least one colour noun."""
        def has_colour_noun(text: str) -> bool:
            if not text:
                return False
            words = text.lower().split()
            return any(w in _CAPTION_COLOUR_NOUNS for w in words)

        return series.apply(has_colour_noun)

    @pa.check("code")
    def code_valid_format(cls, series: pd.Series) -> pd.Series:
        """Validate paint code format: 3-5 alphanumeric or empty."""
        return series.apply(
            lambda x: (
                (isinstance(x, str) and (len(x) == 0 or re.match(r"^[A-Z0-9]{3,5}$", x)))
                or pd.isna(x)
            )
        )


class AvailableColoursSchema(DataFrameModel):
    """Schema for available colour captions extracted from PDF cover pages."""

    caption: Series[str] = Field(str, description="Colour caption text (e.g., 'Yamaha Black', '2SP3-Gold')")
    matched_abbr: Series[str] = Field(str, description="Matched colour abbreviation or empty if no match")
    match_score: Series[float] = Field(float, ge=0.0, le=1.0, description="Match confidence score (0.0-1.0)")

    class Config:
        """Pandera config."""
        strict = False
        coerce = True

    @pa.check("caption")
    def caption_not_empty(cls, series: pd.Series) -> pd.Series:
        """Validate caption is not empty."""
        return series.str.len() > 0

    @pa.check("caption")
    def caption_reasonable_length(cls, series: pd.Series) -> pd.Series:
        """Validate caption length (<= 100 chars, <= 8 words)."""
        return (series.str.len() <= 100) & (series.str.split().str.len() <= 8)

    @pa.check("caption")
    def caption_contains_colour_words(cls, series: pd.Series) -> pd.Series:
        """Validate that caption contains at least one colour noun."""
        def has_colour_noun(text: str) -> bool:
            if not text:
                return False
            words = text.lower().split()
            return any(w in _CAPTION_COLOUR_NOUNS for w in words)

        return series.apply(has_colour_noun)

    @pa.check("matched_abbr")
    def matched_abbr_valid_format(cls, series: pd.Series) -> pd.Series:
        """Validate matched abbreviation format: 2-8 alphanumeric or empty."""
        return series.apply(
            lambda x: (
                (isinstance(x, str) and (len(x) == 0 or re.match(r"^[A-Z0-9]{2,8}$", x)))
                or pd.isna(x)
            )
        )


class ColourRosterSchema(DataFrameModel):
    """Schema for colour roster (variant -> colours mapping)."""

    variant: Series[str] = Field(str, description="Variant code (e.g., B65J, B65L)")
    colours: Series[str] = Field(
        str,
        description="Comma-separated colour abbreviations (e.g., 'SMX,DBNM8,YB')"
    )

    class Config:
        """Pandera config."""
        strict = False
        coerce = True

    @pa.check("variant")
    def variant_valid_format(cls, series: pd.Series) -> pd.Series:
        """Validate variant format: 3-5 alphanumeric with at least one digit."""
        def is_valid_variant(text: str) -> bool:
            return (
                isinstance(text, str)
                and 3 <= len(text) <= 5
                and text.isalnum()
                and any(c.isdigit() for c in text)
            )

        return series.apply(is_valid_variant)

    @pa.check("colours")
    def colours_not_empty(cls, series: pd.Series) -> pd.Series:
        """Validate colours field is not empty."""
        return series.str.len() > 0

    @pa.check("colours")
    def colours_valid_abbreviations(cls, series: pd.Series) -> pd.Series:
        """Validate all comma-separated colours are valid abbreviations."""
        def all_valid(text: str) -> bool:
            if not text:
                return False
            abbrs = [a.strip() for a in text.split(",")]
            return all(re.match(r"^[A-Z0-9]{2,8}$", a) for a in abbrs if a)

        return series.apply(all_valid)


def validate_colour_legend(df: pd.DataFrame) -> None:
    """Validate colour legend DataFrame against schema.

    Args:
        df: DataFrame with columns [abbreviation, name, code, is_model_colour]

    Raises:
        ValidationError: If validation fails
    """
    try:
        ColourLegendSchema.validate(df, lazy=False)
    except ValidationError as e:
        raise ValidationError(
            f"Colour legend validation failed: {e}",
            failure_cases=e.failure_cases,
        ) from e


def validate_available_colours(df: pd.DataFrame) -> None:
    """Validate available colours DataFrame against schema.

    Args:
        df: DataFrame with columns [caption, matched_abbr, match_score]

    Raises:
        ValidationError: If validation fails
    """
    try:
        AvailableColoursSchema.validate(df, lazy=False)
    except ValidationError as e:
        raise ValidationError(
            f"Available colours validation failed: {e}",
            failure_cases=e.failure_cases,
        ) from e


def validate_colour_roster(df: pd.DataFrame) -> None:
    """Validate colour roster DataFrame against schema.

    Args:
        df: DataFrame with columns [variant, colours]

    Raises:
        ValidationError: If validation fails
    """
    try:
        ColourRosterSchema.validate(df, lazy=False)
    except ValidationError as e:
        raise ValidationError(
            f"Colour roster validation failed: {e}",
            failure_cases=e.failure_cases,
        ) from e
