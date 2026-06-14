"""Centralised path definitions. Import from here, never hardcode paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_RAW = ROOT / "data" / "raw"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_OUTPUTS = ROOT / "data" / "outputs"

DOCS = ROOT / "docs"
NOTEBOOKS = ROOT / "notebooks"
MLRUNS = ROOT / "mlruns"

# Ensure runtime dirs exist (raw is read-only; others can be created)
for _d in (DATA_INTERIM, DATA_PROCESSED, DATA_OUTPUTS, MLRUNS):
    _d.mkdir(parents=True, exist_ok=True)
