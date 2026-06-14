import axios from "axios";

export const api = axios.create({ baseURL: "/api/v1", timeout: 30_000 });

// ── Types ──────────────────────────────────────────────────────────────────

export interface PipelineStatus {
  stage1_mcsi: boolean;
  stage2_sales_forecast: boolean;
  stage3_uio_forecast: boolean;
  stage4_orders_eda: boolean;
  stage5_sales_eda: boolean;
  stage6_part_master: boolean;
  stage7_stock_movements: boolean;
  stage8_spare_parts_eda: boolean;
  stage9_classification: boolean;
  stage10_demand_forecast: boolean;
  stage11_stock_tracker: boolean;
  stage12_policy: boolean;
  stage13_shipment_report: boolean;
  stage14_rl_policy: boolean;
}

export interface KpiData {
  total_skus: number;
  active_skus: number;
  stockout_skus: number;
  critical_skus: number;
  excess_skus: number;
  immediate_orders: number;
  soon_orders: number;
  planned_orders: number;
  total_order_value_lkr: number;
  total_stock_value_lkr: number;
  excess_stock_value_lkr: number;
  avg_coverage_months: number;
  sanity_flag_count: number;
  rl_avg_order_reduction_pct: number;
}

export interface OverviewData {
  kpis: KpiData;
  stock_status: Record<string, number>;
  abc_counts: Record<string, number>;
  tier_counts: Record<string, number>;
  urgency_counts: Record<string, number>;
  ss_method_counts: Record<string, number>;
}

export interface ClassificationRow {
  material_9: string;
  description: string;
  abc: string;
  xyz: string;
  fsn: string;
  abc_xyz_fsn: string;
  policy_tier: string;
  demand_category: string | null;
  demand_cluster: number | null;
  demand_segment: string | null;
  in_ssop: boolean | null;
  avg_monthly_demand: number;
  cv: number;
  p_zero: number;
  active_months: number;
  total_months: number;
  total_issue_qty: number;
  total_issue_value_lkr: number;
  total_return_qty: number;
  last_issue_date: string | null;
}

export interface ForecastRow {
  material_9: string;
  description: string;
  abc: string;
  xyz: string;
  fsn: string;
  policy_tier: string;
  method: string;
  forecast_m1: number;
  forecast_m2: number;
  forecast_m3: number;
  forecast_lt: number;
  avg_monthly_demand: number;
  demand_std_monthly: number;
  demand_std_lt: number;
  cv_hist: number;
  total_issue_value_lkr: number;
  active_months: number;
}

export interface MonthlyPoint {
  year_month_str: string;
  issue_qty: number;
  issue_value_lkr: number;
  return_qty: number;
  net_demand: number;
}

export interface InventoryRow {
  material_9: string;
  description: string;
  abc: string;
  xyz: string;
  fsn: string;
  policy_tier: string;
  stock_on_hand: number;
  stock_value_lkr: number;
  coverage_months: number;
  days_of_stock: number;
  stock_status: string;
  avg_monthly_demand: number;
  forecast_lt: number;
  method: string;
  total_receipts: number;
  total_issues: number;
  total_returns: number;
  last_movement_date: string | null;
}

export interface AtRiskRow {
  material_9: string;
  description: string;
  abc: string;
  policy_tier: string;
  stock_on_hand: number;
  stock_status: string;
  coverage_months: number;
  net_requirement: number;
  order_urgency: string;
  unit_value_lkr: number;
}

export interface ExcessRow {
  material_9: string;
  description: string;
  abc: string;
  policy_tier: string;
  stock_on_hand: number;
  stock_value_lkr: number;
  coverage_months: number;
  avg_monthly_demand: number;
}

export interface PolicyRow {
  material_9: string;
  description: string;
  abc: string;
  xyz: string;
  fsn: string;
  policy_tier: string;
  stock_status: string;
  coverage_months: number;
  days_of_stock: number;
  method: string;
  service_level: number;
  z_score: number;
  safety_stock: number;
  ss_method: string;
  rol: number;
  roq: number;
  net_requirement: number;
  order_urgency: string;
  unit_value_lkr: number;
  stock_on_hand: number;
  forecast_lt: number;
  cv: number;
  sanity_flag: boolean;
  sanity_note: string;
}

export interface SanityRow {
  material_9: string;
  description: string;
  abc: string;
  policy_tier: string;
  sanity_note: string;
  rol: number;
  roq: number;
  net_requirement: number;
  avg_monthly_demand: number;
  order_urgency: string;
}

export interface RLRow {
  material_9: string;
  description: string;
  abc: string;
  xyz: string;
  fsn: string;
  policy_tier: string;
  stock_status: string;
  coverage_months: number;
  avg_monthly_demand: number;
  unit_value_lkr: number;
  rl_multiplier: number;
  rl_recommended_qty: number;
  rule_based_roq: number;
  rl_flag: boolean;
}

export interface RLSummary {
  scored_skus: number;
  flagged_skus: number;
  avg_multiplier: number;
  avg_order_reduction_pct: number;
  skus_reduce_order: number;
  skus_increase_order: number;
  skus_unchanged: number;
}

// ── API calls ──────────────────────────────────────────────────────────────

export const fetchPipeline = () =>
  api.get<PipelineStatus>("/overview/pipeline").then(r => r.data);

export const fetchPipelineFreshness = () =>
  api.get<Record<string, string | null>>("/overview/pipeline/freshness").then(r => r.data);

export const fetchOverview = () =>
  api.get<OverviewData>("/overview/kpis").then(r => r.data);

export const fetchClassification = (params?: Record<string, unknown>) =>
  api.get<{
    total: number; rows: ClassificationRow[];
    abc_counts: Record<string, number>; xyz_counts: Record<string, number>;
    fsn_counts: Record<string, number>; segment_counts: Record<string, number>;
    demand_category_counts: Record<string, number>; tier_counts: Record<string, number>;
  }>("/classification", { params }).then(r => r.data);

export const fetchForecast = (params?: Record<string, unknown>) =>
  api.get<{ total: number; rows: ForecastRow[]; method_counts: Record<string, number> }>("/forecast", { params }).then(r => r.data);

export const fetchTrend = (sku?: string) =>
  api.get<MonthlyPoint[]>("/forecast/trend", { params: sku ? { sku } : {} }).then(r => r.data);

export const fetchInventory = (params?: Record<string, unknown>) =>
  api.get<{ total: number; rows: InventoryRow[]; status_counts: Record<string, number>; total_value_lkr: number; excess_value_lkr: number }>("/inventory", { params }).then(r => r.data);

export const fetchCoverageHistogram = () =>
  api.get<{ bin_start: number; bin_end: number; count: number }[]>("/inventory/coverage-histogram").then(r => r.data);

export const fetchAtRisk = (limit = 50) =>
  api.get<AtRiskRow[]>("/inventory/at-risk", { params: { limit } }).then(r => r.data);

export const fetchExcess = (limit = 100) =>
  api.get<ExcessRow[]>("/inventory/excess", { params: { limit } }).then(r => r.data);

export const fetchPolicy = (params?: Record<string, unknown>) =>
  api.get<{ total: number; rows: PolicyRow[]; urgency_counts: Record<string, number>; tier_counts: Record<string, number>; ss_method_counts: Record<string, number> }>("/policy", { params }).then(r => r.data);

export const fetchSanity = (limit = 200) =>
  api.get<SanityRow[]>("/policy/sanity", { params: { limit } }).then(r => r.data);

export const fetchRL = (params?: Record<string, unknown>) =>
  api.get<{ summary: RLSummary; rows: RLRow[] }>("/rl", { params }).then(r => r.data);

// ── Stage 1-3: Bikes ───────────────────────────────────────────────────────

export interface McsiModelRow   { model: string; count: number; pct: number; }
export interface McsiProvinceRow { province: string; count: number; }
export interface McsiMonthlyPoint { period: string; sold: number; revenue_lkr: number; }
export interface McsiSummary {
  total_sold: number;
  date_from: string; date_to: string;
  by_model: McsiModelRow[];
  by_province: McsiProvinceRow[];
  monthly_trend: McsiMonthlyPoint[];
}
export interface SalesForecastRow {
  period: string; forecast: number; lower_80: number; upper_80: number;
  actual: number | null; is_forecast: boolean;
  target: number | null; target_gap: number | null;
}
export interface UIOForecastRow {
  period: string; new_sales: number; uio_total: number; attrition: number;
  is_forecast: boolean; lower_80: number | null; upper_80: number | null;
}
export interface BikesData {
  mcsi: McsiSummary;
  sales_forecast: SalesForecastRow[];
  uio_forecast: UIOForecastRow[];
}

export const fetchBikes = () =>
  api.get<BikesData>("/bikes").then(r => r.data);

export interface SalesTargets {
  yearly_target: number;
  monthly_overrides: Record<string, number>;
}
export const fetchTargets = () =>
  api.get<SalesTargets>("/bikes/targets").then(r => r.data);
export const saveTargets = (body: SalesTargets) =>
  api.post<{ ok: boolean }>("/bikes/targets", body).then(r => r.data);

// ── Stage 1: MCSI EDA ─────────────────────────────────────────────────────

export interface McsiEdaKpis {
  total_vins: number; sold: number; returned: number; return_rate_pct: number;
  total_revenue_lkr: number; avg_monthly_units: number; avg_revenue_per_unit: number;
  active_provinces: number; active_dealers: number; models_sold: number;
  date_from: string; date_to: string; months_of_data: number;
}
export interface McsiEdaYearRow  { year: number; units_sold: number; revenue_lkr: number; avg_monthly: number; }
export interface McsiEdaModelRow { model: string; units_sold: number; revenue_lkr: number; share_pct: number; avg_revenue_per_unit: number; }
export interface McsiEdaProvinceRow { province: string; units_sold: number; revenue_lkr: number; share_pct: number; dealer_count: number; }
export interface McsiEdaData {
  kpis: McsiEdaKpis;
  monthly_trend: McsiMonthlyPoint[];
  by_year: McsiEdaYearRow[];
  by_model: McsiEdaModelRow[];
  by_province: McsiEdaProvinceRow[];
}
export const fetchMcsiEda = () => api.get<McsiEdaData>("/bikes/mcsi-eda").then(r => r.data);

export interface ModelForecastRow {
  period: string; model: string;
  actual: number | null; forecast: number; is_forecast: boolean;
}
export interface ModelForecastData { models: string[]; rows: ModelForecastRow[]; }
export const fetchModelForecast = () =>
  api.get<ModelForecastData>("/bikes/forecast/by-model").then(r => r.data);

export interface UIOExternalRow { model: string; total_sales_units: number; uio: number; }
export interface UIOSummaryRow  { model: string; uio: number; uio_pct: number; }
export interface UIOComparisonData {
  external: UIOExternalRow[];  // UIO.xlsx — full historical fleet (all model generations)
  mcsi: UIOSummaryRow[];       // MCSI.xlsx — recent VIN-verified bikes
}
export const fetchUIOComparison = () =>
  api.get<UIOComparisonData>("/bikes/uio").then(r => r.data);

export interface UIODemandRow {
  material_9: string; description: string; compatible_models: string | null;
  model_count: number; avg_monthly: number; hist_months: number;
  model_uio_total: number; replacement_freq_per_uio: number;
  projected_uio: number; uio_demand_monthly: number;
  uio_demand_leadtime: number; supply_pct_applied: number;
}
export interface UIODemandData {
  total_parts: number; parts_with_demand: number; projected_uio: number;
  supply_pct: number; lead_time_months: number;
  sum_uio_demand_monthly: number; sum_uio_demand_leadtime: number;
  rows: UIODemandRow[];
}
export const fetchUIODemand = (minDemand = 0) =>
  api.get<UIODemandData>("/bikes/uio-demand", { params: { min_demand: minDemand } }).then(r => r.data);

export interface DealerRow {
  province: string; rm: string; ase: string;
  dealer: string; dealer_code: string;
  units_sold: number; revenue_lkr: number;
}
export interface DealersData {
  total_dealers: number; total_units: number; total_revenue_lkr: number;
  available_years: number[];
  rows: DealerRow[];
}
export const fetchBikeDealers = (limit = 500, year?: number) =>
  api.get<DealersData>("/bikes/dealers", { params: { limit, ...(year ? { year } : {}) } }).then(r => r.data);

export interface CrosstabRow { province: string; totals: Record<string, number>; }
export interface CrosstabData { models: string[]; rows: CrosstabRow[]; }
export const fetchCrosstab = (year?: number) =>
  api.get<CrosstabData>("/bikes/crosstab", { params: year ? { year } : {} }).then(r => r.data);

// ── Stage 6: Part Master ───────────────────────────────────────────────────

export interface PartMasterRow {
  part_number: string; description: string;
  compatible_models: string | null;
  order_qty: number; eod_rate: number; stock: number; on_order: number;
  revised_order_qty: number; forecast_monthly_qty: number;
  superseded_from: string | null; has_supersession: boolean;
}
export interface SupersessionRow {
  requested_pn: string; current_pn: string; hops: number;
  current_description: string; old_description: string;
}
export interface PartMasterData {
  total: number; supersession_count: number;
  rows: PartMasterRow[]; supersessions: SupersessionRow[];
}

export const fetchParts = (params?: Record<string, unknown>) =>
  api.get<PartMasterData>("/parts", { params }).then(r => r.data);

export interface CatalogDerivedPartRow {
  part_no: string;
  description: string;
  compatible_models: string;
  source_count: number;
}
export interface CatalogDerivedPartsData {
  indexed: boolean;
  total: number;
  total_models: number;
  rows: CatalogDerivedPartRow[];
  models: string[];
}
export const fetchPartsFromCatalog = (params?: Record<string, unknown>) =>
  api.get<CatalogDerivedPartsData>("/parts/from-catalog", { params, timeout: 60_000 }).then(r => r.data);

// ── Stages 4,5,7,8: EDA ────────────────────────────────────────────────────

export interface OrdersEdaDealer { dealer: string; order_count: number; total_value_lkr: number; }
export interface OrdersEdaMonthlyPoint { period: string; po_count: number; return_count: number; total_value_lkr: number; }
export interface OrdersEdaRejectionRow {
  material: string; description: string; customer: string; document_date: string;
  order_qty: number; lost_qty: number; fill_rate: number;
}
export interface OrdersEdaRejectionReasonRow {
  reason: string; rejected_lines: number; rejected_qty: number; share_pct: number;
}
export interface OrdersEdaData {
  total_po: number; total_returns: number; avg_fill_rate: number;
  avg_lead_time_days: number; fill_rate_lt1_count: number;
  top_dealers: OrdersEdaDealer[];
  monthly_trend: OrdersEdaMonthlyPoint[];
  rejections: OrdersEdaRejectionRow[];
  orders_received_breakdown: { total_documents: number; fully_filled: number; partial_fill: number; complete_zero: number; };
  rejection_reasons: OrdersEdaRejectionReasonRow[];
}

export interface SalesEdaMonthlyPoint { period: string; revenue_lkr: number; qty: number; return_count: number; }
export interface SalesEdaData {
  total_revenue_lkr: number; total_units: number;
  by_channel: Record<string, number>; by_category: Record<string, number>;
  monthly_trend: SalesEdaMonthlyPoint[];
}

export interface MovementMonthlyPoint { period: string; movement_class: string; qty: number; value_lkr: number; }
export interface MovementsData {
  total_records: number; date_from: string; date_to: string;
  by_class: Record<string, number>; monthly_trend: MovementMonthlyPoint[];
}

export interface PZeroBin { bin: string; lo: number; hi: number; count: number; }
export interface IngestionLogRow { filename: string; rows_in: number; inserted: number; duplicates_skipped: number; }
export interface TopSkuRow {
  rank: number; material_9: string; description: string;
  demand_category: string | null;
  total_issue_value_lkr: number; total_issue_qty: number; cumulative_share_pct: number;
}
export interface IntermittentSkuRow {
  material_9: string; description: string; demand_category: string;
  p_zero: number; cv: number; active_months: number;
  avg_monthly_demand: number; total_issue_value_lkr: number;
}
export interface SparePartsEdaData {
  total_skus: number; in_ssop_count: number;
  total_issue_value_lkr: number; median_cv: number; median_p_zero: number;
  demand_category_counts: Record<string, number>;
  p_zero_bins: PZeroBin[];
  ingestion_summary: IngestionLogRow[];
  top_skus: TopSkuRow[];
  intermittent_skus: IntermittentSkuRow[];
}

export interface CatalogFile  { filename: string; rel_path: string; size_kb: number; }
export interface CatalogModel { model: string; pdf_count: number; files: CatalogFile[]; }
export interface CatalogData  { models: CatalogModel[]; total_pdfs: number; }
export const fetchCatalog = () => api.get<CatalogData>("/catalog").then(r => r.data);
export const catalogFileUrl = (rel_path: string) =>
  `/api/v1/catalog/file/${rel_path.split("/").map(encodeURIComponent).join("/")}`;
export interface ColourCode {
  abbreviation: string;  // e.g. "CM6"
  name: string;          // e.g. "CYAN METALLIC 6"
  code: string;          // e.g. "1344"
  is_model_colour: boolean;  // true when PDF marks it with (*)
}

export interface PdfTableResult {
  headers: string[]; rows: string[][]; total: number; sections: string[];
  variants: string[];
  colour_codes: ColourCode[];
  manufacture_year?: string;
  pages_scanned: number; sections_found: number; ocr_flagged: number; warnings: string[];
}
export const fetchPdfTables = (rel_path: string) =>
  api.get<PdfTableResult>(
    `/catalog/tables/${rel_path.split("/").map(encodeURIComponent).join("/")}`,
    { timeout: 120_000 },
  ).then(r => r.data);

// ── Catalogue Agent ────────────────────────────────────────────────────────

export interface AgentPartRow {
  figure: string;
  ref_no: string;
  part_no: string;
  description: string;
  qty: string;
  remarks: string;
  kind: "shared" | "colour_specific";
}

export interface AgentBuild {
  variant: string;
  colour: string;
  colour_name: string;
  colour_code: string;
  part_count: number;
  parts: AgentPartRow[];
}

export interface AgentColourEntry {
  name: string;
  code: string;
  is_model_colour: boolean;
  web_confirmed?: boolean;
  has_external_parts?: boolean;
}

export interface AgentResult {
  source_pdf: string;
  extracted_at: string;
  model: string;
  manufacture_year?: string;
  variants: string[];
  colour_legend: Record<string, AgentColourEntry>;
  rosters: Record<string, string[]>;
  builds: AgentBuild[];
  validated_colours?: string[];
  web_colour_names?: string[];
  warnings: string[];
}

export const fetchAgentBuilds = (rel_path: string, refresh = false) =>
  api.get<AgentResult>(
    `/catalog/agent/${rel_path.split("/").map(encodeURIComponent).join("/")}${refresh ? "?refresh=true" : ""}`,
    { timeout: 180_000 },
  ).then(r => r.data);

export const clearAgentCache = (rel_path: string) =>
  api.delete(
    `/catalog/agent/${rel_path.split("/").map(encodeURIComponent).join("/")}`,
  );

export interface ExtractionStatus {
  running: boolean;
  last_result: {
    ok: boolean; total_rows?: number; distinct_parts?: number;
    models?: number; parquet?: string; error?: string;
  } | null;
  parquet_exists: boolean;
  parquet_size_kb: number;
}
export const fetchExtractionStatus = () =>
  api.get<ExtractionStatus>("/catalog/extraction-status").then(r => r.data);
export const runBatchExtraction = () =>
  api.post<{ queued: boolean; message: string }>("/catalog/run-extraction").then(r => r.data);

export const fetchCatalogFolders = () =>
  api.get<string[]>("/catalog/folders").then(r => r.data);

export const uploadCatalogPdf = (file: File, folder: string) => {
  const form = new FormData();
  form.append("file", file);
  form.append("folder", folder);
  return api.post<{ rel_path: string; filename: string; folder: string }>(
    "/catalog/upload", form,
    { headers: { "Content-Type": "multipart/form-data" }, timeout: 60_000 },
  ).then(r => r.data);
};

export interface ExcelCatalogueFile { filename: string; stem: string; size_kb: number; }
export interface ExcelCatalogueData {
  filename: string;
  headers: string[];
  rows: string[][];
  total: number;
  sections: string[];
}
export const fetchExcelCatalogues = () =>
  api.get<ExcelCatalogueFile[]>("/catalog/excel").then(r => r.data);
export const fetchExcelCatalogue = (filename: string, section?: string, search?: string) =>
  api.get<ExcelCatalogueData>(`/catalog/excel/${encodeURIComponent(filename)}`, {
    params: { ...(section ? { section } : {}), ...(search ? { search } : {}) },
  }).then(r => r.data);

export interface CatalogCoverageRow { model: string; pdf_count: number; distinct_parts: number; ocr_pages: number; }
export interface CatalogCoverageData {
  extracted: boolean; total_part_references: number;
  distinct_parts: number; distinct_models: number;
  rows: CatalogCoverageRow[];
}
export interface CatalogPartRow { part_number: string; source_file: string; ocr_used: boolean; }
export const fetchCatalogCoverage = () => api.get<CatalogCoverageData>("/catalog/coverage").then(r => r.data);
export const fetchCatalogParts = (model: string, limit = 500) =>
  api.get<CatalogPartRow[]>(`/catalog/parts/${encodeURIComponent(model)}`, { params: { limit } }).then(r => r.data);

export type DealerType = "MC" | "OBM" | "ALL";

export const fetchOrdersEda   = (dealerType: DealerType = "MC") =>
  api.get<OrdersEdaData>("/eda/orders", { params: { dealer_type: dealerType } }).then(r => r.data);
export const fetchSalesEda    = (dealerType: DealerType = "MC") =>
  api.get<SalesEdaData>("/eda/sales", { params: { dealer_type: dealerType } }).then(r => r.data);
export const fetchMovements   = () => api.get<MovementsData>("/eda/movements").then(r => r.data);
export const fetchSparePartsEda = () => api.get<SparePartsEdaData>("/eda/spare-parts").then(r => r.data);
