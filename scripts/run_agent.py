"""CLI entry point for the Catalogue Agent.

Usage:
    python -m scripts.run_agent <pdf_path> [--refresh]

Examples:
    python -m scripts.run_agent data/raw/pdf_catalogues/AEROX/1UB65460EV-AEROX.pdf
    python -m scripts.run_agent data/raw/pdf_catalogues/RAY/Ray_ZR_B627.pdf --refresh

Output:
    data/outputs/agent_builds/<pdf_stem>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on the path when run as -m scripts.run_agent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from src.models.master_data.catalogue_agent import CatalogueAgent

CACHE_DIR = Path("data/outputs/agent_builds")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Yamaha Catalogue Agent on a PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the Yamaha parts catalogue PDF")
    parser.add_argument(
        "--refresh", action="store_true",
        help="Force re-run even if a cached result exists",
    )
    parser.add_argument(
        "--max-pages", type=int, default=500,
        help="Maximum pages to scan (default: 500)",
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf.resolve()
    if not pdf_path.exists():
        logger.error(f"File not found: {pdf_path}")
        sys.exit(1)

    safe_name = pdf_path.stem.replace(" ", "_")
    out_path = CACHE_DIR / f"{safe_name}.json"

    if out_path.exists() and not args.refresh:
        logger.info(f"Cached result found: {out_path}  (use --refresh to re-run)")
        result = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        logger.info(f"Running agent on: {pdf_path.name}")
        agent = CatalogueAgent(max_pages=args.max_pages)
        result = agent.run(pdf_path).to_dict()

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.success(f"Saved: {out_path}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"Model   : {result.get('model', '?')}")
    print(f"Variants: {', '.join(result.get('variants', []))}")
    colours = result.get("colour_legend", {})
    print(f"Colours : {len(colours)} ({', '.join(list(colours.keys())[:6])}{'...' if len(colours) > 6 else ''})")
    builds = result.get("builds", [])
    print(f"Builds  : {len(builds)}")
    for b in builds:
        print(f"  {b['variant']:6s} / {b['colour']:8s} {b['colour_name'][:30]:30s}  {b['part_count']} parts")
    warnings = result.get("warnings", [])
    if warnings:
        print(f"\nWarnings:")
        for w in warnings:
            print(f"  ! {w}")
    print(f"{'='*60}")
    print(f"Output  : {out_path}")


if __name__ == "__main__":
    main()
