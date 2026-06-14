"""PostgreSQL-backed DataSource implementation (stub — activate via DATA_BACKEND=postgres)."""

import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, text

from src.config.settings import settings
from src.ingestion.base import DataSource


class DatabaseDataSource(DataSource):
    """Read-only analytics access to PostgreSQL. sslmode=require enforced."""

    def __init__(self) -> None:
        self._engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
        logger.info("DatabaseDataSource connected to PostgreSQL")

    def _query(self, sql: str) -> pd.DataFrame:
        with self._engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def get_msci(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.msci")

    def get_orders(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.orders")

    def get_dealers(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.dealers")

    def get_stock(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.stock")

    def get_catalog(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.catalog")

    def get_ssop(self) -> pd.DataFrame:
        return self._query("SELECT * FROM raw.ssop")
