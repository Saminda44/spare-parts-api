import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, LineChart, Line, Legend,
} from "recharts";
import {
  fetchUIOComparison, fetchM1UIOForecast,
  type UIOComparisonData, type M1UIOForecastResponse,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";

const MODEL_COLORS = [
  "#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED",
  "#06B6D4","#F97316","#94A3B8","#10B981","#EC4899",
];

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

function SourceBadge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide border ${color}`}>
      {label}
    </span>
  );
}

export function UIO() {
  const [data,       setData]      = useState<UIOComparisonData | null>(null);
  const [m1Forecast, setM1Forecast] = useState<M1UIOForecastResponse | null>(null);

  useEffect(() => {
    fetchUIOComparison().then(setData);
    fetchM1UIOForecast({ limit: 500 }).then(setM1Forecast).catch(() => {/* module not run */});
  }, []);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  // ── Module 1 UIO forecast pivot: months × models ─────────────────────────
  const m1Months = m1Forecast
    ? [...new Set(m1Forecast.rows.map(r => r.month))].sort()
    : [];
  const m1Models = m1Forecast?.models ?? [];
  const m1ChartData = m1Months.map(month => {
    const obj: Record<string, string | number> = { month };
    (m1Forecast?.rows ?? []).filter(r => r.month === month).forEach(r => {
      obj[r.model] = r.uio_forecast;
    });
    return obj;
  });

  const totalExternal = data.external.reduce((s, r) => s + r.uio, 0);
  const totalMcsi     = data.mcsi.reduce((s, r) => s + r.uio, 0);

  const historicalTop20 = data.external.slice(0, 20).map(r => ({
    name: r.model, uio: r.uio, sales: r.total_sales_units,
  }));

  const mcsiChartData = data.mcsi.map(r => ({ name: r.model, uio: r.uio }));

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      {/* Page header */}
      <div>
        <h2 className="text-xl font-bold text-slate-800">Units in Operation (UIO)</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Stage 3 · Fleet size from two sources: cohort-survival model (UIO.xlsx) and VIN-verified recent sales (MCSI.xlsx)
        </p>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Historical Fleet"   value={totalExternal.toLocaleString()}
          sub={`${data.external.length} model generations`} color="purple"/>
        <KpiCard label="MCSI Fleet (VIN)"   value={totalMcsi.toLocaleString()}
          sub={`${data.mcsi.length} current models`} color="blue"/>
        <KpiCard label="Top (Historical)"   value={data.external[0]?.model ?? "—"}
          sub={data.external[0] ? `${data.external[0].uio.toLocaleString()} UIO` : ""} color="teal"/>
        <KpiCard label="Top (MCSI)"         value={data.mcsi[0]?.model ?? "—"}
          sub={data.mcsi[0] ? `${data.mcsi[0].uio.toLocaleString()} UIO` : ""} color="green"/>
      </div>

      {/* ── Section 1: Historical Fleet ── */}
      <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <div className="flex items-center gap-2 mb-0.5">
              <h3 className="text-sm font-bold text-slate-800">UIO by Model — Historical Fleet</h3>
              <SourceBadge label="UIO.xlsx" color="text-purple-700 border-purple-200 bg-purple-50"/>
            </div>
            <p className="text-xs text-slate-400">
              Cohort-survival: bikes sold per year × retention rate · All generations back to 2013 · Top 20 shown
            </p>
          </div>
          <div className="flex items-center gap-4 text-xs text-slate-500">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-purple-600 inline-block"/>UIO estimate
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-purple-200 inline-block"/>Total sales
            </span>
          </div>
        </div>

        <ResponsiveContainer width="100%" height={500}>
          <BarChart data={historicalTop20} layout="vertical"
            margin={{ top: 0, right: 60, left: 10, bottom: 0 }} barGap={2} barCategoryGap="30%">
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
            <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
            <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={175}/>
            <Tooltip
              formatter={(v: unknown, n: unknown) => [
                Number(v).toLocaleString(), n === "uio" ? "UIO Estimate" : "Total Sales",
              ]}
              contentStyle={{ fontSize: 11 }}/>
            <Bar dataKey="sales" name="sales" fill="#EDE9FE" radius={[0, 3, 3, 0]}/>
            <Bar dataKey="uio"   name="uio"   fill="#7C3AED" radius={[0, 3, 3, 0]}/>
          </BarChart>
        </ResponsiveContainer>

        {/* Summary row */}
        <div className="flex items-center gap-6 pt-3 border-t border-slate-100 text-xs text-slate-500">
          <span>Total UIO: <strong className="text-purple-700">{totalExternal.toLocaleString()}</strong></span>
          <span>Model generations: <strong className="text-slate-700">{data.external.length}</strong></span>
          <span>Top model: <strong className="text-slate-700">{data.external[0]?.model} ({data.external[0]?.uio.toLocaleString()})</strong></span>
        </div>
      </div>

      {/* ── Section 2: Recent Sales (MCSI) ── */}
      <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
        <div className="flex items-start gap-2 mb-0.5 flex-wrap">
          <h3 className="text-sm font-bold text-slate-800">UIO by Model — Recent Sales</h3>
          <SourceBadge label="MCSI.xlsx" color="text-blue-700 border-blue-200 bg-blue-50"/>
        </div>
        <p className="text-xs text-slate-400 -mt-2">VIN-verified sold bikes · Current models only</p>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Chart */}
          <div className="lg:col-span-3">
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={mcsiChartData} layout="vertical"
                margin={{ top: 0, right: 50, left: 10, bottom: 0 }} barCategoryGap="30%">
                <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={160}/>
                <Tooltip
                  formatter={(v: unknown) => [Number(v).toLocaleString(), "UIO"]}
                  contentStyle={{ fontSize: 11 }}/>
                <Bar dataKey="uio" name="UIO" radius={[0, 3, 3, 0]}>
                  {mcsiChartData.map((_, i) => (
                    <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]}/>
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Table */}
          <div className="lg:col-span-2">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b-2 border-slate-200 text-xs text-slate-500 uppercase">
                  <th className="pb-2 text-left">Model</th>
                  <th className="pb-2 text-right">UIO</th>
                  <th className="pb-2 text-right">Share</th>
                </tr>
              </thead>
              <tbody>
                {data.mcsi.map((r, i) => (
                  <tr key={r.model} className="border-b border-slate-50 hover:bg-slate-50/60">
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }}/>
                        <span className="font-medium text-slate-700">{r.model}</span>
                      </div>
                    </td>
                    <td className="py-2 text-right font-bold text-slate-800">
                      {r.uio.toLocaleString()}
                    </td>
                    <td className="py-2 text-right">
                      <span className="inline-flex items-center gap-1">
                        <span className="text-slate-400">{r.uio_pct.toFixed(1)}%</span>
                        <span className="inline-block h-1.5 rounded-full bg-slate-200 w-10 overflow-hidden">
                          <span className="h-full block rounded-full"
                            style={{
                              width: `${r.uio_pct}%`,
                              background: MODEL_COLORS[i % MODEL_COLORS.length],
                            }}/>
                        </span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-slate-200">
                  <td className="pt-2 text-xs font-semibold text-slate-500 uppercase">Total</td>
                  <td className="pt-2 text-right font-bold text-blue-700">{totalMcsi.toLocaleString()}</td>
                  <td className="pt-2 text-right text-xs text-slate-400">100%</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </div>

      {/* ── Module 1: Per-model UIO forecast ── */}
      {m1Forecast && m1Forecast.total > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-slate-800">UIO Forecast by Model — Module 1</h3>
            <SourceBadge label="m1_uio_forecast.parquet" color="text-teal-700 border-teal-200 bg-teal-50"/>
          </div>
          <p className="text-xs text-slate-400 -mt-2">
            Module 1 vehicle intelligence forecast · {m1Forecast.total.toLocaleString()} rows · {m1Models.length} models · {m1Months.length} months
          </p>
          {m1ChartData.length > 0 && m1Models.length > 0 && (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={m1ChartData.slice(0, 24)} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                <XAxis dataKey="month" tick={{ fontSize: 9 }} interval={2}/>
                <YAxis tick={{ fontSize: 10 }} tickFormatter={fmt} width={44}/>
                <Tooltip formatter={(v: unknown, n: unknown) => [Number(v).toFixed(0), String(n)]}/>
                <Legend wrapperStyle={{ fontSize: 10 }}/>
                {m1Models.slice(0, 8).map((m, i) => (
                  <Line key={m} type="monotone" dataKey={m} stroke={MODEL_COLORS[i % MODEL_COLORS.length]}
                    strokeWidth={1.5} dot={false} name={m}/>
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-left text-[10px] text-slate-500 uppercase">
                  <th className="py-2 pr-3">Month</th>
                  {m1Models.slice(0, 6).map(m => (
                    <th key={m} className="py-2 pr-3 text-right">{m}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {m1ChartData.slice(0, 12).map(row => (
                  <tr key={row.month as string} className="border-b border-slate-50 hover:bg-slate-50/50">
                    <td className="py-1.5 pr-3 font-mono text-slate-600">{row.month as string}</td>
                    {m1Models.slice(0, 6).map(m => (
                      <td key={m} className="py-1.5 pr-3 text-right text-slate-700">
                        {row[m] != null ? Number(row[m]).toFixed(0) : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {m1Months.length > 12 && (
              <p className="text-xs text-slate-400 mt-2 text-center">Showing 12 of {m1Months.length} months</p>
            )}
          </div>
        </div>
      )}

      {/* ── Full historical table ── */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex items-center gap-2 mb-3">
          <h3 className="text-sm font-bold text-slate-700">All Models — Complete Historical List</h3>
          <SourceBadge label="UIO.xlsx" color="text-purple-700 border-purple-200 bg-purple-50"/>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b-2 border-slate-200 text-xs text-slate-500 uppercase">
                <th className="pb-2 w-8 text-left">#</th>
                <th className="pb-2 text-left">Model</th>
                <th className="pb-2 pr-4 text-right">Total Sales</th>
                <th className="pb-2 text-right">UIO</th>
              </tr>
            </thead>
            <tbody>
              {data.external.map((r, i) => (
                <tr key={r.model} className="border-b border-slate-50 hover:bg-purple-50/20">
                  <td className="py-1.5 pr-2 text-xs text-slate-400 font-mono">{i + 1}</td>
                  <td className="py-1.5 pr-4 text-xs text-slate-700">{r.model}</td>
                  <td className="py-1.5 pr-4 text-right text-xs text-slate-500">{r.total_sales_units.toLocaleString()}</td>
                  <td className="py-1.5 text-right text-xs font-bold text-purple-700">{r.uio.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
