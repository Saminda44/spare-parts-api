import { useCallback, useEffect, useState } from "react";
import {
  Search, X, Loader2, AlertCircle,
  Package,
} from "lucide-react";
import {
  fetchPurchaseRecommendationSummary,
  fetchPurchaseRecommendations,
  type PurchaseRecommendationSummary,
  type PurchaseRecommendationRow,
} from "../api/client";

// ── KPI card ──────────────────────────────────────────────────────────────────

function Kpi({
  label, value, sub, accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm px-5 py-4 space-y-0.5">
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className={`text-2xl font-bold leading-tight ${accent ?? "text-slate-800"}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

// ── Priority badge ────────────────────────────────────────────────────────────

const PRIORITY_STYLE: Record<number, string> = {
  1: "bg-red-100 text-red-700",
  2: "bg-orange-100 text-orange-700",
  3: "bg-yellow-100 text-yellow-700",
  4: "bg-green-100 text-green-700",
};

const PRIORITY_LABEL: Record<number, string> = {
  1: "Critical", 2: "High", 3: "Medium", 4: "Low",
};

function PriorityBadge({ priority }: { priority: number }) {
  return (
    <span
      className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded whitespace-nowrap ${
        PRIORITY_STYLE[priority] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {PRIORITY_LABEL[priority] ?? `P${priority}`}
    </span>
  );
}

// ── Action badge ──────────────────────────────────────────────────────────────

const ACTION_STYLE: Record<string, string> = {
  "Order Now":  "bg-red-50 text-red-700 border border-red-200",
  "Plan Order": "bg-orange-50 text-orange-700 border border-orange-200",
  "Monitor":    "bg-slate-100 text-slate-500 border border-slate-200",
};

function ActionBadge({ action }: { action: string }) {
  return (
    <span
      className={`inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${
        ACTION_STYLE[action] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {action}
    </span>
  );
}

// ── Fill-rate colour ──────────────────────────────────────────────────────────

function fillRateColor(pct: number): string {
  if (pct >= 75) return "text-emerald-600";
  if (pct >= 40) return "text-amber-600";
  return "text-red-600";
}

// ── Error / not-yet-run state ─────────────────────────────────────────────────

function NotBuilt({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
      <AlertCircle size={40} className="text-red-300" />
      <div>
        <p className="text-base font-semibold text-slate-700">Data not available</p>
        <p className="text-sm text-slate-400 mt-1 max-w-lg">{message}</p>
        <code className="mt-3 block text-xs bg-slate-100 text-slate-600 px-3 py-2 rounded font-mono">
          python -m scripts.run_module 6 --save
        </code>
      </div>
    </div>
  );
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 100;

// ── Main page ──────────────────────────────────────────────────────────────────

export function PurchaseRecommendation() {
  const [summary,         setSummary]         = useState<PurchaseRecommendationSummary | null>(null);
  const [rows,            setRows]            = useState<PurchaseRecommendationRow[]>([]);
  const [total,           setTotal]           = useState(0);
  const [page,            setPage]            = useState(0);
  const [loadingSum,      setLoadingSum]      = useState(true);
  const [loadingRows,     setLoadingRows]     = useState(true);
  const [priorityFilter,  setPriorityFilter]  = useState<string>("");
  const [classFilter,     setClassFilter]     = useState<string>("");
  const [search,          setSearch]          = useState("");
  const [debSearch,       setDebSearch]       = useState("");
  const [error,           setError]           = useState<string | null>(null);

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => {
      setDebSearch(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  // Load summary KPIs
  useEffect(() => {
    setLoadingSum(true);
    setError(null);
    fetchPurchaseRecommendationSummary()
      .then(setSummary)
      .catch(e => {
        const detail: string =
          e?.response?.data?.detail ?? e?.message ?? "Failed to load summary";
        setError(detail);
      })
      .finally(() => setLoadingSum(false));
  }, []);

  // Load recommendation table (re-runs when filters / page change)
  const loadRows = useCallback(() => {
    setLoadingRows(true);
    const params: Record<string, unknown> = {
      limit:  PAGE_SIZE,
      offset: page * PAGE_SIZE,
    };
    if (priorityFilter) params.priority      = Number(priorityFilter);
    if (classFilter)    params.demand_class  = classFilter;
    if (debSearch)      params.search        = debSearch;

    fetchPurchaseRecommendations(params)
      .then(d => {
        setRows(d.rows);
        setTotal(d.total);
      })
      .catch(e => {
        const detail: string =
          e?.response?.data?.detail ?? e?.message ?? "Failed to load recommendations";
        setError(detail);
      })
      .finally(() => setLoadingRows(false));
  }, [page, priorityFilter, classFilter, debSearch]);

  useEffect(() => { loadRows(); }, [loadRows]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Error / not-built state
  if (error && !loadingSum) {
    return (
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="space-y-2 mb-6">
          <h2 className="text-xl font-bold text-slate-800">Purchase Recommendation</h2>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-8">
          <NotBuilt message={error} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 overflow-y-auto">
      <div className="space-y-6">

        {/* ── Header ── */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-slate-800">Purchase Recommendation</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {summary
                ? `${summary.total_skus_to_order.toLocaleString()} SKUs to order · Module 6 Decision Support`
                : "Loading…"}
            </p>
          </div>
        </div>

        {/* ── KPI cards ── */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Kpi
            label="SKUs to Order"
            value={loadingSum ? "…" : (summary?.total_skus_to_order ?? 0).toLocaleString()}
            sub="order_qty > 0 after all constraints"
          />
          <Kpi
            label="Critical"
            value={loadingSum ? "…" : (summary?.critical_count ?? 0).toLocaleString()}
            sub="priority 1 — order immediately"
            accent="text-red-600"
          />
          <Kpi
            label="Stockout Risk"
            value={loadingSum ? "…" : (summary?.stockout_risk_count ?? 0).toLocaleString()}
            sub="P(stockout) > 30%"
            accent="text-orange-600"
          />
          <Kpi
            label="Fill Rate"
            value={loadingSum ? "…" : `${(summary?.weighted_fill_rate_pct ?? 0).toFixed(1)}%`}
            sub="demand-weighted across all SKUs"
            accent={
              (summary?.weighted_fill_rate_pct ?? 0) >= 70
                ? "text-emerald-600"
                : "text-amber-600"
            }
          />
        </div>

        {/* ── Recommendations table card ── */}
        <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">

          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-3">

            {/* Part number search */}
            <div className="relative flex-1 min-w-[200px]">
              <Search
                size={13}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                className="w-full pl-8 pr-7 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                placeholder="Search part no…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && (
                <button
                  onClick={() => setSearch("")}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            {/* Priority filter */}
            <select
              value={priorityFilter}
              onChange={e => { setPriorityFilter(e.target.value); setPage(0); }}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
            >
              <option value="">All Priorities</option>
              <option value="1">Critical (P1)</option>
              <option value="2">High (P2)</option>
              <option value="3">Medium (P3)</option>
              <option value="4">Low (P4)</option>
            </select>

            {/* Demand class filter */}
            <select
              value={classFilter}
              onChange={e => { setClassFilter(e.target.value); setPage(0); }}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
            >
              <option value="">All Classes</option>
              <option value="A">Class A</option>
              <option value="B">Class B</option>
              <option value="C">Class C</option>
            </select>

            <span className="text-xs text-slate-400 ml-auto">
              {total.toLocaleString()} result{total !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Table */}
          {loadingRows ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3 text-slate-400">
              <Loader2 size={28} className="animate-spin" />
              <p className="text-sm">Loading recommendations…</p>
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2 text-slate-400">
              <Package size={36} className="text-slate-200" />
              <p className="text-sm font-medium">
                No recommendations match the current filters
              </p>
            </div>
          ) : (
            <div
              className="overflow-auto rounded-xl border border-slate-200 shadow-sm"
              style={{ maxHeight: "60vh" }}
            >
              <table className="w-full text-sm border-collapse">
                <thead className="sticky top-0 z-10">
                  <tr style={{ background: "#1B3A6B" }}>
                    {[
                      "Part No.",
                      "Order Qty",
                      "Priority",
                      "Action",
                      "Class",
                      "Fill Rate",
                      "Demand / mo",
                      "Constraint",
                    ].map(h => (
                      <th
                        key={h}
                        className="py-2.5 px-3 text-left text-xs font-bold text-white whitespace-nowrap border-r border-blue-800 last:border-r-0"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr
                      key={r.part_no}
                      className={i % 2 === 0 ? "bg-white" : "bg-slate-50/60"}
                    >
                      {/* Part No. */}
                      <td className="py-2 px-3 border-b border-slate-100 font-mono text-xs text-slate-700 whitespace-nowrap">
                        {r.part_no}
                      </td>

                      {/* Order Qty */}
                      <td className="py-2 px-3 border-b border-slate-100 text-right font-semibold text-slate-800 whitespace-nowrap">
                        {r.order_qty.toLocaleString()}
                      </td>

                      {/* Priority */}
                      <td className="py-2 px-3 border-b border-slate-100 whitespace-nowrap">
                        <PriorityBadge priority={r.priority} />
                      </td>

                      {/* Action */}
                      <td className="py-2 px-3 border-b border-slate-100 whitespace-nowrap">
                        <ActionBadge action={r.action} />
                      </td>

                      {/* ABC Class */}
                      <td className="py-2 px-3 border-b border-slate-100 whitespace-nowrap">
                        <span className="text-xs font-mono text-slate-600">{r.abc}</span>
                      </td>

                      {/* Fill Rate */}
                      <td className="py-2 px-3 border-b border-slate-100 text-right whitespace-nowrap">
                        <span
                          className={`text-xs font-semibold ${fillRateColor(r.fill_rate_pct)}`}
                        >
                          {r.fill_rate_pct.toFixed(1)}%
                        </span>
                      </td>

                      {/* Monthly demand */}
                      <td className="py-2 px-3 border-b border-slate-100 text-right text-xs text-slate-600 whitespace-nowrap">
                        {r.mean_monthly_demand.toFixed(1)}
                      </td>

                      {/* Binding constraint */}
                      <td className="py-2 px-3 border-b border-slate-100 whitespace-nowrap">
                        <span className="text-xs text-slate-400 capitalize">
                          {r.binding_constraint || "—"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-slate-400">
                Page {page + 1} of {totalPages} &nbsp;·&nbsp; {total.toLocaleString()} total rows
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage(p => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="px-3 py-1 text-xs rounded border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="px-3 py-1 text-xs rounded border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          )}

          {/* Footer note */}
          {!loadingRows && rows.length > 0 && (
            <p className="text-xs text-slate-400 pt-1">
              Showing {Math.min(rows.length, PAGE_SIZE)} of {total.toLocaleString()} recommendations.
              Container utilisation estimate: {(summary?.container_utilization_pct ?? 0).toFixed(1)}% (0.002 m³/unit avg).
            </p>
          )}
        </div>

      </div>
    </div>
  );
}
