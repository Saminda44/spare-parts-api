"""Append-on-upload ingestion with hash-based deduplication.

Business rule (CLAUDE.md §11):
  1. Hash on natural-key columns.
  2. Insert only new rows.
  3. Log every run to data/interim/ingestion_log.parquet.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM

_LOG_PATH = DATA_INTERIM / "ingestion_log.parquet"


def _row_hash(df: pd.DataFrame, key_cols: list[str]) -> pd.Series:
    """Deterministic SHA-256 hash of natural-key columns."""
    combined = df[key_cols].astype(str).agg("|".join, axis=1)
    return combined.map(lambda s: hashlib.sha256(s.encode()).hexdigest())


def append_deduplicated(
    new_df: pd.DataFrame,
    existing_path: Path,
    key_cols: list[str],
    source_filename: str,
) -> pd.DataFrame:
    """Merge new_df into existing_path parquet using hash dedup. Returns combined df."""
    new_df = new_df.copy()
    new_df["_row_hash"] = _row_hash(new_df, key_cols)

    if existing_path.exists():
        existing = pd.read_parquet(existing_path)
        known_hashes = set(existing["_row_hash"])
    else:
        existing = pd.DataFrame()
        known_hashes = set()

    mask_new = ~new_df["_row_hash"].isin(known_hashes)
    inserted = new_df[mask_new]
    duplicates_skipped = len(new_df) - len(inserted)

    combined = pd.concat([existing, inserted], ignore_index=True) if len(existing) else inserted
    combined.to_parquet(existing_path, index=False)

    _write_log(source_filename, len(new_df), len(inserted), duplicates_skipped)
    logger.info(
        f"Ingestion {source_filename}: {len(new_df)} in, "
        f"{len(inserted)} inserted, {duplicates_skipped} duplicates skipped"
    )
    return combined


def _write_log(filename: str, rows_in: int, inserted: int, duplicates: int) -> None:
    record = pd.DataFrame([{
        "filename": filename,
        "rows_in": rows_in,
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "validation_failures": 0,
        "ingested_at": pd.Timestamp.utcnow(),
    }])
    if _LOG_PATH.exists():
        existing = pd.read_parquet(_LOG_PATH)
        pd.concat([existing, record], ignore_index=True).to_parquet(_LOG_PATH, index=False)
    else:
        record.to_parquet(_LOG_PATH, index=False)
