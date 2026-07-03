"""Shared FastAPI dependencies — cached data loading."""

from __future__ import annotations

import json
from datetime import UTC
from functools import lru_cache
from pathlib import Path  # noqa: TCH003
from typing import Any

import pandas as pd

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

_TARGETS_PATH = DATA_INTERIM / "sales_targets.json"


def get_sales_targets() -> dict[str, Any]:
    if _TARGETS_PATH.exists():
        result: dict[str, Any] = json.loads(_TARGETS_PATH.read_text())
        return result
    return {"yearly_target": 40000, "monthly_overrides": {}}


def set_sales_targets(data: dict[str, Any]) -> None:
    _TARGETS_PATH.write_text(json.dumps(data, indent=2))


_df_cache: dict[str, pd.DataFrame] = {}


def _load(path: Path) -> pd.DataFrame:
    key = str(path)
    if key not in _df_cache:
        if not path.exists():
            return pd.DataFrame()  # not cached — retry on next call
        _df_cache[key] = pd.read_parquet(path)
    return _df_cache[key]


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


@lru_cache(maxsize=1)
def _load_orders_enriched() -> pd.DataFrame:
    """Load orders_clean parquet and backfill derived columns missing from older parquets."""
    _p = DATA_INTERIM / "orders_clean.parquet"
    df = pd.read_parquet(_p) if _p.exists() else pd.DataFrame()
    if df.empty:
        return df

    # ── 1. Dealer master join (adds Dealer Name / Province / District / RM / ASE) ──
    _dealer_cols = ["Dealer Code", "Dealer Name", "Province", "District", "ASE", "RM"]
    missing_dealer_cols = [c for c in _dealer_cols[1:] if c not in df.columns]
    if missing_dealer_cols:
        _dealers_path = DATA_RAW / "dealers.xlsx"
        if _dealers_path.exists():
            dealers = pd.read_excel(_dealers_path, dtype=str)
            dealers.columns = dealers.columns.str.strip()
            for col in dealers.select_dtypes("object").columns:
                dealers[col] = dealers[col].fillna("").str.strip()
            keep = [c for c in _dealer_cols if c in dealers.columns]
            df = df.merge(dealers[keep], on="Dealer Code", how="left")
            for c in missing_dealer_cols:
                if c not in df.columns:
                    df[c] = ""
            for c in missing_dealer_cols:
                df[c] = df[c].fillna("")

    # ── 2. confirmed_value = Net Price × Confirmed Qty ──────────────────────────
    if "confirmed_value" not in df.columns and "Net Price" in df.columns:
        df["confirmed_value"] = df["Net Price"] * df["Confirmed Quantity (Item)"]

    # ── 3. mc_category keyword classification ───────────────────────────────────
    if "mc_category" not in df.columns:
        df["mc_category"] = "N/A"
        if "dealer_type" in df.columns and "Material Description" in df.columns:
            mc_mask = df["dealer_type"] == "MC"
            if mc_mask.any():
                upper = df.loc[mc_mask, "Material Description"].str.upper().fillna("")
                cat = pd.Series("Spare Parts", index=df.loc[mc_mask].index)
                cat = cat.where(~upper.str.contains("YAMALUBE", na=False), "Lubricant")
                cat = cat.where(~upper.str.contains("KARATE BATTERY", na=False), "Battery")
                cat = cat.where(~upper.str.contains("KATANA TYRE", na=False), "Tyre")
                df.loc[mc_mask, "mc_category"] = cat

    return df


def get_orders_clean() -> pd.DataFrame:
    return _load_orders_enriched()


@lru_cache(maxsize=1)
def get_dealers() -> pd.DataFrame:
    """Load dealers.xlsx and return a normalised Dealer Code / Dealer Name master."""
    path = DATA_RAW / "dealers.xlsx"
    if not path.exists():
        return pd.DataFrame(columns=["Dealer Code", "Dealer Name"])
    df = pd.read_excel(path, dtype=str)
    df.columns = df.columns.str.strip()
    for col in df.select_dtypes("object").columns:
        df[col] = df[col].fillna("").str.strip()
    return df


def get_orders_rejection_log() -> pd.DataFrame:
    return _load(DATA_INTERIM / "orders_rejection_log.parquet")


@lru_cache(maxsize=1)
def _load_sales_enriched() -> pd.DataFrame:
    """Load sales_clean parquet; backfill Province/District/RM/ASE/mc_category if missing."""
    df = _load(DATA_INTERIM / "sales_clean.parquet")
    if df.empty:
        return df

    # Backfill Province / District / RM / ASE via Payer → Dealer Name join
    _hier_cols = ["Province", "District", "ASE", "RM"]
    missing_hier = [c for c in _hier_cols if c not in df.columns]
    if missing_hier and "Payer" in df.columns:
        _dealers_path = DATA_RAW / "dealers.xlsx"
        if _dealers_path.exists():
            dealers = pd.read_excel(_dealers_path, dtype=str)
            dealers.columns = dealers.columns.str.strip()
            for col in dealers.select_dtypes("object").columns:
                dealers[col] = dealers[col].fillna("").str.strip()
            dim_cols = ["Dealer Name"] + [c for c in _hier_cols if c in dealers.columns]
            df = df.merge(
                dealers[dim_cols].drop_duplicates("Dealer Name"),
                left_on="Payer",
                right_on="Dealer Name",
                how="left",
            )
            for c in _hier_cols:
                if c not in df.columns:
                    df[c] = ""
                df[c] = df[c].fillna("")

    # Backfill mc_category via Material description keyword matching
    if "mc_category" not in df.columns and "Material" in df.columns:
        df["mc_category"] = pd.NA
        if "dealer_type" in df.columns:
            mc_mask = df["dealer_type"] == "MC"
            if mc_mask.any():
                upper = df.loc[mc_mask, "Material"].astype(str).str.upper().fillna("")
                cat = pd.Series("Spare Parts", index=df.loc[mc_mask].index)
                cat = cat.where(~upper.str.contains("YAMALUBE", na=False), "Lubricant")
                cat = cat.where(~upper.str.contains("KARATE BATTERY", na=False), "Battery")
                cat = cat.where(~upper.str.contains("KATANA TYRE", na=False), "Tyre")
                df.loc[mc_mask, "mc_category"] = cat

    return df


def get_sales_clean() -> pd.DataFrame:
    return _load_sales_enriched()


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
