"""Pandera schemas for schema-on-read validation.

Each schema matches the confirmed column names from the actual data files.
Raise SchemaValidationError on failure — never silently pass.
"""

import pandera as pa
from pandera import Column, DataFrameSchema


class SchemaValidationError(Exception):
    """Raised when a DataFrame fails pandera schema validation."""


# Confirmed from MCSI.xlsx actual file (note: file is named MCSI.xlsx, col is 'Billing Date')
MCSI_SCHEMA = DataFrameSchema(
    {
        "VIN": Column(str, nullable=False),
        "SlsVolQty": Column(int, nullable=False),
        "Billing Date": Column(str, nullable=False),   # DD.MM.YYYY — parse with dayfirst=True
        "Model": Column(str, nullable=True),
        "Province": Column(str, nullable=True),
        "RM": Column(str, nullable=True),
        "ASE": Column(str, nullable=True),
        "Dealer": Column(str, nullable=True),
        "Dealer Code": Column(str, nullable=True),
        "Net Sales": Column(float, nullable=True),
    },
    coerce=True,
    strict=False,
)

# Confirmed from orders.xlsx actual file
ORDERS_SCHEMA = DataFrameSchema(
    {
        "Sales Document": Column(object, nullable=False),
        "Material": Column(str, nullable=False),
        "Order Quantity (Item)": Column(float, nullable=False),
        "Confirmed Quantity (Item)": Column(float, nullable=False),
        "Goods Issue Date": Column(pa.DateTime, nullable=True),
        "Created On": Column(pa.DateTime, nullable=False),
        "Sold-To Party Name": Column(str, nullable=True),
        "Reason for Rejection": Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

# Confirmed from dealers.xlsx actual file
DEALERS_SCHEMA = DataFrameSchema(
    {
        "Dealer Code": Column(object, nullable=False),
        "Dealer Name": Column(str, nullable=False),
        "Province": Column(str, nullable=True),
        "District": Column(str, nullable=True),
        "ASE": Column(str, nullable=True),
        "RM": Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

# Confirmed from In_and_Out.xlsx
IN_AND_OUT_SCHEMA = DataFrameSchema(
    {
        "Material": Column(str, nullable=False),
        "Qty in unit of entry": Column(int, nullable=False),   # negative = out, positive = in
        "Movement Type": Column(int, nullable=False),
        "Posting Date": Column(pa.DateTime, nullable=False),
        "Customer": Column(object, nullable=True),
        "Movement Type Text": Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

# Confirmed from current stock.xlsx
STOCK_SCHEMA = DataFrameSchema(
    {
        "Material": Column(object, nullable=False),
        "Material Description": Column(str, nullable=True),
        "Unrestricted": Column(object, nullable=True),   # comma-decimal format; clean before use
        "Matl Group": Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)

# Confirmed from SSOP.xlsx
SSOP_SCHEMA = DataFrameSchema(
    {
        "Requested P/No": Column(str, nullable=False),
        "Latest Part No /JAN": Column(str, nullable=True),
        "Description": Column(str, nullable=True),
        "Compatible models": Column(str, nullable=True),
    },
    coerce=True,
    strict=False,
)
