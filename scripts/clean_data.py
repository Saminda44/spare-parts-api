"""CLI entry point: python -m scripts.clean_data [--force]

Cleans all registered raw Excel sources and saves them as Parquet files
under data/interim/.  Pass --force to re-clean files that already have a
cached Parquet (useful after the raw files are updated).

Usage
-----
    # First run (or after raw files change):
    .venv\\Scripts\\python.exe -m scripts.clean_data --force

    # Subsequent runs (skips already-cached files):
    .venv\\Scripts\\python.exe -m scripts.clean_data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python -m scripts.clean_data` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reconfigure stdout so Windows terminals don't choke on Unicode log output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from src.ingestion.cleaner import EXCEL_SOURCES, clean_all, load_clean  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean all raw Excel sources → data/interim/*.parquet"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-clean even if a Parquet cache already exists.",
    )
    args = parser.parse_args()

    print("\nCleaning raw Excel sources...")
    print(f"  force={args.force}\n")

    results = clean_all(force=args.force)

    # Also load and report on any cached files that were skipped
    all_names = list(EXCEL_SOURCES.keys())

    print(f"\n{'Source':<20} {'Rows':>10} {'Cols':>6}  {'Status'}")
    print("-" * 52)

    for name in all_names:
        if name in results:
            df = results[name]
            status = "cleaned"
        else:
            try:
                df = load_clean(name)
                status = "cached"
            except FileNotFoundError:
                print(f"  {name:<18} {'—':>10} {'—':>6}  MISSING (not cleaned yet)")
                continue

        print(f"  {name:<18} {df.shape[0]:>10,} {df.shape[1]:>6}  {status}")

    n_cleaned = len(results)
    n_total = len(all_names)
    print(f"\n{n_cleaned}/{n_total} file(s) cleaned this run.")
    if n_cleaned < n_total:
        print(f"{n_total - n_cleaned} file(s) served from cache (pass --force to re-clean).")


if __name__ == "__main__":
    main()
