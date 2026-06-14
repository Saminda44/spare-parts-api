"""CLI — Stage 6.1: batch-extract all PDF catalogues to catalog_parts.parquet.

Usage::

    python -m scripts.extract_catalogues                    # all PDFs
    python -m scripts.extract_catalogues --model "FZ & FZS" # one model only
    python -m scripts.extract_catalogues --out data/interim/catalog_parts.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

PDF_ROOT   = Path("data/raw/pdf_catalogues")
OUT_PATH   = Path("data/interim/catalog_parts.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Yamaha PDF catalogue tables.")
    parser.add_argument("--model",  default=None, help="Only extract PDFs under this model folder")
    parser.add_argument("--out",    default=str(OUT_PATH), help="Output parquet path")
    parser.add_argument("--pages",  type=int, default=500, help="Max pages per PDF")
    args = parser.parse_args()

    from src.models.master_data.pdf_catalogue_extractor import YamahaCatalogueExtractor

    extractor = YamahaCatalogueExtractor(max_pages=args.pages)
    root = PDF_ROOT / args.model if args.model else PDF_ROOT

    if not root.exists():
        logger.error(f"PDF root not found: {root}")
        sys.exit(1)

    logger.info(f"Extracting catalogues from: {root}")
    df = extractor.extract_all(root, save_path=Path(args.out))

    print(f"\n{'='*60}")
    print(f"  Total rows        : {len(df):,}")
    print(f"  Distinct parts    : {df['part_no'].nunique():,}" if not df.empty else "  (empty)")
    print(f"  Models            : {df['model'].nunique()}" if not df.empty else "")
    print(f"  Output            : {args.out}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
