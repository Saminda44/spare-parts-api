"""CLI: ingest a new monthly data file with hash-dedup.

Usage:
    python -m scripts.ingest --file data/raw/In_and_Out_2026-05.xlsx
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from loguru import logger

app = typer.Typer(help="Ingest a new raw data file (append-on-upload, hash-deduped).")

FILE_CONFIGS: dict[str, dict] = {
    "In_and_Out": {
        "key_cols": ["Sales Document", "Material", "Posting Date"],
        "output": "orders.parquet",
    },
    "MSCI": {
        "key_cols": ["VIN", "Posting Date", "Material", "SlsVolQty"],
        "output": "msci.parquet",
    },
}


@app.command()
def main(
    file: Annotated[Path, typer.Option("--file", help="Path to the raw Excel file")],
) -> None:
    if not file.exists():
        logger.error(f"File not found: {file}")
        raise typer.Exit(code=1)

    stem = file.stem.split("_")[0] if "_" in file.stem else file.stem
    config = FILE_CONFIGS.get(stem)
    if config is None:
        logger.warning(f"No ingest config for {stem!r}. Supported: {list(FILE_CONFIGS)}")
        raise typer.Exit(code=1)

    import pandas as pd
    from src.ingestion.ingest import append_deduplicated
    from src.config.paths import DATA_INTERIM

    logger.info(f"Loading {file}")
    df = pd.read_excel(file, engine="openpyxl")
    out = DATA_INTERIM / config["output"]
    append_deduplicated(df, out, config["key_cols"], file.name)
    logger.info(f"Done. Output: {out}")


if __name__ == "__main__":
    app()
