---
description: Run a specific pipeline stage (1-14) with pre/post validation. Usage: /run-stage <n>
argument: stage number (1-14)
---

## Steps

1. Parse the stage number from the argument. If missing, ask the user which stage to run.
2. Check upstream artifacts exist (see dependency map below). If missing, stop and report which stage must run first.
3. Run pre-stage tests:
   ```bash
   pytest tests/ -q -k "stage<n>" 2>/dev/null || echo "No stage-specific tests found"
   ```
4. Execute the stage:
   ```bash
   python -m scripts.run_stage <n>
   ```
5. On completion, verify the expected output file exists (see table below).
6. Run the full test suite to catch regressions:
   ```bash
   pytest -q
   ```
7. Report: stage, runtime, output files, row counts, any warnings or validation failures.

## Stage → Output File Map

| Stage | Expected output |
|---|---|
| 1 | data/interim/msci_eda.parquet |
| 2 | data/interim/unit_sales_forecast.parquet |
| 3 | data/interim/uio_forecast.parquet |
| 4 | data/interim/orders_eda.parquet |
| 5 | data/interim/sales_eda.parquet |
| 6 | data/outputs/part_master.xlsx |
| 7 | data/interim/stock_movement.parquet |
| 8 | data/interim/spares_eda.parquet |
| 9 | data/processed/classification.parquet |
| 10 | data/processed/demand_forecast.parquet |
| 11 | data/processed/stock_tracker.parquet |
| 12 | data/outputs/inventory_policy.xlsx |
| 13 | data/outputs/next_shipment_report.xlsx |
| 14 | data/outputs/rl_policy.parquet |

## Dependency Map

- Stages 1-5: no upstream dependency
- Stage 6: needs stage 1 output
- Stage 7: needs stage 6 output
- Stage 8: needs stage 7 output
- Stages 9-14: each needs the previous stage's output

## Validation Rules

- **Stage 10 forecast:** MAPE must be < 30% or flag for review. Must beat seasonal-naive baseline.
- **Stage 12 policy:** ROL and ROQ must not exceed 3× recent 12-month demand for any SKU.
- **Stage 14 RL:** recommended order qty must be within ±50% of rule-based policy output.
