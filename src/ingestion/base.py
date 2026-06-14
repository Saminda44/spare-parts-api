"""Abstract DataSource interface — business logic depends on this, never on pd.read_excel."""

from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    """Backend-agnostic data access. Swap excel ↔ postgres ↔ sap via DATA_BACKEND env var."""

    @abstractmethod
    def get_msci(self) -> pd.DataFrame:
        """MSCI motorcycle sales + return records (one row per VIN × transaction)."""
        ...

    @abstractmethod
    def get_orders(self) -> pd.DataFrame:
        """Purchase orders and return documents (In_and_Out)."""
        ...

    @abstractmethod
    def get_dealers(self) -> pd.DataFrame:
        """Dealer master: Dealer_Code, Province, RM, ASE, Dealer_Name."""
        ...

    @abstractmethod
    def get_stock(self) -> pd.DataFrame:
        """Current and historical stock levels."""
        ...

    @abstractmethod
    def get_catalog(self) -> pd.DataFrame:
        """Spare-parts catalog: Part_No, Description, supersession chain."""
        ...

    @abstractmethod
    def get_ssop(self) -> pd.DataFrame:
        """SSOP supersession table: Old_Part → New_Part."""
        ...
