"""Shared FastAPI dependencies — cached data loading."""

from __future__ import annotations

import json
from datetime import UTC
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pandas as pd

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS

_TARGETS_PATH = DATA_INTERIM / "sales_targets.json"


def get_sales_targets() -> dict[str, Any]:
    if _TARGETS_PATH.exists():
        result: dict[str, Any] = json.loads(_TARGETS_PATH.read_text())
        return result
    return {"yearly_target": 40000, "monthly_overrides": {}}


def set_sales_targets(data: dict[str, Any]) -> None:
    _TARGETS_PATH.write_text(json.dumps(data, indent=2))


@lru_cache(maxsize=1)
def _load(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


# ── Stages 1-8 ────────────────────────────────────────────────────────────────


def get_mcsi_clean() -> pd.DataFrame:
    return _load(DATA_INTERIM / "mcsi_clean.parquet")


def get_mcsi_vin_status() -> pd.DataFrame:
    return _load(DATA_INTERIM / "mcsi_vin_status.parquet")


def get_mcsi_uio_summary() -> pd.DataFrame:
    return _load(DATA_INTERIM / "mcsi_uio_summary.parquet")


def get_uio_external() -> pd.DataFrame:
    return _load(DATA_INTERIM / "uio_external.parquet")


def get_unit_sales_forecast() -> pd.DataFrame:
    return _load(DATA_INTERIM / "unit_sales_forecast.parquet")


def get_uio_forecast() -> pd.DataFrame:
    return _load(DATA_INTERIM / "uio_forecast.parquet")


def get_orders_clean() -> pd.DataFrame:
    return _load(DATA_INTERIM / "orders_clean.parquet")


def get_orders_rejection_log() -> pd.DataFrame:
    return _load(DATA_INTERIM / "orders_rejection_log.parquet")


def get_sales_clean() -> pd.DataFrame:
    return _load(DATA_INTERIM / "sales_clean.parquet")


def get_part_master() -> pd.DataFrame:
    return _load(DATA_INTERIM / "part_master.parquet")


def get_supersession_map() -> pd.DataFrame:
    return _load(DATA_INTERIM / "supersession_map.parquet")


def get_stock_movements() -> pd.DataFrame:
    return _load(DATA_INTERIM / "stock_movements.parquet")


def get_spare_parts_features() -> pd.DataFrame:
    return _load(DATA_INTERIM / "spare_parts_features.parquet")


def get_ingestion_log() -> pd.DataFrame:
    return _load(DATA_INTERIM / "ingestion_log.parquet")


# ── Stages 9-14 ───────────────────────────────────────────────────────────────


def get_classification() -> pd.DataFrame:
    return _load(DATA_INTERIM / "abc_xyz_fsn.parquet")


def get_forecast() -> pd.DataFrame:
    return _load(DATA_INTERIM / "demand_forecast.parquet")


def get_stock_tracker() -> pd.DataFrame:
    return _load(DATA_INTERIM / "stock_tracker.parquet")


def get_policy() -> pd.DataFrame:
    return _load(DATA_INTERIM / "inventory_policy.parquet")


def get_rl_policy() -> pd.DataFrame:
    return _load(DATA_INTERIM / "rl_policy.parquet")


def get_monthly_demand() -> pd.DataFrame:
    return _load(DATA_INTERIM / "monthly_demand.parquet")


def get_uio_based_demand() -> pd.DataFrame:
    return _load(DATA_INTERIM / "uio_based_demand.parquet")


def get_catalog_parts() -> pd.DataFrame:
    return _load(DATA_INTERIM / "catalog_parts.parquet")


_STAGE_ARTIFACTS: dict[str, Path] = {
    "stage1_mcsi": DATA_INTERIM / "mcsi_clean.parquet",
    "stage2_sales_forecast": DATA_INTERIM / "unit_sales_forecast.parquet",
    "stage3_uio_forecast": DATA_INTERIM / "uio_forecast.parquet",
    "stage4_orders_eda": DATA_INTERIM / "orders_clean.parquet",
    "stage5_sales_eda": DATA_INTERIM / "sales_clean.parquet",
    "stage6_part_master": DATA_INTERIM / "part_master.parquet",
    "stage7_stock_movements": DATA_INTERIM / "stock_movements.parquet",
    "stage8_spare_parts_eda": DATA_INTERIM / "spare_parts_features.parquet",
    "stage9_classification": DATA_INTERIM / "abc_xyz_fsn.parquet",
    "stage10_demand_forecast": DATA_INTERIM / "demand_forecast.parquet",
    "stage11_stock_tracker": DATA_INTERIM / "stock_tracker.parquet",
    "stage12_policy": DATA_INTERIM / "inventory_policy.parquet",
    "stage13_shipment_report": DATA_OUTPUTS / "stage13_shipment_report.xlsx",
    "stage14_rl_policy": DATA_INTERIM / "rl_policy.parquet",
}


def pipeline_status() -> dict[str, bool]:
    return {k: p.exists() for k, p in _STAGE_ARTIFACTS.items()}


def pipeline_freshness() -> dict[str, str | None]:
    """Return ISO-format mtime for each stage artifact, or None if not present."""
    from datetime import datetime

    result: dict[str, str | None] = {}
    for k, p in _STAGE_ARTIFACTS.items():
        if p.exists():
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            result[k] = mtime.isoformat()
        else:
            result[k] = None
    return result
