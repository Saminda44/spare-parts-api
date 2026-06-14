"""Cached data loaders for the Streamlit dashboard."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.config.paths import DATA_INTERIM, DATA_OUTPUTS


@st.cache_data(ttl=300)
def load_classification() -> pd.DataFrame:
    p = DATA_INTERIM / "abc_xyz_fsn.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def load_forecast() -> pd.DataFrame:
    p = DATA_INTERIM / "demand_forecast.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def load_stock_tracker() -> pd.DataFrame:
    p = DATA_INTERIM / "stock_tracker.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def load_policy() -> pd.DataFrame:
    p = DATA_INTERIM / "inventory_policy.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def load_rl_policy() -> pd.DataFrame:
    p = DATA_INTERIM / "rl_policy.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def load_monthly_demand() -> pd.DataFrame:
    p = DATA_INTERIM / "monthly_demand.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def available_stages() -> dict[str, bool]:
    """Return which pipeline stages have produced their output artefact."""
    return {
        "Stage 9 — Classification":   (DATA_INTERIM / "abc_xyz_fsn.parquet").exists(),
        "Stage 10 — Demand Forecast": (DATA_INTERIM / "demand_forecast.parquet").exists(),
        "Stage 11 — Stock Tracker":   (DATA_INTERIM / "stock_tracker.parquet").exists(),
        "Stage 12 — ROL/ROQ Policy":  (DATA_INTERIM / "inventory_policy.parquet").exists(),
        "Stage 13 — Shipment Report": (DATA_OUTPUTS / "stage13_shipment_report.xlsx").exists(),
        "Stage 14 — RL Policy":       (DATA_INTERIM / "rl_policy.parquet").exists(),
    }
