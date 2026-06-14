import { useEffect, useState } from "react";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { fetchForecast, fetchTrend, type ForecastRow, type MonthlyPoint } from "../api/client";
import { KpiCard } from "../components/KpiCard";

const METHOD_COLORS = ["#4361EE","#2CC56F","#FFC107","#EF4444","#7C3AED","#06B6D4","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export function Forecast() {
  const [data, setData] = useState<{ total: number; rows: ForecastRow[]; method_counts: Record<string, number> } | null>(null);
  const [trend, setTrend] = useState<MonthlyPoint[]>([]);
  const [method, setMethod] = useState<string>("");
  const [search, setSearch] = useState("");

  useEffect(() => { fetchForecast({ limit: 500 }).then(setData); fetchTrend().then(setTrend); }, []);
  useEffect(() => { fetchForecast({ method: method || undefined, limit: 500 }).then(setData); }, [method]);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const methodData = Object.entries(data.method_counts).map(([k, v], i) => ({ name: k, value: v, fill: METHOD_COLORS[i] }));
  const trendData = trend.slice(-24).map(p => ({ month: p.year_month_str.slice(0, 7), qty: p.issue_qty }));

  const mlCount = (data.method_counts["LightGBM"] ?? 0) + (data.method_counts["NHITS"] ?? 0) + (data.method_counts["AutoETS"] ?? 0);
  const zeroCount = data.method_counts["Zero"] ?? 0;

  const filtered = data.rows.filter(r =>
    !search || r.material_9.toLowerCase().includes(search.toLowerCase()) || r.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Demand Forecast</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total SKUs"    value={fmt(data.total)}   color="blue"/>
        <KpiCard label="ML/DL Models"  value={fmt(mlCount)}      sub="LightGBM + NHITS + AutoETS" color="purple"/>
        <KpiCard label="Zero Demand"   value={fmt(zeroCount)}    sub="Non-movers" color="amber"/>
        <KpiCard label="Methods Used"  value={`${Object.keys(data.method_counts).length}`} color="teal"/>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Method donut */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Forecast Method Breakdown</h3>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie data={methodData} cx="50%" cy="50%" innerRadius={60} outerRadius={90} dataKey="value" nameKey="name">
                {methodData.map(d => <Cell key={d.name} fill={d.fill}/>)}
              </Pie>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-2 mt-1">
            {methodData.map(d => (
              <span key={d.name} className="flex items-center gap-1 text-xs text-slate-600">
                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: d.fill }}/>
                {d.name} ({d.value.toLocaleString()})
              </span>
            ))}
          </div>
        </div>

        {/* Trend */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Historical Monthly Demand</h3>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={trendData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="blueGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4361EE" stopOpacity={0.15}/>
                  <stop offset="95%" stopColor="#4361EE" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="month" tick={{ fontSize: 10 }} interval={3}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Area type="monotone" dataKey="qty" stroke="#4361EE" fill="url(#blueGrad)" strokeWidth={2}/>
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex gap-3 mb-4 flex-wrap">
          <input
            className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[180px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
            placeholder="Search SKU or description…"
            value={search} onChange={e => setSearch(e.target.value)}
          />
          <select
            className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none"
            value={method} onChange={e => setMethod(e.target.value)}
          >
            <option value="">All methods</option>
            {Object.keys(data.method_counts).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                <th className="py-2 pr-4">SKU</th>
                <th className="py-2 pr-4">Description</th>
                <th className="py-2 pr-4">Method</th>
                <th className="py-2 pr-4 text-right">Avg Demand</th>
                <th className="py-2 pr-4 text-right">CV</th>
                <th className="py-2 pr-4 text-right">LT Forecast</th>
                <th className="py-2 pr-4 text-right">M+1</th>
                <th className="py-2 pr-4 text-right">M+2</th>
                <th className="py-2 text-right">M+3</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map(r => (
                <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                  <td className="py-2 pr-4 font-mono text-xs text-slate-700">{r.material_9}</td>
                  <td className="py-2 pr-4 text-slate-600 max-w-[200px] truncate">{r.description}</td>
                  <td className="py-2 pr-4">
                    <span className="text-xs bg-brand-blue/10 text-brand-blue px-2 py-0.5 rounded-full">{r.method}</span>
                  </td>
                  <td className="py-2 pr-4 text-right text-slate-700">{r.avg_monthly_demand.toFixed(1)}</td>
                  <td className="py-2 pr-4 text-right text-xs" style={{ color: r.cv_hist > 1 ? "#EF4444" : r.cv_hist > 0.5 ? "#F97316" : "#2CC56F" }}>{r.cv_hist.toFixed(2)}</td>
                  <td className="py-2 pr-4 text-right text-slate-600 font-medium">{r.forecast_lt.toFixed(1)}</td>
                  <td className="py-2 pr-4 text-right text-slate-700">{r.forecast_m1.toFixed(1)}</td>
                  <td className="py-2 pr-4 text-right text-slate-700">{r.forecast_m2.toFixed(1)}</td>
                  <td className="py-2 text-right text-slate-700">{r.forecast_m3.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {filtered.length}</p>}
        </div>
      </div>
    </div>
  );
}
