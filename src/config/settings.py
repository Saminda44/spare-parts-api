"""Application settings loaded from .env via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Data backend
    data_backend: str = Field("excel", pattern="^(excel|postgres|sap)$")

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "spare_parts"
    postgres_user: str = "yamaha_rw"
    postgres_password: str = ""
    postgres_sslmode: str = "disable"  # use 'require' for remote/production

    # MLflow
    mlflow_tracking_uri: str = "./mlruns"

    # App
    log_level: str = "INFO"
    timezone: str = "Asia/Colombo"
    currency: str = "LKR"
    service_level: float = 0.95
    lead_time_days: int = 90

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            f"?sslmode={self.postgres_sslmode}"
        )


settings = Settings()  # type: ignore[call-arg]
