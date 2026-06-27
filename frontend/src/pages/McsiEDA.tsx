import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  ComposedChart, Line, Legend, PieChart, Pie,
} from "recharts";
import { fetchMcsiEda, type McsiEdaData } from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { Bike, MapPin, TrendingUp, Users, RotateCcw, DollarSign } from "lucide-react";

const MODEL_COLORS  = ["#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED","#06B6D4","#F97316","#10B981","#EC4899","#94A3B8"];
const PROV_COLORS   = ["#4361EE","#7C3AED","#2CC56F","#F97316","#EF4444","#06B6D4","#FFC107","#10B981","#EC4899","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "trend" | "model" | "province" | "year";

export function McsiEDA() {
  const [data, setData] = useState<McsiEdaData | null>(null);
  const [tab,  setTab]  = useState<Tab>("trend");

  useEffect(() => { fetchMcsiEda().then(setData); }, []);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const { kpis, monthly_trend, by_year, by_model, by_province } = data;

  const TABS: { key: Tab; label: string }[] = [
    { key: "trend",    label: "Monthly Trend" },
    { key: "model",    label: "By Model"      },
    { key: "province", label: "By Province"   },
    { key: "year",     label: "By Year"       },
  ];

  // Pie data for model share
  const modelPie = by_model.slice(0, 8).map((r, i) => ({
    name: r.model, value: r.units_sold, share_pct: r.share_pct, fill: MODEL_COLORS[i % MODEL_COLORS.length],
  }));

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold text-slate-800">MCSI Motorcycle Sales EDA</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Stage 1 · VIN-level sold/returned classification · {kpis.date_from} → {kpis.date_to}
        </p>
      </div>

      {/* KPI row 1 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total VINs Processed" value={fmt(kpis.total_vins)}
          sub={`${kpis.date_from} → ${kpis.date_to}`} color="blue" icon={<Bike size={18}/>}/>
        <KpiCard label="Bikes Sold" value={fmt(kpis.sold)}
          sub={`avg ${kpis.avg_monthly_units} / month`} color="green" icon={<TrendingUp size={18}/>}/>
        <KpiCard label="Returns" value={fmt(kpis.returned)}
          sub={`${kpis.return_rate_pct.toFixed(2)}% return rate`}
          color={kpis.return_rate_pct > 5 ? "red" : "amber"} icon={<RotateCcw size={18}/>}/>
        <KpiCard label="Total Revenue" value={`LKR ${fmt(kpis.total_revenue_lkr)}`}
          sub={`LKR ${fmt(kpis.avg_revenue_per_unit)} / unit`} color="purple" icon={<DollarSign size={18}/>}/>
      </div>

      {/* KPI row 2 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Active Provinces" value={kpis.active_provinces.toString()}
          color="teal" icon={<MapPin size={18}/>}/>
        <KpiCard label="Active Dealers"  value={fmt(kpis.active_dealers)}
          color="blue" icon={<Users size={18}/>}/>
        <KpiCard label="Models Sold"     value={kpis.models_sold.toString()}  color="purple"/>
        <KpiCard label="Months of Data"  value={kpis.months_of_data.toString()} color="green"/>
      </div>

      {/* Tab content */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex gap-1 mb-5 border-b border-slate-100 pb-2 flex-wrap">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                tab === t.key ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Monthly Trend ── */}
        {tab === "trend" && (
          <div className="space-y-5">
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-1">Monthly Units Sold &amp; Revenue</h3>
              <p className="text-xs text-slate-400 mb-3">Bars = units sold (left axis) · Line = revenue LKR (right axis)</p>
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={monthly_trend} margin={{ top: 5, right: 50, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                  <XAxis dataKey="period" tick={{ fontSize: 9 }} interval={2}/>
                  <YAxis yAxisId="left"  tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <Tooltip formatter={(v: unknown, name: unknown) =>
                    [`${name === "Revenue LKR" ? "LKR " : ""}${fmt(Number(v))}`, String(name)]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Bar  yAxisId="left"  dataKey="sold"        name="Units Sold"   fill="#4361EE" radius={[3,3,0,0]} opacity={0.85}/>
                  <Line yAxisId="right" dataKey="revenue_lkr" name="Revenue LKR"  stroke="#F97316" strokeWidth={2} dot={false}/>
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Returns callout */}
            <div className={`rounded-lg px-4 py-3 border ${kpis.return_rate_pct > 5 ? "bg-red-50 border-red-100" : "bg-amber-50 border-amber-100"}`}>
              <p className="text-sm font-semibold text-slate-700">
                Return Rate: <span className={kpis.return_rate_pct > 5 ? "text-red-600" : "text-amber-600"}>
                  {kpis.return_rate_pct.toFixed(2)}%
                </span>
                <span className="ml-3 font-normal text-slate-500 text-xs">
                  ({kpis.returned.toLocaleString()} returned out of {kpis.total_vins.toLocaleString()} total VINs)
                </span>
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Business rule: VIN with SlsVolQty sum = 0 is classified as returned; sum = 1 as sold.
              </p>
            </div>
          </div>
        )}

        {/* ── By Model ── */}
        {tab === "model" && (
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* Horizontal bar */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Units Sold by Model</h3>
                <ResponsiveContainer width="100%" height={Math.max(200, by_model.length * 30)}>
                  <BarChart data={by_model} layout="vertical" margin={{ top: 0, right: 60, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="model" tick={{ fontSize: 9 }} width={140}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="units_sold" radius={[0, 3, 3, 0]} label={{ position: "right", fontSize: 10 }}>
                      {by_model.map((_, i) => <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* Pie chart */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Market Share</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={modelPie} cx="50%" cy="50%" outerRadius={85} dataKey="value" nameKey="name"
                      label={(props: any) => `${props.name}: ${props.share_pct}%`}
                      labelLine={false}>
                      {modelPie.map((d, i) => <Cell key={i} fill={d.fill}/>)}
                    </Pie>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Model table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-3">Model</th>
                    <th className="py-2 pr-3 text-right">Units Sold</th>
                    <th className="py-2 pr-3 text-right">Share %</th>
                    <th className="py-2 pr-3 text-right">Revenue (LKR)</th>
                    <th className="py-2 text-right">Avg Rev / Unit</th>
                  </tr>
                </thead>
                <tbody>
                  {by_model.map((r, i) => (
                    <tr key={r.model} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-3 flex items-center gap-2">
                        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }}/>
                        <span className="font-medium text-slate-800">{r.model}</span>
                      </td>
                      <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right text-slate-500">{r.share_pct.toFixed(1)}%</td>
                      <td className="py-2 pr-3 text-right">{fmt(r.revenue_lkr)}</td>
                      <td className="py-2 text-right text-slate-500">{fmt(r.avg_revenue_per_unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── By Province ── */}
        {tab === "province" && (
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Units Sold by Province</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={by_province} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="province" tick={{ fontSize: 10 }}/>
                    <YAxis tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="units_sold" radius={[4, 4, 0, 0]}>
                      {by_province.map((_, i) => <Cell key={i} fill={PROV_COLORS[i % PROV_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Revenue by Province (LKR)</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={by_province} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="province" tick={{ fontSize: 10 }}/>
                    <YAxis tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => `LKR ${fmt(Number(v))}`}/>
                    <Bar dataKey="revenue_lkr" name="Revenue" radius={[4, 4, 0, 0]}>
                      {by_province.map((_, i) => <Cell key={i} fill={PROV_COLORS[i % PROV_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-3">Province</th>
                    <th className="py-2 pr-3 text-right">Units Sold</th>
                    <th className="py-2 pr-3 text-right">Share %</th>
                    <th className="py-2 pr-3 text-right">Revenue (LKR)</th>
                    <th className="py-2 text-right">Dealers</th>
                  </tr>
                </thead>
                <tbody>
                  {by_province.map((r, i) => (
                    <tr key={r.province} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-3 flex items-center gap-2">
                        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                          style={{ background: PROV_COLORS[i % PROV_COLORS.length] }}/>
                        <span className="font-medium text-slate-800">{r.province}</span>
                      </td>
                      <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right text-slate-500">{r.share_pct.toFixed(1)}%</td>
                      <td className="py-2 pr-3 text-right">{fmt(r.revenue_lkr)}</td>
                      <td className="py-2 text-right text-slate-500">{r.dealer_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── By Year ── */}
        {tab === "year" && (
          <div className="space-y-5">
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-3">Annual Sales Summary</h3>
              <ResponsiveContainer width="100%" height={220}>
                <ComposedChart data={by_year} margin={{ top: 5, right: 50, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                  <XAxis dataKey="year" tick={{ fontSize: 11 }}/>
                  <YAxis yAxisId="left"  tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <Tooltip formatter={(v: unknown, name: unknown) =>
                    [`${name === "Revenue LKR" ? "LKR " : ""}${fmt(Number(v))}`, String(name)]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }}/>
                  <Bar  yAxisId="left"  dataKey="units_sold"   name="Units Sold"  fill="#4361EE" radius={[3,3,0,0]}/>
                  <Line yAxisId="right" dataKey="revenue_lkr"  name="Revenue LKR" stroke="#F97316" strokeWidth={2} dot={{ r: 4 }}/>
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-3">Year</th>
                    <th className="py-2 pr-3 text-right">Units Sold</th>
                    <th className="py-2 pr-3 text-right">Avg / Month</th>
                    <th className="py-2 text-right">Revenue (LKR)</th>
                  </tr>
                </thead>
                <tbody>
                  {by_year.map(r => (
                    <tr key={r.year} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-3 font-semibold text-slate-800">{r.year}</td>
                      <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right text-slate-500">{r.avg_monthly.toFixed(1)}</td>
                      <td className="py-2 text-right">{fmt(r.revenue_lkr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
