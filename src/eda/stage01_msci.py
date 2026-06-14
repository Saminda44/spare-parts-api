"""Stage 1: Motorcycle Sales (MCSI) EDA.

Business rules applied (CLAUDE.md §4):
- Sold vs returned per VIN: groupby(VIN).SlsVolQty.sum() → 1 = sold, 0 = returned.
- Hierarchy for drilldown: Province → RM → ASE → Dealer.
- Billing Date is in DD.MM.YYYY string format — parsed with dayfirst=True.
- Currency: LKR. Net Sales column used for revenue analysis.
- 'Delaer Name' in source has a typo — normalised to 'Dealer Name' on load.

Outputs:
  data/interim/mcsi_clean.parquet      — validated, enriched, sold-only rows
  data/interim/mcsi_vin_status.parquet — one row per VIN with sold/returned flag
  data/outputs/stage01_mcsi_eda.xlsx   — multi-sheet Excel report
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import xlsxwriter
from loguru import logger

from src.config.constants import CURRENCY, TIMEZONE
from src.config.paths import DATA_INTERIM, DATA_OUTPUTS, DATA_RAW

# ── File paths ────────────────────────────────────────────────
_MCSI_FILE = DATA_RAW / "MCSI.xlsx"
_UIO_FILE  = DATA_RAW / "UIO.xlsx"
_CLEAN_PARQUET = DATA_INTERIM / "mcsi_clean.parquet"
_VIN_PARQUET   = DATA_INTERIM / "mcsi_vin_status.parquet"
_UIO_PARQUET   = DATA_INTERIM / "uio_external.parquet"
_OUTPUT_EXCEL  = DATA_OUTPUTS / "stage01_mcsi_eda.xlsx"


# ── 1. Load & clean ──────────────────────────────────────────

def load_mcsi() -> pd.DataFrame:
    """Load MCSI.xlsx and apply all cleaning rules."""
    logger.info(f"Loading {_MCSI_FILE}")
    df = pd.read_excel(_MCSI_FILE, engine="openpyxl", dtype={"Dealer Code": str})

    # Normalise typo in source column name
    df = df.rename(columns={"Delaer Name": "Dealer Name"})

    # Parse Billing Date (DD.MM.YYYY stored as string in Excel)
    df["Billing Date"] = pd.to_datetime(
        df["Billing Date"], dayfirst=True, errors="coerce"
    ).dt.tz_localize(None)

    # Derived time columns
    df["Year"] = df["Billing Date"].dt.year
    df["Month_Num"] = df["Billing Date"].dt.month
    df["Year_Month"] = df["Billing Date"].dt.to_period("M")
    df["Year_Month_str"] = df["Year_Month"].astype(str)

    # Normalise province (strip whitespace, title-case)
    df["Province"] = df["Province"].str.strip().str.title()

    # Business correction: "Borella" is a showroom in Western Province, Colombo District —
    # not a province. Reclassify those rows.
    borella_mask = df["Province"] == "Borella"
    if borella_mask.any():
        logger.info(
            f"Reclassifying {borella_mask.sum()} 'Borella' rows → Province=Western, District=Colombo"
        )
        df.loc[borella_mask, "Province"] = "Western"
        df.loc[borella_mask, "District"] = "Colombo"

    # Normalise inconsistent casing for North Western
    df["Province"] = df["Province"].replace("North Western Province", "North Western") \
                                   .replace("North-Western", "North Western")
    df.loc[df["Province"].str.lower() == "north western", "Province"] = "North Western"

    # Flag rows with missing VIN
    missing_vin = df["VIN"].isna().sum()
    if missing_vin > 0:
        logger.warning(f"{missing_vin} rows with null VIN — excluded from VIN-level analysis")

    logger.info(f"Loaded {len(df):,} rows, {df['VIN'].nunique():,} unique VINs")
    return df


# ── 2. Classify VINs: sold vs returned ───────────────────────

def classify_vin_status(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame with one row per VIN and a 'Status' column.

    Business rule: sum(SlsVolQty) per VIN → 1 = sold, 0 = returned.
    """
    vin_sum = df.groupby("VIN")["SlsVolQty"].sum().reset_index()
    vin_sum["VIN"] = vin_sum["VIN"].astype(str)
    vin_sum["Status"] = vin_sum["SlsVolQty"].map({1: "Sold", 0: "Returned"})
    # Flag unexpected sums (data quality issue — should not occur)
    unexpected = vin_sum[~vin_sum["SlsVolQty"].isin([0, 1])]
    if len(unexpected) > 0:
        logger.warning(f"{len(unexpected)} VINs with unexpected SlsVolQty sum: {unexpected['SlsVolQty'].value_counts().to_dict()}")
    return vin_sum


def enrich_with_status(df: pd.DataFrame, vin_status: pd.DataFrame) -> pd.DataFrame:
    """Join VIN status back onto the transaction rows."""
    return df.merge(
        vin_status[["VIN", "Status"]].rename(columns={"SlsVolQty": "SlsVolQty_sum"}),
        on="VIN",
        how="left",
    )


# ── 3. Analysis functions ─────────────────────────────────────

def monthly_sales_trend(sold: pd.DataFrame) -> pd.DataFrame:
    """Monthly unit sales and revenue trend (sold bikes only)."""
    return (
        sold.groupby("Year_Month_str")
        .agg(
            Units_Sold=("VIN", "nunique"),
            Revenue_LKR=("Net Sales", "sum"),
        )
        .reset_index()
        .rename(columns={"Year_Month_str": "Month"})
        .sort_values("Month")
    )


def hierarchy_summary(sold: pd.DataFrame, group_cols: list[str], label: str) -> pd.DataFrame:
    """Aggregate sold units and revenue by any drilldown level."""
    return (
        sold.groupby(group_cols, dropna=False)
        .agg(
            Units_Sold=("VIN", "nunique"),
            Revenue_LKR=("Net Sales", "sum"),
        )
        .reset_index()
        .sort_values("Units_Sold", ascending=False)
        .assign(Rank=lambda x: range(1, len(x) + 1))
    )


def model_mix(sold: pd.DataFrame) -> pd.DataFrame:
    """Sales breakdown by motorcycle model."""
    total = sold["VIN"].nunique()
    result = (
        sold.groupby("Model", dropna=False)
        .agg(
            Units_Sold=("VIN", "nunique"),
            Revenue_LKR=("Net Sales", "sum"),
        )
        .reset_index()
        .sort_values("Units_Sold", ascending=False)
    )
    result["Share_%"] = (result["Units_Sold"] / total * 100).round(1)
    return result


def returns_detail(df: pd.DataFrame, vin_status: pd.DataFrame) -> pd.DataFrame:
    """Detail rows for returned VINs."""
    returned_vins = vin_status.loc[vin_status["Status"] == "Returned", "VIN"]
    return df[df["VIN"].isin(returned_vins)][
        ["VIN", "Billing Date", "Model", "Dealer Name", "Province", "SlsVolQty"]
    ].sort_values(["VIN", "Billing Date"])


def dealer_performance(sold: pd.DataFrame) -> pd.DataFrame:
    """Dealer-level sales performance with hierarchy context."""
    return (
        sold.groupby(
            ["Province", "RM", "ASE", "Dealer", "Dealer Code"],
            dropna=False,
        )
        .agg(
            Units_Sold=("VIN", "nunique"),
            Revenue_LKR=("Net Sales", "sum"),
            Models=("Model", lambda x: ", ".join(sorted(x.dropna().unique()))),
        )
        .reset_index()
        .sort_values("Units_Sold", ascending=False)
        .assign(Rank=lambda x: range(1, len(x) + 1))
    )


def province_model_crosstab(sold: pd.DataFrame) -> pd.DataFrame:
    """Crosstab: Province × Model unit sales."""
    ct = pd.crosstab(
        sold.drop_duplicates("VIN")["Province"],
        sold.drop_duplicates("VIN")["Model"],
        values=sold.drop_duplicates("VIN")["VIN"],
        aggfunc="count",
    ).fillna(0).astype(int)
    ct["Total"] = ct.sum(axis=1)
    return ct.sort_values("Total", ascending=False)


def summary_kpis(sold: pd.DataFrame, returned: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Top-level KPI dictionary."""
    months_active = sold["Year_Month_str"].nunique()
    return {
        "Total Units Sold": sold["VIN"].nunique(),
        "Total Returns": returned["VIN"].nunique(),
        "Return Rate %": round(returned["VIN"].nunique() / df["VIN"].nunique() * 100, 2),
        "Total Revenue (LKR)": round(sold["Net Sales"].sum(), 0),
        "Avg Monthly Units": round(sold["VIN"].nunique() / months_active, 1),
        "Avg Revenue Per Unit (LKR)": round(sold["Net Sales"].sum() / sold["VIN"].nunique(), 0),
        "Active Provinces": sold["Province"].nunique(),
        "Active Dealers": sold["Dealer Code"].nunique(),
        "Models Sold": sold["Model"].nunique(),
        "Data From": str(sold["Billing Date"].min().date()),
        "Data To": str(sold["Billing Date"].max().date()),
        "Months of Data": months_active,
    }


# ── 4. Excel report writer ───────────────────────────────────

def _col_width(df: pd.DataFrame, col: str) -> int:
    """Auto-width: max of column name and longest value."""
    return min(max(len(str(col)), df[col].astype(str).str.len().max()) + 2, 50)


def _write_df(ws: xlsxwriter.worksheet.Worksheet, df: pd.DataFrame,
              workbook: xlsxwriter.Workbook, start_row: int = 1) -> None:
    """Write a DataFrame to an xlsxwriter worksheet with header formatting."""
    hdr_fmt = workbook.add_format({
        "bold": True, "bg_color": "#003087", "font_color": "white",
        "border": 1, "align": "center",
    })
    num_fmt = workbook.add_format({"num_format": "#,##0", "border": 1})
    flt_fmt = workbook.add_format({"num_format": "#,##0.0", "border": 1})
    pct_fmt = workbook.add_format({"num_format": "0.0%", "border": 1})
    cell_fmt = workbook.add_format({"border": 1})

    for col_idx, col in enumerate(df.columns):
        ws.write(start_row - 1, col_idx, col, hdr_fmt)

    for row_idx, row in enumerate(df.itertuples(index=False), start=start_row):
        for col_idx, (col, val) in enumerate(zip(df.columns, row)):
            col_lower = col.lower()
            if pd.isna(val):
                ws.write(row_idx, col_idx, "", cell_fmt)
            elif "lkr" in col_lower or "revenue" in col_lower or "units" in col_lower or "rank" in col_lower:
                ws.write_number(row_idx, col_idx, float(val) if val != "" else 0, num_fmt)
            elif "%" in col_lower or "share" in col_lower:
                ws.write_number(row_idx, col_idx, float(val) if val != "" else 0, flt_fmt)
            elif isinstance(val, (int, float)):
                ws.write_number(row_idx, col_idx, val, num_fmt)
            else:
                ws.write(row_idx, col_idx, str(val), cell_fmt)

    # Column widths
    for col_idx, col in enumerate(df.columns):
        ws.set_column(col_idx, col_idx, _col_width(df, col))


def write_excel_report(
    kpis: dict,
    monthly: pd.DataFrame,
    province: pd.DataFrame,
    rm: pd.DataFrame,
    ase: pd.DataFrame,
    dealers: pd.DataFrame,
    models: pd.DataFrame,
    crosstab: pd.DataFrame,
    returns: pd.DataFrame,
) -> None:
    """Write all analysis results to a multi-sheet Excel workbook."""
    _OUTPUT_EXCEL.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(_OUTPUT_EXCEL))

    title_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": "#003087"})
    kpi_label_fmt = wb.add_format({"bold": True, "bg_color": "#F0F4FF", "border": 1})
    kpi_val_fmt = wb.add_format({"num_format": "#,##0", "border": 1, "bold": True})
    kpi_str_fmt = wb.add_format({"border": 1, "bold": True})

    # ── Sheet 1: Summary KPIs ────────────────────────────────
    ws = wb.add_worksheet("Summary")
    ws.write(0, 0, "Stage 1 — Motorcycle Sales EDA", title_fmt)
    ws.write(1, 0, f"Currency: {CURRENCY}  |  Timezone: {TIMEZONE}")
    ws.set_column(0, 0, 35)
    ws.set_column(1, 1, 25)
    row = 3
    for label, val in kpis.items():
        ws.write(row, 0, label, kpi_label_fmt)
        if isinstance(val, (int, float)):
            ws.write_number(row, 1, val, kpi_val_fmt)
        else:
            ws.write(row, 1, str(val), kpi_str_fmt)
        row += 1

    # ── Sheet 2: Monthly Trend ───────────────────────────────
    ws2 = wb.add_worksheet("Monthly Trend")
    ws2.write(0, 0, "Monthly Sales Trend", title_fmt)
    _write_df(ws2, monthly, wb, start_row=2)

    # ── Sheet 3: Province ───────────────────────────────────
    ws3 = wb.add_worksheet("By Province")
    ws3.write(0, 0, "Sales by Province", title_fmt)
    _write_df(ws3, province, wb, start_row=2)

    # ── Sheet 4: RM ─────────────────────────────────────────
    ws4 = wb.add_worksheet("By RM")
    ws4.write(0, 0, "Sales by Regional Manager", title_fmt)
    _write_df(ws4, rm, wb, start_row=2)

    # ── Sheet 5: ASE ────────────────────────────────────────
    ws5 = wb.add_worksheet("By ASE")
    ws5.write(0, 0, "Sales by Area Sales Executive", title_fmt)
    _write_df(ws5, ase, wb, start_row=2)

    # ── Sheet 6: Dealer Performance ─────────────────────────
    ws6 = wb.add_worksheet("Dealer Performance")
    ws6.write(0, 0, "Dealer-Level Sales Performance", title_fmt)
    _write_df(ws6, dealers, wb, start_row=2)

    # ── Sheet 7: Model Mix ──────────────────────────────────
    ws7 = wb.add_worksheet("Model Mix")
    ws7.write(0, 0, "Sales by Motorcycle Model", title_fmt)
    _write_df(ws7, models, wb, start_row=2)

    # ── Sheet 8: Province × Model Crosstab ──────────────────
    ws8 = wb.add_worksheet("Province x Model")
    ws8.write(0, 0, "Units Sold: Province × Model", title_fmt)
    ct_reset = crosstab.reset_index()
    _write_df(ws8, ct_reset, wb, start_row=2)

    # ── Sheet 9: Returns ────────────────────────────────────
    ws9 = wb.add_worksheet("Returns")
    ws9.write(0, 0, f"Returned Transactions ({len(returns)} rows)", title_fmt)
    if len(returns) > 0:
        _write_df(ws9, returns, wb, start_row=2)
    else:
        ws9.write(2, 0, "No returns found in this period.")

    wb.close()
    logger.info(f"Excel report written: {_OUTPUT_EXCEL}")


# ── 5. Main run function ─────────────────────────────────────

def run(refresh: bool = False) -> dict:
    """Execute Stage 1. Returns summary KPIs dict for dashboard use.

    Args:
        refresh: If True, re-run even if cached parquet exists.
    """
    if not refresh and _CLEAN_PARQUET.exists():
        logger.info("Stage 1 cached — loading from parquet. Pass refresh=True to recompute.")
        sold = pd.read_parquet(_CLEAN_PARQUET)
        vin_status = pd.read_parquet(_VIN_PARQUET)
        df = load_mcsi()  # still need full df for returns
    else:
        df = load_mcsi()
        vin_status = classify_vin_status(df)
        df = enrich_with_status(df, vin_status)

        sold = df[df["Status"] == "Sold"].copy()
        # Coerce mixed-type object columns to string to satisfy pyarrow
        for col in sold.select_dtypes(include="object").columns:
            sold[col] = sold[col].astype(str).replace("nan", pd.NA)
        sold.to_parquet(_CLEAN_PARQUET, index=False)
        vin_status.to_parquet(_VIN_PARQUET, index=False)
        logger.info(f"Saved clean parquet: {_CLEAN_PARQUET}")

    returned_vins = vin_status[vin_status["Status"] == "Returned"]
    returned = df[df["VIN"].isin(returned_vins["VIN"])]

    # ── Compute all analytics ─────────────────────────────
    kpis = summary_kpis(sold, returned, df)
    monthly = monthly_sales_trend(sold)
    province = hierarchy_summary(sold, ["Province"], "Province")
    rm = hierarchy_summary(sold, ["Province", "RM"], "RM")
    ase = hierarchy_summary(sold, ["Province", "RM", "ASE"], "ASE")
    dealers = dealer_performance(sold)
    models = model_mix(sold)
    crosstab = province_model_crosstab(sold)
    returns_df = returns_detail(df, vin_status)

    # ── Print summary to console ─────────────────────────
    logger.info("=" * 55)
    logger.info("STAGE 1 — MCSI EDA SUMMARY")
    logger.info("=" * 55)
    for k, v in kpis.items():
        logger.info(f"  {k:<35} {v:>15}")
    logger.info("")
    logger.info("Monthly trend (units sold):")
    for _, row in monthly.iterrows():
        bar = "█" * int(row["Units_Sold"] / 100)
        logger.info(f"  {row['Month']}  {row['Units_Sold']:>5,}  {bar}")
    logger.info("")
    logger.info("Top 5 provinces:")
    for _, row in province.head(5).iterrows():
        logger.info(f"  {row['Province']:<20} {row['Units_Sold']:>5,} units")
    logger.info("")
    logger.info("Model mix:")
    for _, row in models.iterrows():
        logger.info(f"  {row['Model']:<35} {row['Units_Sold']:>5,} units  ({row['Share_%']}%)")

    # ── Write Excel report ───────────────────────────────
    write_excel_report(kpis, monthly, province, rm, ase, dealers, models, crosstab, returns_df)

    # ── Data quality flags ───────────────────────────────
    months = kpis["Months of Data"]
    if months < 12:
        logger.warning(
            f"Only {months} months of data available. "
            "Stage 2 seasonality detection will be limited — "
            "recommend using ETS or Prophet without yearly seasonality."
        )

    # ── UIO.xlsx external fleet data ─────────────────────────
    load_uio_external()

    logger.info("Stage 1 complete.")
    return kpis


def load_uio_external() -> pd.DataFrame:
    """Load UIO.xlsx cohort-survival fleet data and save to parquet.

    Business meaning: UIO.xlsx contains bikes sold per year per model multiplied
    by their survival/retention rate, giving the estimated fleet still in
    operation today. This supplements MCSI (which only covers the recent period)
    with the full historical fleet going back to 2013.

    Output columns: model, total_sales_units, uio
    """
    if not _UIO_FILE.exists():
        logger.warning(f"UIO.xlsx not found at {_UIO_FILE} — skipping external UIO load")
        return pd.DataFrame(columns=["model", "total_sales_units", "uio"])

    logger.info(f"Loading {_UIO_FILE}")
    df = pd.read_excel(_UIO_FILE, engine="openpyxl")

    if "Model" not in df.columns or "UIO" not in df.columns:
        logger.error("UIO.xlsx missing expected columns 'Model' / 'UIO'")
        return pd.DataFrame(columns=["model", "total_sales_units", "uio"])

    result = df[["Model", "Total Sales Units", "UIO"]].copy()
    result.columns = ["model", "total_sales_units", "uio"]
    result = (
        result
        .dropna(subset=["model", "uio"])
        .query("uio > 0")
        .assign(
            model=lambda x: x["model"].astype(str).str.strip(),
            total_sales_units=lambda x: pd.to_numeric(x["total_sales_units"], errors="coerce").fillna(0).astype(int),
            uio=lambda x: pd.to_numeric(x["uio"], errors="coerce").fillna(0).round(0).astype(int),
        )
        .sort_values("uio", ascending=False)
        .reset_index(drop=True)
    )

    result.to_parquet(_UIO_PARQUET, index=False)
    logger.info(f"UIO external: {len(result)} models, total UIO = {result['uio'].sum():,}")
    logger.info(f"Saved: {_UIO_PARQUET}")
    return result
