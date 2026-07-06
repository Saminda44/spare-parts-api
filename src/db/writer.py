"""Database write utilities for the spare-parts pipeline."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import create_engine, text

from src.config.settings import settings

if TYPE_CHECKING:
    import pandas as pd


def _get_commit_sha() -> str:
    """Return the current git commit SHA (short), or 'unknown' if git is unavailable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def save_catalog_parts(df: pd.DataFrame) -> None:
    """Write extracted catalog parts to PostgreSQL (interim.catalog_parts).

    Business meaning: full-refresh write of the PDF-extracted parts catalog to
    Postgres so downstream stages can read from DB instead of parquet files.
    Skips gracefully if DATA_BACKEND != 'postgres' or Postgres is unreachable.

    Args:
        df: DataFrame produced by YamahaCatalogueExtractor.extract_all().
    """
    if settings.data_backend != "postgres":
        logger.debug("DB catalog write skipped — DATA_BACKEND={}", settings.data_backend)
        return
    if not settings.postgres_password:
        logger.warning("DB catalog write skipped — postgres_password not configured in .env")
        return

    if df.empty:
        logger.warning("DB catalog write skipped — extraction produced an empty DataFrame")
        return

    try:
        engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)

        # Ensure the interim schema exists (idempotent)
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS interim"))

        # Attach CLAUDE.md §12 metadata footer columns
        out = df.copy()
        out["source_hash"] = ""  # populated by caller if source file hash is available
        out["code_commit_sha"] = _get_commit_sha()
        out["generated_at"] = datetime.now(tz=UTC)
        out["model_version"] = "stage06-pdf-extractor"

        out.to_sql(
            "catalog_parts",
            engine,
            schema="interim",
            if_exists="replace",  # catalog is a reference table — full refresh per extraction
            index=False,
            method="multi",
            chunksize=500,
        )
        logger.info("interim.catalog_parts written to PostgreSQL: {} rows", len(out))
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB catalog write failed (non-fatal, parquet is the primary store): {}", exc)
