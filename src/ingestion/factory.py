"""Return the configured DataSource backend from DATA_BACKEND env var."""

from src.config.settings import settings
from src.ingestion.base import DataSource


def get_data_source() -> DataSource:
    """Return the active DataSource backend (controlled by DATA_BACKEND env var)."""
    backend = settings.data_backend
    if backend == "excel":
        from src.ingestion.excel import ExcelDataSource
        return ExcelDataSource()
    if backend == "postgres":
        from src.ingestion.database import DatabaseDataSource
        return DatabaseDataSource()
    raise ValueError(f"Unknown DATA_BACKEND={backend!r}. Choose excel | postgres | sap.")
