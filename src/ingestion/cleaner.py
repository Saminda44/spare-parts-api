"""Universal Excel cleaner — runs before any module ingests data.

Every raw Excel file passes through :func:`clean_excel` before any business
logic touches it.  The three invariants enforced here:

1. First row is always the column header (``header=0``).
2. Fully-empty columns (all NaN) are dropped.
3. Fully-empty rows (all NaN) are dropped and the index is reset.

Cleaned DataFrames are cached to ``data/interim/<name>.parquet``.
Downstream modules load them via :func:`load_clean` rather than re-reading
the raw Excel file each time.

``data/raw/`` is never modified — it is immutable source.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from src.config.paths import DATA_INTERIM, DATA_RAW

# ---------------------------------------------------------------------------
# pyarrow-safe type coercion
# ---------------------------------------------------------------------------

# infer_dtype labels that cannot be serialised as a single Arrow column type
_MIXED_DTYPE_LABELS: frozenset[str] = frozenset(
    {"mixed", "mixed-integer", "mixed-integer-float", "bytes"}
)


def _coerce_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Convert mixed-type or bytes object columns to nullable string.

    Business meaning: raw Excel files often contain columns whose values are
    a mixture of integers and strings (e.g. ``Dealer Code`` holds both numeric
    codes and the sentinel string 'No Code ').  pyarrow cannot infer a single
    Arrow type for such columns, causing ``to_parquet`` to fail.  Coercing to
    string is safe for the cleaning layer — downstream modules apply their own
    casts after loading.

    Args:
        df: DataFrame whose object columns may contain mixed types.

    Returns:
        New DataFrame with mixed-type object columns cast to ``object`` dtype
        of Python ``str`` values, with original NaN positions restored to
        ``None`` (null in Parquet).
    """
    df = df.copy()
    for col in df.select_dtypes(include="object").columns:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        inferred = pd.api.types.infer_dtype(non_null, skipna=True)
        if inferred in _MIXED_DTYPE_LABELS:
            mask_na = df[col].isna()
            df[col] = df[col].astype(str)
            df.loc[mask_na, col] = None  # restore nulls (avoids 'nan' strings)
            logger.debug(f"  coerced '{col}' (inferred={inferred!r}) → str")
    return df

# ---------------------------------------------------------------------------
# Source registry
# Logical name → filename inside DATA_RAW
# ---------------------------------------------------------------------------
EXCEL_SOURCES: dict[str, str] = {
    "mcsi": "MCSI.xlsx",
    "in_and_out": "In_and_Out.xlsx",
    "current_stock": "current stock.xlsx",
    "orders": "orders.xlsx",
    "sales": "sales.xlsx",
    "uio": "UIO.xlsx",
    "dealers": "dealers.xlsx",
    "ssop": "SSOP.xlsx",
}

# Files large enough to warrant an explicit engine call (avoids xlrd being
# picked for .xlsx files on some installs).
_LARGE_FILES: frozenset[str] = frozenset({"mcsi", "in_and_out", "orders", "sales"})


# ---------------------------------------------------------------------------
# Core cleaning function
# ---------------------------------------------------------------------------


def clean_excel(path: Path, sheet_name: int | str = 0) -> pd.DataFrame:
    """Read an Excel file and return a cleaned DataFrame.

    Business meaning: enforces the three data-quality invariants agreed for
    every raw source file before any module logic runs.

    Args:
        path: Absolute path to the ``.xlsx`` file.
        sheet_name: Sheet index or name.  Defaults to the first sheet (0).

    Returns:
        Cleaned DataFrame with:
        - Column names stripped of leading/trailing whitespace.
        - Fully-empty columns removed.
        - Fully-empty rows removed and index reset to 0-based RangeIndex.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Raw file not found: {path}")

    logger.debug(f"Reading: {path.name}")
    df: pd.DataFrame = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=0,          # rule 1: first row is always the header
        engine="openpyxl",
    )

    before_shape = df.shape

    # Rule 2: drop fully-empty columns
    df = df.dropna(how="all", axis=1)

    # Strip whitespace from column names (catches " Column " artefacts)
    df.columns = df.columns.str.strip()

    # Rule 3: drop fully-empty rows and reset the index
    df = df.dropna(how="all", axis=0).reset_index(drop=True)

    after_shape = df.shape
    logger.info(
        f"{path.name}: {before_shape[0]:,}r × {before_shape[1]}c  →  "
        f"{after_shape[0]:,}r × {after_shape[1]}c  "
        f"(dropped {before_shape[0] - after_shape[0]:,} rows, "
        f"{before_shape[1] - after_shape[1]} cols)"
    )

    # Ensure all object columns are Parquet-safe (coerce mixed types to str)
    df = _coerce_for_parquet(df)

    return df


# ---------------------------------------------------------------------------
# Batch cleaner
# ---------------------------------------------------------------------------


def clean_all(force: bool = False) -> dict[str, pd.DataFrame]:
    """Clean all registered Excel sources and cache them as Parquet.

    Skips a source whose ``.parquet`` already exists in ``data/interim/``
    unless ``force=True``.

    Args:
        force: When ``True``, re-cleans and overwrites existing Parquet files.

    Returns:
        Mapping of logical name → cleaned DataFrame for every source that was
        processed (skipped files are *not* included in the return value, but
        their existing Parquet files remain available via :func:`load_clean`).
    """
    DATA_INTERIM.mkdir(parents=True, exist_ok=True)

    results: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    for name, filename in EXCEL_SOURCES.items():
        parquet_path = DATA_INTERIM / f"{name}.parquet"

        if parquet_path.exists() and not force:
            logger.info(f"{name}: parquet already exists — skipping (use force=True to re-clean)")
            skipped.append(name)
            continue

        raw_path = DATA_RAW / filename
        try:
            df = clean_excel(raw_path)
            df.to_parquet(parquet_path, index=False)
            logger.info(f"{name}: saved → {parquet_path.name}")
            results[name] = df
        except FileNotFoundError:
            logger.warning(f"{name}: raw file not found at {raw_path} — skipping")
            failed.append((name, f"FileNotFoundError: {raw_path}"))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{name}: failed to clean — {exc}")
            failed.append((name, str(exc)))

    # Summary log
    logger.info(
        f"clean_all complete: {len(results)} cleaned, "
        f"{len(skipped)} skipped (cached), {len(failed)} failed"
    )
    if failed:
        for name, reason in failed:
            logger.warning(f"  FAILED — {name}: {reason}")

    return results


# ---------------------------------------------------------------------------
# Load helper
# ---------------------------------------------------------------------------


def load_clean(name: str) -> pd.DataFrame:
    """Load a previously cleaned file from ``data/interim/<name>.parquet``.

    Business meaning: downstream modules call this instead of re-reading raw
    Excel files, ensuring they always see the cleaned, validated data.

    Args:
        name: Logical source name (key in :data:`EXCEL_SOURCES`).

    Returns:
        DataFrame read from the cached Parquet file.

    Raises:
        KeyError: If ``name`` is not a registered source in :data:`EXCEL_SOURCES`.
        FileNotFoundError: If the Parquet cache does not yet exist
            (run :func:`clean_all` first).
    """
    if name not in EXCEL_SOURCES:
        raise KeyError(
            f"Unknown source '{name}'. Valid names: {sorted(EXCEL_SOURCES)}"
        )

    parquet_path = DATA_INTERIM / f"{name}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"Parquet cache not found for '{name}': {parquet_path}. "
            "Run clean_all() (or: python -m scripts.clean_data) first."
        )

    logger.debug(f"Loading cached clean data: {parquet_path.name}")
    return pd.read_parquet(parquet_path)
