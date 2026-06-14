"""Pydantic response schemas for the Inventory Optimization API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PipelineStatus(BaseModel):
    stage1_mcsi: bool
    stage2_sales_forecast: bool
    stage3_uio_forecast: bool
    stage4_orders_eda: bool
    stage5_sales_eda: bool
    stage6_part_master: bool
    stage7_stock_movements: bool
    stage8_spare_parts_eda: bool
    stage9_classification: bool
    stage10_demand_forecast: bool
    stage11_stock_tracker: bool
    stage12_policy: bool
    stage13_shipment_report: bool
    stage14_rl_policy: bool


# ---------------------------------------------------------------------------
# Overview / KPIs
# ---------------------------------------------------------------------------

class KpiResponse(BaseModel):
    total_skus: int
    active_skus: int
    stockout_skus: int
    critical_skus: int
    excess_skus: int
    immediate_orders: int
    soon_orders: int
    planned_orders: int
    total_order_value_lkr: float
    total_stock_value_lkr: float
    excess_stock_value_lkr: float
    avg_coverage_months: float
    sanity_flag_count: int
    rl_avg_order_reduction_pct: float   # average % order reduction vs rule-based


class StockStatusBreakdown(BaseModel):
    stockout: int = 0
    critical: int = 0
    low: int = 0
    ok: int = 0
    excess: int = 0


class OverviewResponse(BaseModel):
    kpis: KpiResponse
    stock_status: StockStatusBreakdown
    abc_counts: dict[str, int]
    tier_counts: dict[str, int]
    urgency_counts: dict[str, int]
    ss_method_counts: dict[str, int]   # ML vs classical safety stock breakdown


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class ClassificationRow(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    abc_xyz_fsn: str
    policy_tier: str
    demand_category: str | None = None   # fast / slow / intermittent / non-moving
    demand_cluster: int | None = None    # K-Means cluster number
    demand_segment: str | None = None    # human-readable K-Means label
    in_ssop: bool | None = None          # appears in SSOP supersession table
    avg_monthly_demand: float
    cv: float
    p_zero: float                        # probability of zero-demand month
    active_months: int
    total_months: int
    total_issue_qty: float
    total_issue_value_lkr: float
    total_return_qty: float
    last_issue_date: str | None = None


class ClassificationResponse(BaseModel):
    total: int
    rows: list[ClassificationRow]
    abc_counts: dict[str, int]
    xyz_counts: dict[str, int]
    fsn_counts: dict[str, int]
    segment_counts: dict[str, int]
    demand_category_counts: dict[str, int]
    tier_counts: dict[str, int]


# ---------------------------------------------------------------------------
# Demand Forecast
# ---------------------------------------------------------------------------

class ForecastRow(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    policy_tier: str
    method: str
    forecast_m1: float
    forecast_m2: float
    forecast_m3: float
    forecast_lt: float
    avg_monthly_demand: float
    demand_std_monthly: float
    demand_std_lt: float
    cv_hist: float                       # historical coefficient of variation
    total_issue_value_lkr: float
    active_months: int


class ForecastResponse(BaseModel):
    total: int
    rows: list[ForecastRow]
    method_counts: dict[str, int]


class MonthlyDemandPoint(BaseModel):
    year_month_str: str
    issue_qty: float
    issue_value_lkr: float
    return_qty: float
    net_demand: float


# ---------------------------------------------------------------------------
# Inventory Status
# ---------------------------------------------------------------------------

class InventoryRow(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    policy_tier: str
    stock_on_hand: float
    stock_value_lkr: float
    coverage_months: float
    days_of_stock: float
    stock_status: str
    avg_monthly_demand: float
    forecast_lt: float
    method: str
    total_receipts: float
    total_issues: float
    total_returns: float
    last_movement_date: str | None = None


class InventoryResponse(BaseModel):
    total: int
    rows: list[InventoryRow]
    status_counts: dict[str, int]
    total_value_lkr: float
    excess_value_lkr: float


class CoverageHistogramBucket(BaseModel):
    bin_start: float
    bin_end: float
    count: int


class AtRiskRow(BaseModel):
    material_9: str
    description: str
    abc: str
    policy_tier: str
    stock_on_hand: float
    stock_status: str
    coverage_months: float
    net_requirement: float
    order_urgency: str
    unit_value_lkr: float


class ExcessRow(BaseModel):
    material_9: str
    description: str
    abc: str
    policy_tier: str
    stock_on_hand: float
    stock_value_lkr: float
    coverage_months: float
    avg_monthly_demand: float


# ---------------------------------------------------------------------------
# Policy / Orders
# ---------------------------------------------------------------------------

class PolicyRow(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    policy_tier: str
    stock_status: str
    coverage_months: float
    days_of_stock: float
    method: str                    # forecast method used for this SKU's policy
    service_level: float           # target service level (e.g. 0.99 for critical)
    z_score: float                 # safety stock z-score
    safety_stock: float
    ss_method: str                 # "ML-Quantile" or "Classical"
    rol: float
    roq: float
    net_requirement: float
    order_urgency: str
    unit_value_lkr: float
    stock_on_hand: float
    forecast_lt: float
    cv: float
    sanity_flag: bool
    sanity_note: str


class PolicyResponse(BaseModel):
    total: int
    rows: list[PolicyRow]
    urgency_counts: dict[str, int]
    tier_counts: dict[str, int]
    ss_method_counts: dict[str, int]


class SanityRow(BaseModel):
    material_9: str
    description: str
    abc: str
    policy_tier: str
    sanity_note: str
    rol: float
    roq: float
    net_requirement: float
    avg_monthly_demand: float
    order_urgency: str


# ---------------------------------------------------------------------------
# RL Policy
# ---------------------------------------------------------------------------

class RLRow(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    policy_tier: str
    stock_status: str
    coverage_months: float
    avg_monthly_demand: float
    unit_value_lkr: float
    rl_multiplier: float
    rl_recommended_qty: float
    rule_based_roq: float
    rl_flag: bool


class RLSummary(BaseModel):
    scored_skus: int
    flagged_skus: int
    avg_multiplier: float
    avg_order_reduction_pct: float   # average % reduction vs rule-based
    skus_reduce_order: int
    skus_increase_order: int
    skus_unchanged: int


class RLResponse(BaseModel):
    summary: RLSummary
    rows: list[RLRow]


# ---------------------------------------------------------------------------
# SKU detail (aggregates all stages)
# ---------------------------------------------------------------------------

class SKUDetail(BaseModel):
    material_9: str
    description: str
    abc: str
    xyz: str
    fsn: str
    abc_xyz_fsn: str
    policy_tier: str
    demand_category: str | None = None
    demand_segment: str | None = None
    in_ssop: bool | None = None
    avg_monthly_demand: float
    demand_std_monthly: float
    p_zero: float
    cv: float
    active_months: int
    total_issue_qty: float
    total_issue_value_lkr: float
    # Stock
    stock_on_hand: float
    stock_value_lkr: float
    stock_status: str
    coverage_months: float
    days_of_stock: float
    total_receipts: float
    total_issues: float
    total_returns: float
    last_movement_date: str | None = None
    # Forecast
    method: str
    forecast_m1: float
    forecast_m2: float
    forecast_m3: float
    forecast_lt: float
    # Policy
    service_level: float
    z_score: float
    safety_stock: float
    ss_method: str
    rol: float
    roq: float
    net_requirement: float
    order_urgency: str
    unit_value_lkr: float
    sanity_flag: bool
    sanity_note: str
    # RL
    rl_multiplier: float | None = None
    rl_recommended_qty: float | None = None
    rl_flag: bool | None = None
    # Demand history
    monthly_demand: list[MonthlyDemandPoint] = []


# ---------------------------------------------------------------------------
# Bikes — Stages 1-3 (MCSI, Unit Sales Forecast, UIO Forecast)
# ---------------------------------------------------------------------------

class McsiModelRow(BaseModel):
    model: str
    count: int
    pct: float


class McsiProvinceRow(BaseModel):
    province: str
    count: int


class McsiMonthlyPoint(BaseModel):
    period: str
    sold: int
    revenue_lkr: float = 0.0


class McsiSummary(BaseModel):
    total_sold: int
    date_from: str
    date_to: str
    by_model: list[McsiModelRow]
    by_province: list[McsiProvinceRow]
    monthly_trend: list[McsiMonthlyPoint]


class SalesForecastRow(BaseModel):
    period: str
    forecast: int
    lower_80: int
    upper_80: int
    actual: float | None = None
    is_forecast: bool
    target: float | None = None
    target_gap: float | None = None


class UIOForecastRow(BaseModel):
    period: str
    new_sales: int
    uio_total: int
    attrition: int
    is_forecast: bool
    lower_80: float | None = None
    upper_80: float | None = None


class BikesResponse(BaseModel):
    mcsi: McsiSummary
    sales_forecast: list[SalesForecastRow]
    uio_forecast: list[UIOForecastRow]


class UIOExternalRow(BaseModel):
    model: str
    total_sales_units: int
    uio: int


class UIOSummaryRow(BaseModel):
    model: str
    uio: int
    uio_pct: float


class UIOComparisonResponse(BaseModel):
    external: list[UIOExternalRow]   # from UIO.xlsx (historical cohort model)
    mcsi: list[UIOSummaryRow]        # from MCSI.xlsx (recent VIN-verified sales)


class UIODemandRow(BaseModel):
    material_9: str
    description: str
    compatible_models: str | None
    model_count: int
    avg_monthly: float
    hist_months: int
    model_uio_total: float
    replacement_freq_per_uio: float
    projected_uio: float
    uio_demand_monthly: float
    uio_demand_leadtime: float
    supply_pct_applied: float


class UIODemandResponse(BaseModel):
    total_parts: int
    parts_with_demand: int
    projected_uio: float
    supply_pct: float
    lead_time_months: int
    sum_uio_demand_monthly: float
    sum_uio_demand_leadtime: float
    rows: list[UIODemandRow]


class ModelForecastRow(BaseModel):
    period: str
    model: str
    actual: int | None
    forecast: int
    is_forecast: bool


class ModelForecastResponse(BaseModel):
    models: list[str]
    rows: list[ModelForecastRow]


class DealerRow(BaseModel):
    province: str
    rm: str
    ase: str
    dealer: str
    dealer_code: str
    units_sold: int
    revenue_lkr: float


class DealersResponse(BaseModel):
    total_dealers: int
    total_units: int
    total_revenue_lkr: float
    available_years: list[int]
    rows: list[DealerRow]


class CrosstabRow(BaseModel):
    province: str
    totals: dict[str, int]


class CrosstabResponse(BaseModel):
    models: list[str]
    rows: list[CrosstabRow]


# ---------------------------------------------------------------------------
# MCSI EDA — Stage 1 detailed analytics
# ---------------------------------------------------------------------------

class McsiEdaKpis(BaseModel):
    total_vins: int
    sold: int
    returned: int
    return_rate_pct: float
    total_revenue_lkr: float
    avg_monthly_units: float
    avg_revenue_per_unit: float
    active_provinces: int
    active_dealers: int
    models_sold: int
    date_from: str
    date_to: str
    months_of_data: int


class McsiEdaYearRow(BaseModel):
    year: int
    units_sold: int
    revenue_lkr: float
    avg_monthly: float


class McsiEdaModelRow(BaseModel):
    model: str
    units_sold: int
    revenue_lkr: float
    share_pct: float
    avg_revenue_per_unit: float


class McsiEdaProvinceRow(BaseModel):
    province: str
    units_sold: int
    revenue_lkr: float
    share_pct: float
    dealer_count: int


class McsiEdaResponse(BaseModel):
    kpis: McsiEdaKpis
    monthly_trend: list[McsiMonthlyPoint]
    by_year: list[McsiEdaYearRow]
    by_model: list[McsiEdaModelRow]
    by_province: list[McsiEdaProvinceRow]


# ---------------------------------------------------------------------------
# Part Master — Stage 6
# ---------------------------------------------------------------------------

class PartMasterRow(BaseModel):
    part_number: str
    description: str
    compatible_models: str | None = None
    order_qty: int
    eod_rate: float
    stock: float
    on_order: float
    revised_order_qty: int
    forecast_monthly_qty: float
    superseded_from: str | None = None
    has_supersession: bool


class SupersessionRow(BaseModel):
    requested_pn: str
    current_pn: str
    hops: int
    current_description: str
    old_description: str


class PartMasterResponse(BaseModel):
    total: int
    supersession_count: int
    rows: list[PartMasterRow]
    supersessions: list[SupersessionRow]


# ---------------------------------------------------------------------------
# EDA — Stages 4, 5, 7, 8
# ---------------------------------------------------------------------------

class OrdersEdaDealer(BaseModel):
    dealer: str
    order_count: int
    total_value_lkr: float


class OrdersEdaMonthlyPoint(BaseModel):
    period: str
    po_count: int
    return_count: int
    total_value_lkr: float


class OrdersEdaRejectionRow(BaseModel):
    material: str
    description: str
    customer: str
    document_date: str
    order_qty: float
    lost_qty: float
    fill_rate: float


class OrdersEdaRejectionReasonRow(BaseModel):
    reason: str
    rejected_lines: int
    rejected_qty: float
    share_pct: float


class OrdersEdaResponse(BaseModel):
    total_po: int
    total_returns: int
    avg_fill_rate: float
    avg_lead_time_days: float
    fill_rate_lt1_count: int
    top_dealers: list[OrdersEdaDealer]
    monthly_trend: list[OrdersEdaMonthlyPoint]
    rejections: list[OrdersEdaRejectionRow]
    orders_received_breakdown: dict[str, int]
    rejection_reasons: list[OrdersEdaRejectionReasonRow]


class SalesEdaMonthlyPoint(BaseModel):
    period: str
    revenue_lkr: float
    qty: int
    return_count: int


class SalesEdaResponse(BaseModel):
    total_revenue_lkr: float
    total_units: int
    by_channel: dict[str, float]
    by_category: dict[str, float]
    monthly_trend: list[SalesEdaMonthlyPoint]


class MovementMonthlyPoint(BaseModel):
    period: str
    movement_class: str
    qty: float
    value_lkr: float


class MovementsResponse(BaseModel):
    total_records: int
    date_from: str
    date_to: str
    by_class: dict[str, int]
    monthly_trend: list[MovementMonthlyPoint]


class CatalogFile(BaseModel):
    filename: str
    rel_path: str
    size_kb: float


class CatalogModel(BaseModel):
    model: str
    pdf_count: int
    files: list[CatalogFile]


class CatalogResponse(BaseModel):
    models: list[CatalogModel]
    total_pdfs: int


class CatalogCoverageRow(BaseModel):
    model: str
    pdf_count: int
    distinct_parts: int
    ocr_pages: int


class CatalogCoverageResponse(BaseModel):
    extracted: bool
    total_part_references: int
    distinct_parts: int
    distinct_models: int
    rows: list[CatalogCoverageRow]


class CatalogPartRow(BaseModel):
    part_number: str
    source_file: str
    ocr_used: bool


class TopSkuRow(BaseModel):
    rank: int
    material_9: str
    description: str
    demand_category: str | None
    total_issue_value_lkr: float
    total_issue_qty: float
    cumulative_share_pct: float


class IntermittentSkuRow(BaseModel):
    material_9: str
    description: str
    demand_category: str
    p_zero: float
    cv: float
    active_months: int
    avg_monthly_demand: float
    total_issue_value_lkr: float


class SparePartsEdaResponse(BaseModel):
    total_skus: int
    in_ssop_count: int
    total_issue_value_lkr: float
    median_cv: float
    median_p_zero: float
    demand_category_counts: dict[str, int]
    p_zero_bins: list[dict[str, Any]]
    ingestion_summary: list[dict[str, Any]]
    top_skus: list[TopSkuRow]
    intermittent_skus: list[IntermittentSkuRow]
