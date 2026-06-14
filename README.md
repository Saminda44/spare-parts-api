# Yamaha Spare Parts — Inventory Optimization System

Demand-forecasting and inventory-policy engine for a Yamaha motorcycle spare-parts distributor in Sri Lanka.

## Quick start

```powershell
# 1. Create virtual environment (Python 3.12)
uv venv --python 3.12

# 2. Install core dependencies
uv sync

# 3. Activate
.venv\Scripts\Activate.ps1

# 4. Copy env template and fill in values
Copy-Item .env.example .env

# 5. Verify
ruff check src/
mypy src/
pytest -q
```

## Run a pipeline stage

```powershell
python -m scripts.run_stage 1    # Stage 1: MSCI EDA
python -m scripts.run_stage 9    # Stage 9: ABC-XYZ-FSN classification
python -m scripts.run_stage 13   # Stage 13: Next shipment report
```

## Dashboard

```powershell
streamlit run src/dashboard/app.py
```

## Ingest a new monthly file

```powershell
python -m scripts.ingest --file data/raw/In_and_Out_2026-05.xlsx
```

## Pipeline stages

| Stage | Description |
|-------|-------------|
| 1 | MSCI EDA |
| 2 | Unit sales forecast |
| 3 | UIO forecast |
| 4 | Orders EDA |
| 5 | Sales EDA |
| 6 | Master data (catalog + supersession) |
| 7 | Stock movement |
| 8 | Spare parts EDA |
| 9 | ABC-XYZ-FSN classification |
| 10 | Demand forecast |
| 11 | Stock tracker |
| 12 | ROL / ROQ / Buffer policy |
| 13 | Next shipment report |
| 14 | RL system + dashboard |

See `docs/master_prompt.md` for full stage details.

## Tech stack

Python 3.12 · pandas · polars · scikit-learn · statsforecast · prophet · xgboost · lightgbm · pandera · MLflow · SQLAlchemy · Streamlit · loguru · pydantic-settings · ruff · mypy · pytest
