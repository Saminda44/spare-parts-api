"""CLI entry point: python -m scripts.run_stage <stage_number> [--refresh] [--append]

Usage:
    python -m scripts.run_stage 1           # MSCI EDA
    python -m scripts.run_stage 6           # Master data (catalog + supersession)
    python -m scripts.run_stage 6.4         # UIO-based demand estimation
    python -m scripts.run_stage 7           # Stock movement (full load)
    python -m scripts.run_stage 7 --append  # Stock movement (append new rows only)
    python -m scripts.run_stage 9           # ABC-XYZ-FSN classification
    python -m scripts.run_stage 13          # Next shipment report
    python -m scripts.run_stage 9 --refresh # Force re-run ignoring cached outputs
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from loguru import logger

app = typer.Typer(help="Run a specific pipeline stage (1-14).")

STAGE_REGISTRY: dict[str, str] = {
    "1":   "MSCI EDA",
    "2":   "Unit sales forecast",
    "3":   "UIO forecast",
    "4":   "Orders EDA",
    "5":   "Sales EDA",
    "6":   "Master data (catalog + supersession)",
    "6.4": "UIO-based demand estimation",
    "7":   "Stock movement",
    "8":   "Spare parts EDA",
    "9":   "ABC-XYZ-FSN classification",
    "10":  "Demand forecast",
    "11":  "Stock tracker",
    "12":  "ROL / ROQ / Buffer policy",
    "13":  "Next shipment report",
    "14":  "RL system + dashboard",
}


@app.command()
def main(
    stage: Annotated[str, typer.Argument(help="Stage number, e.g. 1, 7, 6.4")],
    refresh: Annotated[bool, typer.Option("--refresh", help="Force re-run")] = False,
    append:  Annotated[bool, typer.Option("--append",  help="Stage 7: append new rows only")] = False,
) -> None:
    name = STAGE_REGISTRY.get(stage, "Unknown")
    logger.info(f"Starting Stage {stage}: {name} (refresh={refresh}, append={append})")

    # Dispatch — each stage module is imported lazily so missing deps only fail at runtime
    if stage == "1":
        from src.eda.stage01_msci import run
        run(refresh=refresh)
    elif stage == "2":
        from src.models.unit_sales_forecast.stage02_unit_sales_forecast import run
        run(refresh=refresh)
    elif stage == "3":
        from src.models.uio_forecast.stage03_uio_forecast import run
        run(refresh=refresh)
    elif stage == "4":
        from src.eda.stage04_orders_eda import run
        run(refresh=refresh)
    elif stage == "5":
        from src.eda.stage05_sales_eda import run
        run(refresh=refresh)
    elif stage == "6":
        from src.models.master_data.stage06_master_data import run
        run(refresh=refresh)
    elif stage == "6.4":
        from src.models.master_data.stage064_uio_demand import run
        run(refresh=refresh)
    elif stage == "7":
        from src.eda.stage07_stock_movement import run
        run(refresh=refresh, append=append)
    elif stage == "8":
        from src.eda.stage08_spare_parts_eda import run
        run(refresh=refresh)
    elif stage == "9":
        from src.models.classification.stage09_abc_xyz_fsn import run
        run(refresh=refresh)
    elif stage == "10":
        from src.models.demand_forecast.stage10_demand_forecast import run
        run(refresh=refresh)
    elif stage == "11":
        from src.models.inventory_policy.stage11_stock_tracker import run
        run(refresh=refresh)
    elif stage == "12":
        from src.models.inventory_policy.stage12_rol_roq import run
        run(refresh=refresh)
    elif stage == "13":
        from src.reports.stage13_shipment_report import run
        run(refresh=refresh)
    elif stage == "14":
        from src.models.rl_agent.stage14_rl_system import run
        run(refresh=refresh)
    else:
        logger.warning(f"Stage {stage!r} ({name}) not yet implemented. Skipping.")
        raise typer.Exit(code=0)

    logger.info(f"Stage {stage} complete.")


if __name__ == "__main__":
    app()
