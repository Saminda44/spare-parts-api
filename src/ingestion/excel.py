"""Excel-backed DataSource implementation."""

import pandas as pd
from loguru import logger

from src.config.paths import DATA_RAW
from src.ingestion.base import DataSource


class ExcelDataSource(DataSource):
    """Reads immutable Excel files from data/raw/. Never writes to raw."""

    def get_msci(self) -> pd.DataFrame:
        path = DATA_RAW / "MSCI.xlsx"
        logger.info(f"Reading MSCI from {path}")
        return pd.read_excel(path, engine="openpyxl")

    def get_orders(self) -> pd.DataFrame:
        # Supports both a single file and monthly append files
        files = sorted(DATA_RAW.glob("In_and_Out*.xlsx"))
        if not files:
            raise FileNotFoundError(f"No In_and_Out*.xlsx found in {DATA_RAW}")
        logger.info(f"Reading {len(files)} order file(s)")
        return pd.concat([pd.read_excel(f, engine="openpyxl") for f in files], ignore_index=True)

    def get_dealers(self) -> pd.DataFrame:
        path = DATA_RAW / "Dealers.xlsx"
        logger.info(f"Reading Dealers from {path}")
        return pd.read_excel(path, engine="openpyxl")

    def get_stock(self) -> pd.DataFrame:
        path = DATA_RAW / "Stock.xlsx"
        logger.info(f"Reading Stock from {path}")
        return pd.read_excel(path, engine="openpyxl")

    def get_catalog(self) -> pd.DataFrame:
        path = DATA_RAW / "Catalog.xlsx"
        logger.info(f"Reading Catalog from {path}")
        return pd.read_excel(path, engine="openpyxl")

    def get_ssop(self) -> pd.DataFrame:
        path = DATA_RAW / "SSOP.xlsx"
        logger.info(f"Reading SSOP from {path}")
        return pd.read_excel(path, engine="openpyxl")
