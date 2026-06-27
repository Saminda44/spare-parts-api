# AGENTS Guidelines for This Repository

This repository is the **Yamaha Spare Parts Inventory Optimization** project: a
demand-forecasting and inventory-policy system for a Yamaha motorcycle spare-parts
distributor in Sri Lanka (3-month India lead time, ~monthly order cycles). It is a
**Python analytics + ML + RL pipeline** (14 stages) with a Streamlit dashboard, running
on Excel today and designed to move to Postgres/SAP without a rewrite.

`CLAUDE.md` is the always-loaded, authoritative context for this project. **Read it
fully before any task.** This file (`AGENTS.md`) is the entry point for non-Claude
agents (e.g. the Codex CLI) and mirrors the rules that matter most for an interactive
session. When the two ever disagree, `CLAUDE.md` wins. The deep reference is
`docs/master_prompt.md`.

> ⚠️ This is **not** a Next.js / Node project. There is no dev server to keep alive, no
> HMR, and no `npm run build`. Ignore any prior web-app instructions.

---

## 1. Working Protocol — non-negotiable

Follow this sequence for every stage. Do not skip ahead.

1. **Clarify first.** Before starting a new stage, ask for missing context — schemas,
   date formats, business rules. Never assume. (Full question list: `master_prompt.md`
   §3 Step A.)
2. **Architecture before code.** For any non-trivial change, sketch
   inputs → method → outputs → validation and get sign-off before implementing.
3. **One stage at a time.** Finish, validate, summarize, confirm — then move on. No
   chaining stages without a checkpoint.
4. **Validate every read and write.** Schema-on-read with `pandera`; round-trip checks
   on every Excel output.
5. **Plain-English summary after each task.** What was done, key findings, files
   produced, open questions.

---

## 2. Critical Business Rules — apply without being asked

Violating any of these produces wrong numbers that move LKR-millions.

* **Sale vs return** — in `MSCI.xlsx`, classify per VIN by
  `groupby(VIN).SlsVolQty.sum()`: sum = 1 → sold, sum = 0 → returned.
* **Order document classification** — `Sales Document` starting with `4` is a
  **purchase order**; starting with `6` is a **return**.
* **Dealer scoping** — only customers whose `Customer` matches a `Dealer_Code` in
  `Dealers.xlsx` are bike-spare dealers. Exclude all others from spare-parts analysis.
* **Lead time** — 3 months (~90 days) for India imports. Per order line:
  `Good Issue Date − Created On` (days).
* **Lost qty per line** — `Confirmed Quantity − Order Quantity` (negative = short-ship).
  Fully-rejected line (`lost_qty == order_qty`) → drop from "received", log to rejection
  report.
* **Sales hierarchy** — Province → Regional Manager (RM) → Area Sales Executive (ASE) →
  Dealer.
* **Timezone** — Asia/Colombo (UTC+05:30): store UTC, display local.
* **Currency** — LKR only. Never assume USD.

---

## 3. Use the Right Workflow — `uv`, not a build server

* **Environment is managed with `uv`** (fallback `poetry`). Python **3.11+ (3.12
  preferred)**.
* Run pipeline work through the stage runner and tests, not a long-lived server. The
  Streamlit dashboard is the only persistent process, and only when explicitly needed.
* **Do not** run ad-hoc one-off transformations that bypass the `DataSource` interface
  (see §6).

```bash
uv venv --python 3.12
uv sync
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pre-commit install
cp .env.example .env             # then fill real values (never commit .env)
```

---

## 4. Keep Dependencies in Sync

If you add or update a dependency:

1. Use `uv add <pkg>` / `uv lock` (or the `poetry` equivalent) — never hand-edit the
   lockfile.
2. **Commit the lockfile** (`uv.lock` / `poetry.lock`).
3. Justify any new dependency in the PR description. The stack in `CLAUDE.md` §5 is
   locked; don't introduce alternatives casually.

---

## 5. Coding Conventions

* **Python only** for pipeline/source code. No `.tsx`/`.ts`.
* **Type hints required** on every public function. `mypy --strict` for `src/models/`
  and `src/db/`; relaxed elsewhere.
* **Docstrings**: Google style. Add a "Business meaning" line for any function encoding
  a business rule.
* **Naming**: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE`
  constants, lowercase filenames.
* **No magic numbers** — constants live in `src/config/constants.py`
  (e.g. `LEAD_TIME_DAYS = 90`, `DEFAULT_SERVICE_LEVEL = 0.95`).
* **Pure functions for transformations**: I/O at the edges, logic in the middle.
  DataFrames in, DataFrames out — don't mutate inputs.
* **Dates**: always `pd.Timestamp` with timezone; use the `to_period(df, "M")` helper.
* **Logging**: `loguru.logger` (INFO milestones, DEBUG row counts). Never `print()` in
  `src/`.
* **Errors**: raise specific exceptions (`SchemaValidationError`,
  `SupersessionCycleError`), not bare `Exception`.
* **Tests**: every transformation gets a unit test. Use `hypothesis` for hash-dedup and
  supersession-resolution properties.
* **File placement**: notebooks are exploration only; promote stable code to `src/` with
  tests. (Structure: `CLAUDE.md` §6.)

---

## 6. Data Abstraction & Ingestion

* All ingestion goes through the abstract **`DataSource`** interface
  (`src/ingestion/base.py`). Business logic depends on `DataSource`, **never** directly
  on `pd.read_excel`. Backend is chosen by `DATA_BACKEND=excel|postgres|sap` in `.env`.
* **Append-on-upload, hash-deduped.** Compute a deterministic row hash on natural-key
  columns, insert only new rows, and log every ingestion (filename, rows in, new rows,
  duplicates skipped, validation failures, timestamp). **Never** truncate-and-reload.

---

## 7. Security Rules — enforce always

* **Secrets** in `.env` only, via `pydantic-settings`. `.env` is git-ignored; only
  `.env.example` is committed. Never paste real credentials anywhere.
* **Real data must never leave the project** — no real VINs, dealer codes, customer
  names, or prices in external tools or LLM prompts. Use schema-preserving synthetic
  samples.
* **`data/raw/` is immutable** — read-only; never edit a raw file in place.
* **DB**: read-only analytics service account, `sslmode=require` minimum.
* **PDFs are untrusted** — parse in a subprocess, cap at 50MB, strip JS.
* **Generated reports (xlsx/docx/pdf) must carry a metadata footer**: source file
  hashes, code commit SHA, generation timestamp, model version. Non-negotiable.

---

## 8. Files Agents Must NEVER Touch

* Anything under `data/raw/` — immutable source.
* `.env` — secrets.
* `uv.lock` / `poetry.lock` — only via `uv add` / `uv lock`.
* `docs/adr/*.md` once merged — append a new ADR instead.
* Any file the user has explicitly marked "frozen" in a comment.

---

## 9. When to Pause and Ask

Stop and surface the issue — don't silently proceed — when: schema mismatch on
ingestion; a forecast fails to beat a seasonal-naive baseline; a supersession cycle
(A→B→A) appears in `SSOP.xlsx`; ROL/ROQ exceeds 3× recent realized demand for an SKU;
the Stage 14 RL agent recommends a quantity outside ±50% of the rule-based policy; a
single dealer is >40% of monthly return value; PDF extraction confidence < 0.85; or any
computation needs data not yet provided.

---

## 10. Useful Commands Recap

| Command                                             | Purpose                                  |
| --------------------------------------------------- | ---------------------------------------- |
| `python -m scripts.run_stage <1-14>`                | Run a specific pipeline stage.           |
| `python -m scripts.run_stage 7 --refresh`           | Re-run a stage on fresh data.            |
| `python -m scripts.ingest --file <path.xlsx>`       | Append-on-upload, hash-deduped ingest.   |
| `streamlit run src/dashboard/app.py`                | Launch the dashboard.                    |
| `ruff format src/ tests/`                           | Format.                                  |
| `ruff check src/ tests/ --fix`                      | Lint (autofix).                          |
| `mypy src/`                                         | Type-check.                              |
| `pytest`                                            | Full test suite.                         |
| `pytest -k "<keyword>" -v`                          | Run tests by keyword.                    |
| `pytest --cov=src --cov-report=html`                | Coverage report.                         |
| `python -m pyinstrument scripts/run_stage.py <n>`   | Profile a slow stage.                    |

---

## 11. First-Task Checklist (every new session)

1. Read `CLAUDE.md` fully; skim `master_prompt.md` §2 and the relevant stage section.
2. `git status` and `git log --oneline -5` to see where things stand.
3. `pytest -q` to confirm the codebase is green before changes.
4. Ask the user which stage to work on and what's changed since last session.

Only then begin substantive work.

---

Following these practices keeps agent-assisted work on this project correct, reproducible,
and safe with sensitive distributor data. When in doubt, re-read `CLAUDE.md` and ask
before proceeding.
