"""CLI — Stage 6.x: extract cross-model part compatibility from all PDF catalogues.

For every PDF under data/raw/pdf_catalogues/:
  1. Word-position extraction (existing YamahaCatalogueExtractor).
  2. LLM fallback (Claude) for PDFs whose yield is below --threshold.
  3. Cross-model rollup: part_no → compatible_models + model_years.

Output:
    data/outputs/model_compatibility.xlsx   (Excel, human-readable)
    data/outputs/model_compatibility.parquet (Parquet, downstream use)

Usage::

    python -m scripts.extract_model_compatibility
    python -m scripts.extract_model_compatibility --model "FZ & FZS"
    python -m scripts.extract_model_compatibility --threshold 0.5 --workers 8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

PDF_ROOT = Path("data/raw/pdf_catalogues")
OUT_PATH = Path("data/outputs/model_compatibility.xlsx")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract cross-model part compatibility from Yamaha PDF catalogues."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Only process PDFs under this model sub-folder (e.g. 'FZ & FZS')",
    )
    parser.add_argument(
        "--out",
        default=str(OUT_PATH),
        help="Output Excel path (a sibling .parquet is written automatically)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.70,
        help="Word-position yield ratio below which LLM fallback is used (default 0.70)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel PDF workers (default 4)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=500,
        help="Max pages per PDF for word-position extractor (default 500)",
    )
    parser.add_argument(
        "--llm-model",
        default="claude-sonnet-4-6",
        help="Claude model for LLM fallback (default claude-sonnet-4-6)",
    )
    args = parser.parse_args()

    from src.models.master_data.catalogue_agent import CrossModelCompatibilityAgent

    root = PDF_ROOT / args.model if args.model else PDF_ROOT
    if not root.exists():
        logger.error(f"PDF root not found: {root}")
        sys.exit(1)

    out_path = Path(args.out)

    agent = CrossModelCompatibilityAgent(
        max_pages=args.pages,
        llm_model=args.llm_model,
        yield_threshold=args.threshold,
    )

    logger.info(f"Starting cross-model compatibility extraction: {root}")
    df = agent.run_all(root, max_workers=args.workers)

    if df.empty:
        logger.warning("No parts extracted — check PDF root and logs.")
        sys.exit(1)

    agent.save(df, out_path)

    print(f"\n{'='*60}")
    print(f"  Unique parts      : {len(df):,}")
    print(f"  Models covered    : {df['compatible_models'].explode().nunique()}")
    multi = (df["compatible_models"].apply(len) > 1).sum()
    print(f"  Cross-model parts : {multi:,}  ({multi/len(df):.1%} of total)")
    print(f"  Excel output      : {out_path}")
    print(f"  Parquet output    : {out_path.with_suffix('.parquet')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
