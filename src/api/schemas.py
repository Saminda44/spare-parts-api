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
    rl_avg_order_reduction_pct: float  # average % order reduction vs rule-based


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
    ss_method_counts: dict[str, int]  # ML vs classical safety stock breakdown


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
    demand_category: str | None = None  # fast / slow / intermittent / non-moving
    demand_cluster: int | None = None  # K-Means cluster number
    demand_segment: str | None = None  # human-readable K-Means label
    in_ssop: bool | None = None  # appears in SSOP supersession table
    avg_monthly_demand: float
    cv: float
    p_zero: float  # probability of zero-demand month
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
    cv_hist: float  # historical coefficient of variation
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


class LocationRow(BaseModel):
    description: str
    qty: float
    value_lkr: float
    sku_count: int
    is_excluded: bool


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
    method: str  # forecast method used for this SKU's policy
    service_level: float  # target service level (e.g. 0.99 for critical)
    z_score: float  # safety stock z-score
    safety_stock: float
    ss_method: str  # "ML-Quantile" or "Classical"
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


class UIOAdjustedRow(BaseModel):
    material_9: str
    description: str
    abc: str
    policy_tier: str
    order_urgency: str
    primary_model: str  # bike model most associated with this SKU
    uio_current: int  # total MCSI units sold for that model (fleet proxy)
    uio_historical: int  # MCSI units sold during demand-history period
    uio_ratio: float  # uio_current / uio_historical (1.0 = no change)
    base_roq: float  # Stage-12 rule-based ROQ
    uio_adjusted_roq: float  # base_roq × uio_ratio (rounded)
    delta: float  # uio_adjusted_roq − base_roq
    delta_pct: float  # delta / base_roq × 100
    net_requirement: float
    stock_on_hand: float
    unit_value_lkr: float


class UIOAdjustedResponse(BaseModel):
    total_skus: int
    models_covered: int
    avg_uio_ratio: float
    rows: list[UIOAdjustedRow]


class UIOServicePlanRow(BaseModel):
    material_9: str
    description: str
    abc: str
    policy_tier: str
    order_urgency: str
    catalog_models: str  # catalog model(s) for this part, empty if not in catalog
    hist_months: int  # months of MC dealer order history used
    hist_demand_total: float  # total confirmed qty across history window
    avg_monthly: float  # avg confirmed qty per month from MC dealer orders
    service_plan_qty: float  # avg_monthly × horizon_months
    base_roq: float  # Stage-12 rule-based ROQ
    net_requirement: float  # current stock gap from policy
    recommended_order: float  # max(service_plan_qty, net_requirement)
    delta_vs_roq: float  # service_plan_qty − base_roq
    delta_pct: float  # delta_vs_roq / base_roq × 100
    stock_on_hand: float
    unit_value_lkr: float
    in_catalog: bool  # True if SKU appears in catalog_parts.parquet


class UIOServicePlanResponse(BaseModel):
    total_skus: int
    skus_with_history: int  # SKUs that have at least 1 month of MC order data
    horizon_months: int  # planning horizon used (default 5)
    avg_monthly_demand_total: float  # sum of avg_monthly across all SKUs
    total_service_plan_value: float  # service_plan_qty × unit_value_lkr (all SKUs)
    total_rule_based_value: float  # base_roq × unit_value_lkr (all SKUs)
    value_delta: float  # total_service_plan_value − total_rule_based_value
    rows: list[UIOServicePlanRow]


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
    avg_order_reduction_pct: float  # average % reduction vs rule-based
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
    external: list[UIOExternalRow]  # from UIO.xlsx (historical cohort model)
    mcsi: list[UIOSummaryRow]  # from MCSI.xlsx (recent VIN-verified sales)


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


class McsiColorRow(BaseModel):
    model: str
    color: str
    units_sold: int


class McsiRmRow(BaseModel):
    rm: str
    units_sold: int
    revenue_lkr: float
    dealer_count: int
    ase_count: int
    share_pct: float
    avg_revenue_per_unit: float


class McsiAseRow(BaseModel):
    ase: str
    rm: str
    units_sold: int
    revenue_lkr: float
    dealer_count: int
    share_pct: float


class McsiDistrictRow(BaseModel):
    province: str
    district: str
    units_sold: int
    revenue_lkr: float
    share_pct: float


class DealerModelRow(BaseModel):
    dealer: str
    dealer_code: str
    province: str
    rm: str
    ase: str
    total: int
    totals: dict[str, int]


class DealerModelMatrix(BaseModel):
    models: list[str]
    rows: list[DealerModelRow]


class McsiEdaResponse(BaseModel):
    kpis: McsiEdaKpis
    monthly_trend: list[McsiMonthlyPoint]
    by_year: list[McsiEdaYearRow]
    by_model: list[McsiEdaModelRow]
    by_province: list[McsiEdaProvinceRow]
    by_color: list[McsiColorRow]
    by_rm: list[McsiRmRow]
    by_ase: list[McsiAseRow]
    by_district: list[McsiDistrictRow]


class GeoMatrixRow(BaseModel):
    entity: str
    total: int
    totals: dict[str, int]


class GeoMatrixLevel(BaseModel):
    models: list[str]
    rows: list[GeoMatrixRow]


class GeoModelResponse(BaseModel):
    rm: GeoMatrixLevel
    ase: GeoMatrixLevel
    province: GeoMatrixLevel
    district: GeoMatrixLevel


class TargetBreakdownRow(BaseModel):
    model: str
    color: str
    historical_units: int
    share_pct: float
    allocated_units: int


class UpliftFactorsRow(BaseModel):
    month_key: str
    promotion_pct: float = 0.0
    new_model_pct: float = 0.0
    dealer_pct: float = 0.0
    pricing_pct: float = 0.0
    other_pct: float = 0.0

    @property
    def total_pct(self) -> float:
        return (
            self.promotion_pct
            + self.new_model_pct
            + self.dealer_pct
            + self.pricing_pct
            + self.other_pct
        )


class GeoColorResponse(BaseModel):
    rm: GeoMatrixLevel  # entity = RM,       keys = SAP color names
    ase: GeoMatrixLevel  # entity = ASE,       keys = SAP color names
    province: GeoMatrixLevel  # entity = Province,  keys = SAP color names
    district: GeoMatrixLevel  # entity = District,  keys = SAP color names


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
    total_value_lkr: float  # sum of Net Value (Item) — order received value
    confirmed_value_lkr: float = 0.0  # Confirmed Qty × unit price — actual sales value


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


class CategoryMixRow(BaseModel):
    segment: str
    order_lines: int
    value_lkr: float
    value_share_pct: float
    fill_rate_pct: float


class YoYGrowthRow(BaseModel):
    segment: str
    year_prev: int
    year_curr: int
    value_year_prev: float
    value_year_curr: float
    yoy_pct: float
    lines_year_prev: int
    lines_year_curr: int


class ProvincePerformanceRow(BaseModel):
    province: str
    order_value_lkr: float
    value_share_pct: float
    fill_rate_pct: float
    dealer_count: int


class ShortShipRow(BaseModel):
    material: str
    description: str
    short_qty: float
    fill_rate_pct: float
    occurrences: int


class FillRateBandRow(BaseModel):
    segment: str
    above_98: int
    between_95_98: int
    between_90_95: int
    below_90: int


# ---------------------------------------------------------------------------
# Orders EDA — per-segment analysis tables
# ---------------------------------------------------------------------------


class PartAnalysisRow(BaseModel):
    material: str
    description: str
    order_lines: int
    order_qty: float
    confirmed_qty: float
    total_value_lkr: float
    fill_rate_pct: float
    value_share_pct: float
    short_qty: float


class DealerPerfRow(BaseModel):
    dealer_code: str
    dealer_name: str
    province: str
    district: str
    rm: str
    ase: str
    po_lines: int
    order_value_lkr: float
    fill_rate_pct: float
    return_rate_pct: float
    dealer_tier: str
    value_share_pct: float


class RmPerfRow(BaseModel):
    rm: str
    province: str = ""
    unique_dealers: int
    po_lines: int
    order_value_lkr: float
    fill_rate_pct: float
    return_rate_pct: float
    value_share_pct: float


class AsePerfRow(BaseModel):
    ase: str
    rm: str
    province: str
    unique_dealers: int
    po_lines: int
    order_value_lkr: float
    fill_rate_pct: float
    return_rate_pct: float


class DistrictPerfRow(BaseModel):
    province: str
    district: str
    unique_dealers: int
    po_lines: int
    order_value_lkr: float
    fill_rate_pct: float
    value_share_pct: float
    return_rate_pct: float = 0.0


class ProvinceAnalysisRow(BaseModel):
    province: str
    unique_dealers: int
    po_lines: int
    order_value_lkr: float
    fill_rate_pct: float
    return_rate_pct: float
    value_share_pct: float


class McMonthlyCategoryPoint(BaseModel):
    period: str
    lubricant_lkr: float = 0.0
    battery_lkr: float = 0.0
    tyre_lkr: float = 0.0
    spare_parts_lkr: float = 0.0
    total_lkr: float = 0.0


class FulfillmentLineBucket(BaseModel):
    lines: int
    pct_of_lines: float
    order_qty: float
    confirmed_qty: float
    confirmed_value_lkr: float


class FulfillmentAnalysis(BaseModel):
    """Qty/value breakdown by fulfillment status at line and document level."""

    total_lines: int
    fully_confirmed: FulfillmentLineBucket  # Confirmed Qty >= Order Qty
    partially_confirmed: FulfillmentLineBucket  # 0 < Confirmed Qty < Order Qty
    fully_rejected: FulfillmentLineBucket  # Confirmed Qty == 0 (from rejection log)
    total_docs: int
    docs_fully_filled: int
    docs_fully_filled_pct: float
    docs_partially_filled: int
    docs_partially_filled_pct: float
    docs_complete_zero: int
    docs_complete_zero_pct: float


class OrdersEdaResponse(BaseModel):
    data_year: int = 0  # the year all KPIs are computed for (0 = all years)
    available_years: list[int] = []  # years available in the dataset for the picker
    total_po: int
    total_returns: int
    avg_fill_rate: float  # qty-based fill rate including rejected lines (true overall)
    avg_lead_time_days: float
    fill_rate_lt1_count: int
    total_order_value_lkr: float = (
        0.0  # sum of PO Net Value — "Order Received" (all lines incl. rejected)
    )
    total_confirmed_value_lkr: float = 0.0  # Confirmed Qty × unit price — "Total Sales"
    value_fill_rate_pct: float = 0.0  # total_confirmed_value / total_order_value × 100
    total_return_value_lkr: float = (
        0.0  # sum of Net Value for H-type Return Order lines only (not cancelled C-orders)
    )
    return_rate_value_pct: float = 0.0  # return_value / order_value × 100
    unfulfill_value_lkr: float = 0.0  # ordered value not confirmed (PO ordered − PO confirmed)
    sales_qty: float = 0.0  # total confirmed quantity on PO lines
    unique_skus: int = 0  # unique materials on PO lines
    total_po_documents: int = 0  # unique purchase order document count
    return_order_reasons: list[OrdersEdaRejectionReasonRow] = []  # reasons for return orders
    return_type_breakdown: dict[str, int] = {}  # {"Return Order": n, "Cancelled Order": n}
    top_dealers: list[OrdersEdaDealer]
    monthly_trend: list[OrdersEdaMonthlyPoint]
    rejections: list[OrdersEdaRejectionRow]
    orders_received_breakdown: dict[str, int]
    rejection_reasons: list[OrdersEdaRejectionReasonRow]
    fulfillment: FulfillmentAnalysis | None = None
    # Per-segment analysis tables (filtered by dealer_type + mc_category)
    part_analysis: list[PartAnalysisRow] = []
    dealer_perf: list[DealerPerfRow] = []
    rm_perf: list[RmPerfRow] = []
    ase_perf: list[AsePerfRow] = []
    district_perf: list[DistrictPerfRow] = []
    province_analysis: list[ProvinceAnalysisRow] = []
    # Business insights (computed from full dataset, not filtered by dealer_type)
    category_mix: list[CategoryMixRow] = []
    yoy_growth: list[YoYGrowthRow] = []
    province_perf: list[ProvincePerformanceRow] = []
    top_short_shipped: list[ShortShipRow] = []
    fill_rate_bands: list[FillRateBandRow] = []
    dealer_health_summary: dict[str, int] = {}
    pareto_summary: dict[str, int] = {}
    category_cross: dict[str, int] = {}
    rejection_rate_pct: float = 0.0
    fraud_alerts: list[dict[str, Any]] = []
    mc_monthly_category: list[McMonthlyCategoryPoint] = []


class SalesEdaMonthlyPoint(BaseModel):
    period: str
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    net_value_lkr: float = 0.0
    sale_qty: float = 0.0
    return_qty: float = 0.0
    order_received_lkr: float = 0.0  # PO value from orders.xlsx (dealer orders to Yamaha)


class SalesPartRow(BaseModel):
    material: str
    sale_lines: int = 0
    sale_qty: float = 0.0
    sale_value_lkr: float = 0.0
    return_lines: int = 0
    return_qty: float = 0.0
    return_value_lkr: float = 0.0
    net_qty: float = 0.0
    net_value_lkr: float = 0.0
    return_rate_pct: float = 0.0


class SalesDealerRow(BaseModel):
    dealer_name: str
    dealer_type: str = ""
    province: str = ""
    district: str = ""
    ase: str = ""
    rm: str = ""
    sale_qty: float = 0.0
    sale_value_lkr: float = 0.0
    return_qty: float = 0.0
    return_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    unique_skus: int = 0
    order_received_lkr: float = 0.0  # PO value placed by this dealer
    fulfillment_pct: float = 0.0  # sale_value / order_received × 100


class SalesRmRow(BaseModel):
    rm: str
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    sale_qty: float = 0.0
    dealer_count: int = 0
    unique_skus: int = 0


class SalesAseRow(BaseModel):
    ase: str
    rm: str = ""
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    sale_qty: float = 0.0
    dealer_count: int = 0
    unique_skus: int = 0


class SalesDistrictRow(BaseModel):
    district: str
    province: str = ""
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    sale_qty: float = 0.0
    dealer_count: int = 0
    unique_skus: int = 0
    order_received_lkr: float = 0.0


class SalesProvinceRow(BaseModel):
    province: str
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    sale_qty: float = 0.0
    dealer_count: int = 0
    unique_skus: int = 0


class SalesMcCategoryRow(BaseModel):
    mc_category: str
    sale_lines: int = 0
    sale_qty: float = 0.0
    sale_value_lkr: float = 0.0
    return_value_lkr: float = 0.0
    value_share_pct: float = 0.0
    return_rate_pct: float = 0.0
    unique_skus: int = 0


class SalesMcMonthlyPoint(BaseModel):
    period: str
    lubricant_lkr: float = 0.0
    battery_lkr: float = 0.0
    tyre_lkr: float = 0.0
    spare_parts_lkr: float = 0.0
    total_lkr: float = 0.0


class SalesEdaResponse(BaseModel):
    # Year context
    data_year: int = 0  # the year all KPIs are computed for (0 = unknown)
    available_years: list[int] = []  # years available in the dataset for the picker
    # KPIs
    total_sale_value_lkr: float = 0.0
    total_return_value_lkr: float = 0.0
    net_sale_value_lkr: float = 0.0
    return_rate_pct: float = 0.0
    total_sale_qty: float = 0.0
    total_return_qty: float = 0.0
    unique_parts: int = 0
    unique_dealers: int = 0
    total_sale_lines: int = 0
    total_return_lines: int = 0
    order_received_lkr: float = 0.0  # total PO value from orders.xlsx for same dealer scope
    fulfillment_pct: float = 0.0  # net_sale_value / order_received × 100
    # Trend
    monthly_trend: list[SalesEdaMonthlyPoint] = []
    # Part-wise
    part_analysis: list[SalesPartRow] = []
    # Hierarchy performance
    dealer_perf: list[SalesDealerRow] = []
    rm_perf: list[SalesRmRow] = []
    ase_perf: list[SalesAseRow] = []
    district_perf: list[SalesDistrictRow] = []
    province_perf: list[SalesProvinceRow] = []
    # MC category (MC dealer_type only)
    mc_category_mix: list[SalesMcCategoryRow] = []
    mc_monthly_category: list[SalesMcMonthlyPoint] = []


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


class AssociationRule(BaseModel):
    antecedents: list[str]
    consequents: list[str]
    support: float
    confidence: float
    lift: float
    conviction: float | None
    antecedent_support: float
    consequent_support: float


class FrequentItemset(BaseModel):
    items: list[str]
    support: float
    count: int


class CoOccurrenceGroup(BaseModel):
    items: list[str]
    count: int
    support: float
    lift: float  # geometric-mean lift across all items in the group


class LargeInvoice(BaseModel):
    billing_document: str
    billing_date: str
    payer: str
    items: list[str]
    item_count: int


class MarketBasketResponse(BaseModel):
    total_baskets: int
    multi_item_baskets: int
    total_unique_materials: int
    total_rules: int
    max_basket_size: int
    top_materials: list[dict[str, Any]]
    frequent_itemsets: list[FrequentItemset]
    rules: list[AssociationRule]
    groups: list[CoOccurrenceGroup]
    large_invoices: list[LargeInvoice]


# ── ML Market Basket schemas ──────────────────────────────────────────────────


class ItemSimilarity(BaseModel):
    item: str
    score: float
    method: str  # "item2vec" | "svd"


class ItemRecommendations(BaseModel):
    item: str
    freq: int
    item2vec: list[ItemSimilarity]
    svd: list[ItemSimilarity]


class CustomerRecommendation(BaseModel):
    payer: str
    purchased: list[str]
    recommendations: list[dict[str, Any]]  # [{item, score}]


class UmapPoint(BaseModel):
    item: str
    x: float
    y: float
    cluster: int
    freq: int


class MLCluster(BaseModel):
    cluster_id: int
    items: list[str]
    count: int


class MLModelInfo(BaseModel):
    embedding_dim: int
    n_baskets: int
    n_items_trained: int
    n_components_svd: int
    n_clusters: int


class MarketBasketMLResponse(BaseModel):
    item_recommendations: list[ItemRecommendations]
    customer_recommendations: list[CustomerRecommendation]
    umap_coords: list[UmapPoint]
    clusters: list[MLCluster]
    model_info: MLModelInfo
