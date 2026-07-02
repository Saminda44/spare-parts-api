import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  ComposedChart, Line, Legend, PieChart, Pie,
} from "recharts";
import {
  fetchMcsiEda, fetchBikeDealers, fetchCrosstab, fetchDealerModelMatrix, fetchGeoModel, fetchGeoModelColor,
  type McsiEdaData, type DealersData, type CrosstabData, type DealerModelMatrix,
  type GeoModelData, type GeoMatrixLevel,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { MapPin, TrendingUp, Users, RotateCcw, DollarSign } from "lucide-react";

const MODEL_COLORS  = ["#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED","#06B6D4","#F97316","#10B981","#EC4899","#94A3B8"];
const PROV_COLORS   = ["#4361EE","#7C3AED","#2CC56F","#F97316","#EF4444","#06B6D4","#FFC107","#10B981","#EC4899","#94A3B8"];

// Map the actual SAP color name to a visual hex for charts.
// Priority order matters: "REDDISH YELLOW" must be checked before "RED".
function getColorHex(name: string): string {
  const u = name.toUpperCase();
  if (u.includes("REDDISH YELLOW") || u.includes("YELLOW COCKTAIL")) return "#FFC107";
  if (u.includes("YELLOW"))   return "#FFC107";
  if (u.includes("ORANGE"))   return "#F97316";
  if (u.includes("GREEN"))    return "#2CC56F";
  if (u.includes("CYAN"))     return "#06B6D4";
  if (u.includes("BLUE") || u.includes("PURPLISH")) return "#4361EE";
  if (u.includes("GRAY") || u.includes("GREY"))     return "#94A3B8";
  if (u.includes("RED"))      return "#EF4444";
  if (u.includes("BLACK"))    return "#1E293B";
  return "#CBD5E1";
}

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "trend" | "model" | "color" | "year" | "geo" | "dealer";

export function McsiEDA() {
  const [data,     setData]     = useState<McsiEdaData | null>(null);
  const [dealers,  setDealers]  = useState<DealersData | null>(null);
  const [crosstab, setCrosstab] = useState<CrosstabData | null>(null);
  const [tab,      setTab]      = useState<Tab>("trend");
  const [dlrSearch, setDlrSearch] = useState("");
  const [dlrYear,   setDlrYear]   = useState<number | undefined>(undefined);
  const [geoSub,      setGeoSub]      = useState<"rm" | "ase" | "province" | "district">("rm");
  const [geoView,     setGeoView]     = useState<"overview" | "model" | "model_color">("overview");
  const [dealerMatrix, setDealerMatrix] = useState<DealerModelMatrix | null>(null);
  const [geoModel,      setGeoModel]      = useState<GeoModelData | null>(null);
  const [geoModelColor, setGeoModelColor] = useState<GeoModelData | null>(null);

  useEffect(() => { fetchMcsiEda().then(setData); }, []);
  useEffect(() => { fetchDealerModelMatrix().then(setDealerMatrix); }, []);
  useEffect(() => { fetchGeoModel().then(setGeoModel); }, []);
  useEffect(() => { fetchGeoModelColor().then(setGeoModelColor); }, []);
  useEffect(() => {
    fetchBikeDealers(500, dlrYear).then(setDealers);
    fetchCrosstab(dlrYear).then(setCrosstab);
  }, [dlrYear]);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const { kpis, monthly_trend, by_year, by_model, by_province, by_color, by_rm, by_ase, by_district } = data;

  const TABS: { key: Tab; label: string }[] = [
    { key: "trend",  label: "Monthly Trend"        },
    { key: "model",  label: "By Model"              },
    { key: "color",  label: "By Color"              },
    { key: "year",   label: "By Year"               },
    { key: "geo",    label: "RM / ASE / Geography" },
    { key: "dealer",   label: "Dealer Performance"      },
  ];

  // ── Dealer Performance tab data prep ───────────────────────────────────────
  const filteredDealers = dealers?.rows.filter(r =>
    !dlrSearch || [r.province, r.rm, r.ase, r.dealer].some(v =>
      v.toLowerCase().includes(dlrSearch.toLowerCase())
    )
  ) ?? [];

  const provinceBarData = (() => {
    if (!dealers) return [];
    const map: Record<string, { units: number; revenue: number }> = {};
    for (const r of dealers.rows) {
      if (!map[r.province]) map[r.province] = { units: 0, revenue: 0 };
      map[r.province].units   += r.units_sold;
      map[r.province].revenue += r.revenue_lkr;
    }
    return Object.entries(map)
      .map(([province, v]) => ({ province, ...v }))
      .sort((a, b) => b.units - a.units);
  })();

  // ── Color tab data prep ─────────────────────────────────────────────────────
  // Collect all canonical color names present in the data
  const colorKeys = [...new Set(by_color.map(r => r.color))].sort();
  // Pivot: one row per model with a key per color (values are number | string)
  type ColorBarRow = Record<string, number | string>;
  const colorBarData: ColorBarRow[] = Object.values(
    by_color.reduce<Record<string, ColorBarRow>>((acc, r) => {
      if (!acc[r.model]) acc[r.model] = { model: r.model };
      acc[r.model][r.color] = ((acc[r.model][r.color] as number) ?? 0) + r.units_sold;
      return acc;
    }, {})
  ).sort((a, b) => {
    const totalA = colorKeys.reduce((s, c) => s + ((a[c] as number) ?? 0), 0);
    const totalB = colorKeys.reduce((s, c) => s + ((b[c] as number) ?? 0), 0);
    return totalB - totalA;
  });

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
          sub={`${kpis.date_from} → ${kpis.date_to}`} color="blue"/>
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

        {/* ── By Color ── */}
        {tab === "color" && (
          <div className="space-y-5">
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-1">Model Sales by Color — Stacked Units</h3>
              <p className="text-xs text-slate-400 mb-3">
                Sold bikes only · Colors extracted from Material description · SKUs with no identifiable color are excluded
              </p>
              <ResponsiveContainer width="100%" height={Math.max(260, colorBarData.length * 42)}>
                <BarChart data={colorBarData} layout="vertical" margin={{ top: 5, right: 60, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={v => Number(v).toLocaleString()}/>
                  <YAxis type="category" dataKey="model" tick={{ fontSize: 9 }} width={165}/>
                  <Tooltip formatter={(v: unknown, name: unknown) => [Number(v).toLocaleString() + " units", String(name)]}/>
                  <Legend wrapperStyle={{ fontSize: 11 }} iconType="square"/>
                  {colorKeys.map(color => (
                    <Bar key={color} dataKey={color} stackId="a" name={color}
                      fill={getColorHex(color)} radius={[0, 0, 0, 0]}/>
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Color legend pills */}
            <div className="flex flex-wrap gap-2">
              {colorKeys.map(color => (
                <span key={color} className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium text-white"
                  style={{ background: getColorHex(color) }}>
                  {color}
                </span>
              ))}
            </div>

            {/* Breakdown table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-3">Model</th>
                    <th className="py-2 pr-3">Color</th>
                    <th className="py-2 pr-3 text-right">Units Sold</th>
                    <th className="py-2 text-right">Share within Model</th>
                  </tr>
                </thead>
                <tbody>
                  {by_color.map((r, i) => {
                    const modelTotal = by_color.filter(x => x.model === r.model).reduce((s, x) => s + x.units_sold, 0);
                    const sharePct = modelTotal ? (r.units_sold / modelTotal * 100).toFixed(1) : "0.0";
                    return (
                      <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-3 font-medium text-slate-800">{r.model}</td>
                        <td className="py-2 pr-3">
                          <span className="inline-flex items-center gap-1.5 text-xs px-2.5 py-0.5 rounded-full font-medium text-white"
                            style={{ background: getColorHex(r.color) }}>
                            {r.color}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                        <td className="py-2 text-right text-slate-500">{sharePct}%</td>
                      </tr>
                    );
                  })}
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

        {/* ── RM / ASE / Geography ── */}
        {tab === "geo" && (
          <div className="space-y-4">
            {/* Sub-tab switcher */}
            <div className="flex gap-1 border-b border-slate-100 pb-2">
                {(["rm", "ase", "province", "district"] as const).map(s => (
                <button key={s} onClick={() => { setGeoSub(s); setGeoView("overview"); }}
                  className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${
                    geoSub === s ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"
                  }`}>
                  {s === "rm" ? "Regional Manager"
                   : s === "ase" ? "Area Sales Executive"
                   : s === "province" ? "By Province"
                   : "By District"}
                </button>
              ))}
              {/* view toggle */}
              <div className="ml-auto flex gap-1">
                {(["overview", "model", "model_color"] as const).map(v => (
                  <button key={v} onClick={() => setGeoView(v)}
                    className={`px-2.5 py-1 text-xs rounded-md font-medium transition-colors border ${
                      geoView === v
                        ? "bg-slate-700 text-white border-slate-700"
                        : "text-slate-400 border-slate-200 hover:bg-slate-50"
                    }`}>
                    {v === "overview" ? "Overview" : v === "model" ? "× Model" : "× Model × Color"}
                  </button>
                ))}
              </div>
            </div>

            {/* ── RM panel ── */}
            {geoView === "overview" && geoSub === "rm" && (
              <div className="space-y-4">
                <ResponsiveContainer width="100%" height={Math.max(180, by_rm.length * 36)}>
                  <BarChart data={[...by_rm].reverse()} layout="vertical" margin={{ top: 0, right: 80, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={v => Number(v).toLocaleString()}/>
                    <YAxis type="category" dataKey="rm" tick={{ fontSize: 10 }} width={90}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="units_sold" name="Units Sold" radius={[0, 4, 4, 0]}
                      label={{ position: "right", fontSize: 10 }}>
                      {[...by_rm].reverse().map((_, i) => <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">Regional Manager</th>
                        <th className="py-2 pr-3 text-right">Units Sold</th>
                        <th className="py-2 pr-3 text-right">Share %</th>
                        <th className="py-2 pr-3 text-right">Revenue (LKR)</th>
                        <th className="py-2 pr-3 text-right">Avg Rev / Unit</th>
                        <th className="py-2 pr-3 text-right">Dealers</th>
                        <th className="py-2 text-right">ASEs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {by_rm.map((r, i) => (
                        <tr key={r.rm} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3 flex items-center gap-2">
                            <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                              style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }}/>
                            <span className="font-medium text-slate-800">{r.rm}</span>
                          </td>
                          <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                          <td className="py-2 pr-3 text-right text-slate-500">{r.share_pct.toFixed(1)}%</td>
                          <td className="py-2 pr-3 text-right">{fmt(r.revenue_lkr)}</td>
                          <td className="py-2 pr-3 text-right text-slate-400">{fmt(r.avg_revenue_per_unit)}</td>
                          <td className="py-2 pr-3 text-right text-slate-500">{r.dealer_count}</td>
                          <td className="py-2 text-right text-slate-500">{r.ase_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── ASE panel ── */}
            {geoView === "overview" && geoSub === "ase" && (
              <div className="space-y-4">
                <ResponsiveContainer width="100%" height={Math.max(220, by_ase.length * 34)}>
                  <BarChart data={[...by_ase].reverse()} layout="vertical" margin={{ top: 0, right: 80, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={v => Number(v).toLocaleString()}/>
                    <YAxis type="category" dataKey="ase" tick={{ fontSize: 10 }} width={100}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="units_sold" name="Units Sold" radius={[0, 4, 4, 0]}
                      label={{ position: "right", fontSize: 10 }}>
                      {[...by_ase].reverse().map((r, i) => (
                        <Cell key={i} fill={MODEL_COLORS[by_rm.findIndex(rm => rm.rm === r.rm) % MODEL_COLORS.length] ?? "#94A3B8"}/>
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">ASE</th>
                        <th className="py-2 pr-3">Regional Manager</th>
                        <th className="py-2 pr-3 text-right">Units Sold</th>
                        <th className="py-2 pr-3 text-right">Share %</th>
                        <th className="py-2 pr-3 text-right">Revenue (LKR)</th>
                        <th className="py-2 text-right">Dealers</th>
                      </tr>
                    </thead>
                    <tbody>
                      {by_ase.map((r, i) => (
                        <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3 font-medium text-slate-800">{r.ase}</td>
                          <td className="py-2 pr-3">
                            <span className="text-xs px-2 py-0.5 rounded-full font-medium text-white"
                              style={{ background: MODEL_COLORS[by_rm.findIndex(rm => rm.rm === r.rm) % MODEL_COLORS.length] ?? "#94A3B8" }}>
                              {r.rm}
                            </span>
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

            {/* ── Province panel ── */}
            {geoView === "overview" && geoSub === "province" && (
              <div className="space-y-4">
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

            {/* ── District panel ── */}
            {geoView === "overview" && geoSub === "district" && (
              <div className="space-y-4">
                <ResponsiveContainer width="100%" height={Math.max(260, by_district.length * 28)}>
                  <BarChart data={[...by_district].reverse()} layout="vertical" margin={{ top: 0, right: 70, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={v => Number(v).toLocaleString()}/>
                    <YAxis type="category" dataKey="district" tick={{ fontSize: 9 }} width={100}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="units_sold" name="Units Sold" radius={[0, 4, 4, 0]}
                      label={{ position: "right", fontSize: 9 }}>
                      {[...by_district].reverse().map((r, i) => (
                        <Cell key={i} fill={PROV_COLORS[by_province.findIndex(p => p.province === r.province) % PROV_COLORS.length] ?? "#94A3B8"}/>
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">Province</th>
                        <th className="py-2 pr-3">District</th>
                        <th className="py-2 pr-3 text-right">Units Sold</th>
                        <th className="py-2 pr-3 text-right">Share %</th>
                        <th className="py-2 text-right">Revenue (LKR)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {by_district.map((r, i) => (
                        <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3">
                            <span className="inline-flex items-center gap-1.5 text-xs">
                              <span className="w-2 h-2 rounded-full inline-block shrink-0"
                                style={{ background: PROV_COLORS[by_province.findIndex(p => p.province === r.province) % PROV_COLORS.length] ?? "#94A3B8" }}/>
                              {r.province}
                            </span>
                          </td>
                          <td className="py-2 pr-3 font-medium text-slate-800">{r.district}</td>
                          <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                          <td className="py-2 pr-3 text-right text-slate-500">{r.share_pct.toFixed(1)}%</td>
                          <td className="py-2 text-right">{fmt(r.revenue_lkr)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* ── × Model panel (shared across all sub-tabs) ── */}
            {geoView === "model" && (() => {
              const level: GeoMatrixLevel | undefined = geoModel
                ? geoModel[geoSub as keyof GeoModelData]
                : undefined;
              if (!level || level.rows.length === 0)
                return <div className="py-12 text-center text-slate-400">Loading…</div>;
              const { models: gm, rows: gr } = level;
              const colMax: Record<string, number> = {};
              for (const m of gm) colMax[m] = Math.max(1, ...gr.map(r => r.totals[m] ?? 0));
              const entityLabel = geoSub === "rm" ? "Regional Manager"
                : geoSub === "ase" ? "ASE"
                : geoSub === "province" ? "Province"
                : "District";
              return (
                <div className="space-y-2">
                  <p className="text-xs text-slate-400">
                    {gr.length} {entityLabel}s × {gm.length} models · sorted by total units (desc)
                  </p>
                  <div className="overflow-x-auto">
                    <table className="text-xs w-full border-collapse">
                      <thead>
                        <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase text-left">
                          <th className="py-2 px-2 font-semibold sticky left-0 bg-slate-50 z-10 min-w-[140px]">{entityLabel}</th>
                          <th className="py-2 px-2 font-semibold text-right min-w-[52px]">Total</th>
                          {gm.map(m => (
                            <th key={m} className="py-2 px-2 text-right font-semibold min-w-[70px]">{m}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {gr.map((r, ri) => (
                          <tr key={ri} className="border-b border-slate-100 hover:bg-slate-50/60">
                            <td className="py-1.5 px-2 font-medium text-slate-800 sticky left-0 bg-white z-10">{r.entity}</td>
                            <td className="py-1.5 px-2 text-right font-bold text-slate-800">{r.total.toLocaleString()}</td>
                            {gm.map(m => {
                              const v = r.totals[m] ?? 0;
                              const opacity = v === 0 ? 0 : 0.12 + 0.78 * (v / colMax[m]);
                              return (
                                <td key={m} className="py-1.5 px-2 text-right"
                                  style={{
                                    background: v > 0 ? `rgba(67,97,238,${opacity.toFixed(2)})` : "transparent",
                                    color: opacity > 0.55 ? "#fff" : v > 0 ? "#1E3A8A" : "#CBD5E1",
                                    fontWeight: v > 0 ? 600 : 400,
                                  }}>
                                  {v > 0 ? v.toLocaleString() : "—"}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}

            {/* ── × Color panel (entity-specific per sub-tab) ── */}
            {geoView === "model_color" && (() => {
              const level: GeoMatrixLevel | undefined = geoModelColor
                ? geoModelColor[geoSub as keyof GeoModelData]
                : undefined;
              if (!level || level.rows.length === 0)
                return <div className="py-12 text-center text-slate-400">Loading…</div>;
              const { models: combos, rows: cr } = level;
              const colMax: Record<string, number> = {};
              for (const c of combos) colMax[c] = Math.max(1, ...cr.map(r => r.totals[c] ?? 0));
              const entityLabel = geoSub === "rm" ? "Regional Manager"
                : geoSub === "ase" ? "ASE"
                : geoSub === "province" ? "Province"
                : "District";
              return (
                <div className="space-y-3">
                  <p className="text-xs text-slate-400">
                    {entityLabel} × Model × Color · {cr.length} entities · heat-map intensity = column max
                  </p>
                  <div className="overflow-x-auto">
                    <table className="text-xs w-full border-collapse">
                      <thead>
                        <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 text-left">
                          <th className="py-2 px-2 font-semibold sticky left-0 bg-slate-50 z-10 min-w-[140px] uppercase">{entityLabel}</th>
                          <th className="py-2 px-2 font-semibold text-right min-w-[52px] uppercase">Total</th>
                          {combos.map(c => {
                            const sepIdx = c.indexOf(" – ");
                            const modelPart = sepIdx >= 0 ? c.slice(0, sepIdx) : c;
                            const colorPart = sepIdx >= 0 ? c.slice(sepIdx + 3) : c;
                            return (
                              <th key={c} className="py-2 px-2 text-right font-semibold min-w-[110px]">
                                <span className="inline-flex flex-col items-end gap-0.5">
                                  <span className="text-slate-600 normal-case font-semibold text-[10px] leading-tight">{modelPart}</span>
                                  <span className="inline-flex items-center gap-1">
                                    <span className="w-2 h-2 rounded-full inline-block shrink-0"
                                      style={{ background: getColorHex(colorPart) }}/>
                                    <span className="text-[9px] uppercase text-slate-500">{colorPart}</span>
                                  </span>
                                </span>
                              </th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody>
                        {cr.map((r, ri) => (
                          <tr key={ri} className="border-b border-slate-100 hover:bg-slate-50/60">
                            <td className="py-1.5 px-2 font-medium text-slate-800 sticky left-0 bg-white z-10">{r.entity}</td>
                            <td className="py-1.5 px-2 text-right font-bold text-slate-800">{r.total.toLocaleString()}</td>
                            {combos.map(c => {
                              const sepIdx = c.indexOf(" – ");
                              const colorPart = sepIdx >= 0 ? c.slice(sepIdx + 3) : c;
                              const hex = getColorHex(colorPart);
                              const v = r.totals[c] ?? 0;
                              const opacity = v === 0 ? 0 : 0.12 + 0.78 * (v / colMax[c]);
                              const bg = v > 0
                                ? `${hex}${Math.round(opacity * 255).toString(16).padStart(2, "0")}`
                                : "transparent";
                              return (
                                <td key={c} className="py-1.5 px-2 text-right"
                                  style={{
                                    background: bg,
                                    color: opacity > 0.55 ? "#fff" : v > 0 ? "#1E293B" : "#CBD5E1",
                                    fontWeight: v > 0 ? 600 : 400,
                                  }}>
                                  {v > 0 ? v.toLocaleString() : "—"}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}
          </div>
        )}

        {/* ── Dealer Performance ── */}
        {tab === "dealer" && (
          <div className="space-y-5">
            {/* Year filter + KPIs */}
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <p className="text-xs text-slate-500">Province → RM → ASE → Dealer hierarchy · Units sold &amp; revenue</p>
              {dealers && dealers.available_years.length > 0 && (
                <select
                  className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                  value={dlrYear ?? ""}
                  onChange={e => setDlrYear(e.target.value ? Number(e.target.value) : undefined)}
                >
                  <option value="">All years</option>
                  {dealers.available_years.map(y => <option key={y} value={y}>{y}</option>)}
                </select>
              )}
            </div>

            {dealers ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                  <KpiCard label="Total Dealers" value={dealers.total_dealers.toLocaleString()} color="blue" sub={dlrYear ? `Year ${dlrYear}` : "All years"}/>
                  <KpiCard label="Total Units"   value={fmt(dealers.total_units)}               color="green"/>
                  <KpiCard label="Total Revenue" value={`LKR ${fmt(dealers.total_revenue_lkr)}`} color="purple"/>
                </div>

                {/* Province bar */}
                <div className="bg-white rounded-xl shadow-sm p-5">
                  <h3 className="text-sm font-semibold text-slate-700 mb-3">Units Sold by Province</h3>
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={provinceBarData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                      <XAxis dataKey="province" tick={{ fontSize: 11 }}/>
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                      <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                      <Bar dataKey="units" name="Units Sold" radius={[4, 4, 0, 0]}>
                        {provinceBarData.map((d, i) => <Cell key={d.province} fill={PROV_COLORS[i % PROV_COLORS.length]}/>)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Dealer table */}
                <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
                  <div className="flex items-center gap-3">
                    <input
                      className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 max-w-xs focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                      placeholder="Search dealer, RM, ASE, province…"
                      value={dlrSearch}
                      onChange={e => setDlrSearch(e.target.value)}
                    />
                    <span className="text-xs text-slate-400">{filteredDealers.length} dealers</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                          <th className="py-2 pr-3">Province</th>
                          <th className="py-2 pr-3">RM</th>
                          <th className="py-2 pr-3">ASE</th>
                          <th className="py-2 pr-3">Dealer</th>
                          <th className="py-2 pr-3">Code</th>
                          <th className="py-2 pr-3 text-right">Units</th>
                          <th className="py-2 text-right">Revenue (LKR)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredDealers.slice(0, 200).map((r, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                            <td className="py-2 pr-3 text-xs font-medium text-slate-700">{r.province}</td>
                            <td className="py-2 pr-3 text-xs text-slate-500">{r.rm}</td>
                            <td className="py-2 pr-3 text-xs text-slate-500">{r.ase}</td>
                            <td className="py-2 pr-3 text-xs text-slate-800 font-medium max-w-[180px] truncate" title={r.dealer}>{r.dealer}</td>
                            <td className="py-2 pr-3 font-mono text-xs text-slate-400">{r.dealer_code}</td>
                            <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.units_sold.toLocaleString()}</td>
                            <td className="py-2 text-right text-slate-700">{fmt(r.revenue_lkr)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {filteredDealers.length > 200 && (
                      <p className="text-xs text-slate-400 mt-2 text-center">Showing 200 of {filteredDealers.length}</p>
                    )}
                  </div>
                </div>

                {/* Province × Model crosstab */}
                {crosstab && crosstab.models.length > 0 && (
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-3">Province × Model Sales Matrix</h3>
                    <div className="overflow-x-auto">
                      <table className="text-xs w-full">
                        <thead>
                          <tr className="border-b border-slate-100 text-slate-500 uppercase">
                            <th className="py-2 pr-3 text-left">Province</th>
                            {crosstab.models.map(m => <th key={m} className="py-2 px-2 text-right">{m}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {crosstab.rows.map(r => (
                            <tr key={r.province} className="border-b border-slate-50 hover:bg-slate-50/50">
                              <td className="py-2 pr-3 font-medium text-slate-700">{r.province}</td>
                              {crosstab.models.map(m => (
                                <td key={m} className="py-2 px-2 text-right"
                                  style={{ color: (r.totals[m] ?? 0) === 0 ? "#CBD5E1" : "#1E293B" }}>
                                  {(r.totals[m] ?? 0).toLocaleString()}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Dealer × Model matrix */}
                {dealerMatrix && dealerMatrix.models.length > 0 && (() => {
                  const { models: dmModels, rows: dmRows } = dealerMatrix;
                  const colMax: Record<string, number> = {};
                  for (const m of dmModels) colMax[m] = Math.max(1, ...dmRows.map(r => r.totals[m] ?? 0));
                  return (
                    <div className="bg-white rounded-xl shadow-sm p-5">
                      <h3 className="text-sm font-semibold text-slate-700 mb-1">Dealer × Model Sales Matrix</h3>
                      <p className="text-xs text-slate-400 mb-3">
                        {dmRows.length} dealers × {dmModels.length} models · sorted by total units (desc) · heat-map intensity = column max
                      </p>
                      <div className="overflow-x-auto">
                        <table className="text-xs w-full border-collapse">
                          <thead>
                            <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase text-left">
                              <th className="py-2 px-2 font-semibold sticky left-0 bg-slate-50 z-10 min-w-[160px]">Dealer</th>
                              <th className="py-2 px-2 font-semibold min-w-[80px]">Province</th>
                              <th className="py-2 px-2 font-semibold min-w-[80px]">RM</th>
                              <th className="py-2 px-2 font-semibold min-w-[80px]">ASE</th>
                              <th className="py-2 px-2 font-semibold text-right min-w-[52px]">Total</th>
                              {dmModels.map(m => (
                                <th key={m} className="py-2 px-2 text-right font-semibold min-w-[70px]">{m}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {dmRows.map((r, ri) => (
                              <tr key={ri} className="border-b border-slate-100 hover:bg-slate-50/60">
                                <td className="py-1.5 px-2 font-medium text-slate-800 sticky left-0 bg-white z-10 max-w-[200px] truncate" title={r.dealer}>
                                  {r.dealer}
                                </td>
                                <td className="py-1.5 px-2 text-slate-500">{r.province}</td>
                                <td className="py-1.5 px-2 text-slate-500">{r.rm}</td>
                                <td className="py-1.5 px-2 text-slate-500">{r.ase}</td>
                                <td className="py-1.5 px-2 text-right font-bold text-slate-800">{r.total.toLocaleString()}</td>
                                {dmModels.map(m => {
                                  const v = r.totals[m] ?? 0;
                                  const opacity = v === 0 ? 0 : 0.12 + 0.78 * (v / colMax[m]);
                                  return (
                                    <td key={m} className="py-1.5 px-2 text-right"
                                      style={{
                                        background: v > 0 ? `rgba(67,97,238,${opacity.toFixed(2)})` : "transparent",
                                        color: opacity > 0.55 ? "#fff" : v > 0 ? "#1E3A8A" : "#CBD5E1",
                                        fontWeight: v > 0 ? 600 : 400,
                                      }}>
                                      {v > 0 ? v.toLocaleString() : "—"}
                                    </td>
                                  );
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })()}
              </>
            ) : (
              <div className="flex items-center justify-center py-12 text-slate-400">Loading dealer data…</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
