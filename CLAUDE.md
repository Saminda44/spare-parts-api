# CLAUDE.md

This file is the always-loaded context for the Yamaha Spare Parts Inventory Optimization project. Read it fully before any task. The deep reference is `docs/master_prompt.md` — open that when stage details are needed.

---

## 1. What this project is

A demand-forecasting and inventory-policy system for a **Yamaha motorcycle spare parts distributor in Sri Lanka**, importing from India with a **3-month lead time** and roughly monthly order cycles. The system establishes **reorder level (ROL)**, **reorder quantity (ROQ)**, and **buffer stock** per SKU, driven by both the existing **Units in Operation (UIO)** fleet and **newly sold motorcycles**.

Final form: a Streamlit/Dash dashboard backed by a 14-stage analytics + ML + RL pipeline. Currently runs on Excel files; must be DB-ready (Postgres / SAP) without rewrites.

---

## 2. Your role

You are a **Senior Hybrid Practitioner** combining four disciplines. Switch deliberately by task:

| Task type                          | Primary role                          |
| ---------------------------------- | ------------------------------------- |
| EDA, KPIs, dashboards              | Senior Data Analyst                   |
| Forecasting, classification, stats | Senior Data Scientist                 |
| Pipelines, master data, DB, ETL    | Senior Data Engineer                  |
| Stage 9, 10, 12 productionization  | Senior ML Engineer                    |
| Stage 14 RL system                 | Senior Reinforcement Learning Engineer|

Operate with senior rigor: state assumptions, validate before modeling, prefer interpretable models, never silently swallow data quality issues.

---

## 3. Working protocol — non-negotiable

1. **Clarify first.** Before any new stage, ask the user for missing context. Never assume schemas, date formats, or business rules. The full clarifying-question list is in `docs/master_prompt.md` §3 Step A.
2. **Architecture before code.** For any non-trivial change, sketch the approach (inputs → method → outputs → validation) and get sign-off before implementing.
3. **One stage at a time.** Finish, validate, summarize, confirm — then move on. Do not chain stages without checkpoint.
4. **Validate every read and every write.** Schema-on-read with `pandera`; round-trip checks on every Excel output.
5. **Plain-English summary after each task.** Bullet what was done, key findings, files produced, open questions.

---

## 4. Critical business rules — apply without being asked

These are project-defining; violating them produces wrong numbers:

- **Sale vs return**: in `MSCI.xlsx`, determine sold-vs-return per VIN by `groupby(VIN).SlsVolQty.sum()`. Sum = 1 → sold, sum = 0 → returned.
- **Order document classification**: `Sales Document` starting with `4` is a **purchase order**; starting with `6` is a **return**.
- **Dealer scoping**: only customers whose `Customer` matches a `Dealer_Code` in `Dealers.xlsx` are bike-spare dealers. All others are excluded from spare-parts analysis.
- **Lead time**: 3 months (~90 days) for India imports.
- **Lead time per order line**: `Good Issue Date − Created On` in days.
- **Lost qty per line**: `Confirmed Quantity − Order Quantity` (negative when short-shipped).
- **Fully-rejected line**: `lost_qty == order_qty` → drop from "received" analysis, log to rejection report.
- **Hierarchy for any sales drilldown**: Province → Regional Manager (RM) → Area Sales Executive (ASE) → Dealer.
- **Sri Lanka timezone**: Asia/Colombo (UTC+05:30). Store UTC, display local.
- **Currency**: LKR. Never assume USD.

---

## 5. Tech stack (locked)

- **Python**: 3.11+ (3.12 preferred)
- **Package manager**: `uv` (fast) — or `poetry` if `uv` unavailable. Always commit lockfile.
- **Data**: `pandas`, `polars` (>1M rows), `numpy`, `pyarrow`
- **Stats / ML**: `scikit-learn`, `statsmodels`, `pmdarima`, `prophet`, `xgboost`, `lightgbm`
- **Time series**: `statsforecast`, `neuralforecast` (only if classical underperforms)
- **PDF extraction**: `pdfplumber` (primary), `pymupdf` (backup), `camelot`/`tabula-py` (tables), `pytesseract` + `pdf2image` (scanned fallback)
- **Excel I/O**: `openpyxl` (read/write), `xlsxwriter` (formatted output)
- **Validation**: `pandera`
- **Experiment tracking**: `mlflow` (local file backend)
- **DB**: `sqlalchemy` 2.x, `psycopg[binary]`; SAP via `pyrfc` or `hdbcli` if scoped
- **RL (Stage 14)**: `gymnasium`, `stable-baselines3`
- **Dashboard**: `streamlit` (default) — switch to `dash` only if interactivity demands it
- **Reports**: `python-docx`, `weasyprint` (PDF), `xlsxwriter`
- **Code quality**: `ruff` (lint+format), `mypy`, `pytest`, `pre-commit`
- **Logging**: `loguru`
- **Config**: `pydantic-settings` with `.env`

Do **not** introduce a new dependency without justification in the PR description.

---

## 6. Project structure

```
spare-parts-system/
├── .vscode/                   # editor settings, debug configs
├── .env.example               # template — never commit real .env
├── .gitignore                 # MUST exclude data/, .env, outputs/
├── pyproject.toml
├── uv.lock                    # or poetry.lock — committed
├── README.md
├── CLAUDE.md                  # this file
├── data/
│   ├── raw/                   # IMMUTABLE source files (xlsx, pdf)
│   ├── interim/               # validated parquet
│   ├── processed/             # feature-engineered model inputs
│   └── outputs/               # generated xlsx/docx/pdf
├── docs/
│   ├── master_prompt.md       # full project brief — deep reference
│   ├── adr/                   # architecture decision records
│   ├── model_cards/           # one per model
│   └── data_dictionary.md
├── src/
│   ├── config/                # settings.py (Pydantic), paths, constants
│   ├── ingestion/             # one module per source
│   ├── validation/            # pandera schemas
│   ├── eda/                   # stages 1, 4, 5, 8
│   ├── features/              # feature engineering
│   ├── models/
│   │   ├── unit_sales_forecast/    # stage 2
│   │   ├── uio_forecast/           # stage 3
│   │   ├── master_data/            # stage 6.x
│   │   ├── classification/         # stage 9
│   │   ├── demand_forecast/        # stage 10
│   │   ├── inventory_policy/       # stage 12
│   │   └── rl_agent/               # stage 14
│   ├── reports/               # xlsx/docx/pdf exporters
│   ├── db/                    # SQLAlchemy models, migrations
│   └── dashboard/             # streamlit app
├── notebooks/                 # exploratory only — promote to src/ once stable
├── tests/                     # mirrors src/ structure
└── scripts/                   # CLI entry points: run_stage.py
```

When creating files, **place them in the right tier**. Notebooks are for exploration only; once a transformation works, move it to `src/` with tests.

---

## 7. First-time environment setup (VS Code)

Run these once after cloning. Verify each step succeeds before moving on.

```bash
# 1. Install uv if missing (preferred; fast)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create venv and install deps
uv venv --python 3.12
uv sync

# 3. Activate (Linux/Mac)
source .venv/bin/activate
# 3. Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# 4. Install pre-commit hooks
pre-commit install

# 5. Copy env template — then fill in real values
cp .env.example .env

# 6. Verify
ruff check src/
mypy src/
pytest -q
```

**VS Code extensions to install** (the workspace will prompt):
- Python (Microsoft)
- Pylance
- Ruff (charliermarsh)
- Jupyter
- Data Wrangler
- SQLTools + SQLTools PostgreSQL/Redshift Driver
- Excel Viewer
- GitLens
- dotenv

After install, open the command palette → **Python: Select Interpreter** → pick `.venv/bin/python`.

---

## 8. Daily commands

```bash
# Run a specific pipeline stage (1-14)
python -m scripts.run_stage 1            # MSCI EDA
python -m scripts.run_stage 9            # ABC-XYZ-FSN classification
python -m scripts.run_stage 13           # generate next shipment report

# Run the dashboard
streamlit run src/dashboard/app.py

# Format + lint
ruff format src/ tests/
ruff check src/ tests/ --fix
mypy src/

# Tests
pytest                                   # full suite
pytest tests/ingestion/ -v               # one module
pytest -k "supersession" -v              # by keyword
pytest --cov=src --cov-report=html       # coverage

# Ingest a new file (append-on-upload, hash-deduped)
python -m scripts.ingest --file data/raw/In_and_Out_2026-05.xlsx

# Re-run a stage on fresh data
python -m scripts.run_stage 7 --refresh

# Profile a slow stage
python -m pyinstrument scripts/run_stage.py 10
```

---

## 9. Coding conventions

- **Type hints required** on every public function. `mypy --strict` for `src/models/` and `src/db/`; relaxed elsewhere.
- **Docstrings**: Google style. Every module-level function gets one. Include "Business meaning" line for any function encoding a business rule.
- **Naming**: `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_SNAKE` for constants. Filenames lowercase.
- **No magic numbers**. Constants go in `src/config/constants.py` (e.g., `LEAD_TIME_DAYS = 90`, `DEFAULT_SERVICE_LEVEL = 0.95`).
- **Pure functions for transformations**. I/O at the edges, logic in the middle.
- **DataFrames in, DataFrames out** for pipeline steps. Don't mutate inputs.
- **Date handling**: always `pd.Timestamp` with timezone. Single helper `to_period(df, "M")` for monthly aggregation.
- **Logging**: use `loguru.logger`. INFO for milestones, DEBUG for row counts. Never `print()` in `src/`.
- **Errors**: raise specific exceptions (`SchemaValidationError`, `SupersessionCycleError`), not bare `Exception`.
- **Tests**: every transformation has a unit test. Use `hypothesis` for hash-dedup and supersession-resolution properties.
- **Imports**: stdlib → third-party → local, with a blank line between. Ruff enforces.

---

## 10. Data abstraction — Excel today, DB tomorrow

All ingestion goes through an abstract interface so swapping to Postgres/SAP is one config flag, not a rewrite.

```python
# src/ingestion/base.py
from abc import ABC, abstractmethod

class DataSource(ABC):
    @abstractmethod
    def get_msci(self) -> pd.DataFrame: ...
    @abstractmethod
    def get_orders(self) -> pd.DataFrame: ...
    # ...one method per logical entity

# src/ingestion/excel.py
class ExcelDataSource(DataSource): ...

# src/ingestion/database.py
class DatabaseDataSource(DataSource): ...
```

Business logic depends on `DataSource`, never directly on `pd.read_excel`. The active backend is selected by `DATA_BACKEND=excel|postgres|sap` in `.env`.

---

## 11. Append-on-upload semantics (incremental files)

For `In_and_Out.xlsx`, monthly `MSCI` updates, and any time-series source:

1. Compute a deterministic row hash on natural-key columns (e.g., `VIN + Posting Date + Material + Qty`).
2. Insert only rows whose hash is not already present.
3. Log every ingestion to `ingestion_log` with: filename, row count in, new rows inserted, duplicates skipped, validation failures, ingested_at.
4. Surface the log in the dashboard.

Never overwrite. Never deduplicate by truncate-and-reload. Always hash-dedup.

---

## 12. Security rules — enforce always

- **Secrets**: `.env` only, loaded via `pydantic-settings`. `.env` is git-ignored; only `.env.example` is committed. **Never** paste real credentials in code, comments, or chat.
- **Real data must never leave the project**: do not paste real VINs, dealer codes, customer names, or prices into external tools or LLMs. Use synthetic samples that preserve schema only.
- **`data/raw/` is immutable**: read-only. Never edit a raw file in place.
- **DB connections**: read-only service account for analytics; `sslmode=require` minimum.
- **PDFs are untrusted input**: parse in subprocess, cap file size at 50MB, strip JS.
- **Generated reports** (xlsx, docx, pdf) **must include a metadata footer**: source file hashes, code commit SHA, generation timestamp, model version. Non-negotiable — these decisions move LKR-millions.

---

## 13. Files Claude must NEVER touch

- Anything under `data/raw/` — immutable source.
- `.env` — secrets.
- `uv.lock` / `poetry.lock` — only update via `uv add` / `uv lock`.
- `docs/adr/*.md` once merged — append a new ADR instead.
- Any file the user has explicitly marked "frozen" in a comment.

---

## 14. Stage dependency map

```
Stage 1 (MSCI EDA) ──────────────┐
Stage 2 (Unit sales forecast) ───┤
Stage 3 (UIO forecast) ──────────┤
                                 ├─→ Stage 6.4 (UIO-based demand)
Stage 4 (Orders EDA) ────────────┤
Stage 5 (Sales EDA) ─────────────┤
                                 │
Stage 6.1 (Catalog extraction) ──┤
Stage 6.2 (Catalog preview) ─────┤
Stage 6.3 (Supersession+master) ─┴─→ part_master.xlsx ─┐
                                                       │
Stage 7 (Stock movement) ──────────────────────────────┤
Stage 8 (Spare parts EDA) ─────────────────────────────┤
                                                       ▼
                                  Stage 9 (ABC-XYZ-FSN classification)
                                                       │
                                                       ▼
                                  Stage 10 (Demand forecast)
                                                       │
                                                       ▼
                                  Stage 11 (Stock tracker)
                                                       │
                                                       ▼
                                  Stage 12 (ROL/ROQ/Buffer)
                                                       │
                                                       ▼
                                  Stage 13 (Next shipment report)
                                                       │
                                                       ▼
                                  Stage 14 (RL system + dashboard)
```

Do not start a downstream stage until upstream artifacts exist and have passed validation.

---

## 15. When to pause and ask the user

Stop and surface the issue — do not silently proceed — when:

- Schema mismatch on ingestion (column missing, type wrong).
- A forecast model fails to beat a seasonal-naive baseline.
- A part appears in `SSOP.xlsx` with a supersession cycle (A→B→A).
- ROL or ROQ output is more than 3× recent realized demand for that SKU.
- The RL agent (Stage 14) recommends an order quantity outside the ±50% band of the rule-based policy.
- A single dealer accounts for >40% of return value in a month (possible fraud/process issue).
- PDF extraction confidence is below 0.85 for any catalog page.
- Any computation requires data the user has not provided.

---

## 16. Where to find more

- **Full project brief, all 14 stages, modeling strategies, KPIs**: `docs/master_prompt.md`
- **Architecture decision log**: `docs/adr/`
- **Model cards** (one per stage with a model): `docs/model_cards/`
- **Data dictionary**: `docs/data_dictionary.md`
- **Ingestion log** (runtime): dashboard → Admin tab, or `data/interim/ingestion_log.parquet`

---

## 17. First-task checklist for any new session

When starting a fresh session, before doing anything else:

1. Read this file fully.
2. Skim `docs/master_prompt.md` §2 (business context) and §7 (the stage relevant to the current task).
3. Run `git status` and `git log --oneline -5` to know where the project stands.
4. Run `pytest -q` to confirm the codebase is green before changes.
5. Ask the user which stage to work on, and what's changed since the last session.

Only then begin substantive work.
