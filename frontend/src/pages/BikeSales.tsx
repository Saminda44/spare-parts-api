import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  AreaChart, Area, Legend, ComposedChart, Line, LineChart,
} from "recharts";
import { Check } from "lucide-react";
import {
  fetchBikes, fetchModelForecast, fetchTargets, saveTargets, fetchTargetBreakdown,
  type BikesData, type ModelForecastData, type SalesTargets, type TargetBreakdownRow,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TimePicker, filterByRange, type TimeRange, YearPicker, filterByYear, getYears, monthLabel } from "../components/TimePicker";

const MODEL_COLORS = ["#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED","#06B6D4","#F97316","#94A3B8","#10B981","#EC4899"];
const PROV_COLORS  = ["#4361EE","#7C3AED","#2CC56F","#F97316","#EF4444","#06B6D4","#FFC107","#10B981","#EC4899","#94A3B8"];

function colorHex(name: string): string {
  const u = name.toUpperCase();
  if (u.includes("REDDISH YELLOW") || u.includes("YELLOW COCKTAIL")) return "#FFC107";
  if (u.includes("YELLOW"))  return "#FFC107";
  if (u.includes("ORANGE"))  return "#F97316";
  if (u.includes("GREEN"))   return "#2CC56F";
  if (u.includes("CYAN"))    return "#06B6D4";
  if (u.includes("BLUE") || u.includes("PURPLISH")) return "#4361EE";
  if (u.includes("GRAY") || u.includes("GREY"))     return "#94A3B8";
  if (u.includes("RED"))     return "#EF4444";
  if (u.includes("BLACK"))   return "#1E293B";
  return "#CBD5E1";
}

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "mcsi" | "forecast";

export function BikeSales() {
  const [data,      setData]      = useState<BikesData | null>(null);
  const [modelFcst, setModelFcst] = useState<ModelForecastData | null>(null);
  const [tab,       setTab]       = useState<Tab>("mcsi");
  const [mcsiYear,      setMcsiYear]      = useState<number | "All">("All");
  const [mcsiRange,     setMcsiRange]     = useState<TimeRange>("YTD");
  const [forecastYear,  setForecastYear]  = useState<number | "All">("All");
  const [forecastRange, setForecastRange] = useState<TimeRange>("YTD");
  const [selectedModel, setSelectedModel] = useState<string>("All Models");
  const [targets,      setTargets]      = useState<SalesTargets>({ yearly_target: 40000, monthly_overrides: {} });
  const [draftYearly,  setDraftYearly]  = useState("40000");
  const [draftMonthly, setDraftMonthly] = useState<Record<string, string>>({});
  const [pinnedMonths, setPinnedMonths] = useState<Set<string>>(new Set());
  const [saving,       setSaving]       = useState(false);
  const [breakdownMonth, setBreakdownMonth] = useState<string | null>(null);
  const [breakdown,      setBreakdown]      = useState<TargetBreakdownRow[] | null>(null);
  const [breakdownLoading, setBreakdownLoading] = useState(false);

  useEffect(() => {
    fetchBikes().then(d => {
      setData(d);
      const mcsiYrs = getYears(d.mcsi.monthly_trend);
      if (mcsiYrs.length) setMcsiYear(mcsiYrs[0]);
      const fcstYrs = getYears(d.sales_forecast);
      if (fcstYrs.length) setForecastYear(fcstYrs[0]);
    });
    fetchModelForecast().then(setModelFcst);
    fetchTargets().then(t => {
      const year = new Date().getFullYear();
      setTargets(t);
      setDraftYearly(String(t.yearly_target));
      setDraftMonthly(buildMonthlyDraft(t.yearly_target, t.monthly_overrides, year));
      setPinnedMonths(new Set(Object.keys(t.monthly_overrides).filter(k => k.startsWith(`${year}-`))));
      loadBreakdown("Yearly", t.yearly_target);
    });
  }, []);

  const TARGET_YEAR = typeof forecastYear === "number" ? forecastYear : new Date().getFullYear();
  const MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  function monthKey(year: number, m: number) {
    return `${year}-${String(m).padStart(2, "0")}`;
  }

  function buildMonthlyDraft(yearly: number, overrides: Record<string, number>, year: number) {
    const def = Math.round(yearly / 12) || 0;
    const draft: Record<string, string> = {};
    for (let m = 1; m <= 12; m++) {
      const k = monthKey(year, m);
      draft[k] = String(overrides[k] ?? def);
    }
    return draft;
  }

  function rebalanceFree(
    yearly: number,
    pinned: Set<string>,
    draft: Record<string, string>,
    changedKey?: string,
    changedVal?: string,
  ): Record<string, string> {
    const next = { ...draft };
    if (changedKey !== undefined) next[changedKey] = changedVal ?? "";
    const pinnedSum = [...pinned]
      .filter(k => k.startsWith(`${TARGET_YEAR}-`))
      .reduce((s, k) => s + (Number(next[k]) || 0), 0);
    const freeMths: string[] = [];
    for (let m = 1; m <= 12; m++) {
      const mk = monthKey(TARGET_YEAR, m);
      if (!pinned.has(mk)) freeMths.push(mk);
    }
    const remaining = Math.max(0, yearly - pinnedSum);
    const freeCount = freeMths.length;
    if (freeCount > 0) {
      const base = Math.floor(remaining / freeCount);
      const rem = remaining - base * freeCount;
      freeMths.forEach((mk, i) => { next[mk] = String(base + (i < rem ? 1 : 0)); });
    }
    return next;
  }

  function handleYearlyChange(val: string) {
    setDraftYearly(val);
    const yearly = Number(val) || 0;
    setDraftMonthly(prev => rebalanceFree(yearly, pinnedMonths, prev));
  }

  function handleMonthChange(k: string, val: string) {
    const newPinned = new Set(pinnedMonths).add(k);
    setPinnedMonths(newPinned);
    setDraftMonthly(prev => rebalanceFree(Number(draftYearly) || 0, newPinned, prev, k, val));
  }

  function resetMonthlyToDefault() {
    setPinnedMonths(new Set());
    const def = Math.round(Number(draftYearly) / 12) || 0;
    const reset: Record<string, string> = {};
    for (let m = 1; m <= 12; m++) {
      reset[monthKey(TARGET_YEAR, m)] = String(def);
    }
    setDraftMonthly(prev => ({ ...prev, ...reset }));
  }

  function loadBreakdown(k: string, targetVal: number) {
    setBreakdownMonth(k);
    setBreakdownLoading(true);
    setBreakdown(null);
    fetchTargetBreakdown(k, targetVal).then(d => { setBreakdown(d); setBreakdownLoading(false); });
  }

  function handleSaveTargets() {
    setSaving(true);
    const yearly = Number(draftYearly) || 0;
    const def = Math.round(yearly / 12);
    // Only persist months that deviate from the default
    const overrides: Record<string, number> = { ...targets.monthly_overrides };
    for (const [k, v] of Object.entries(draftMonthly)) {
      const num = Number(v) || 0;
      if (num !== def) {
        overrides[k] = num;
      } else {
        delete overrides[k];
      }
    }
    const updated: SalesTargets = { yearly_target: yearly, monthly_overrides: overrides };
    saveTargets(updated).then(() => {
      setTargets(updated);
      setSaving(false);
      fetchBikes().then(setData);
    });
  }

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const { mcsi, sales_forecast, uio_forecast } = data;

  const actualSales  = sales_forecast.filter(r => !r.is_forecast);
  const latestActual = actualSales[actualSales.length - 1];
  const forecastOnly = sales_forecast.filter(r => r.is_forecast);
  const uioActual    = uio_forecast.filter(r => !r.is_forecast);
  const latestUIO    = uioActual[uioActual.length - 1];
  const projectedUIO = uio_forecast[uio_forecast.length - 1];

  // ── Time-filtered data ──────────────────────────────────────────────────────
  const mcsiTrend    = filterByRange(filterByYear(mcsi.monthly_trend, mcsiYear), mcsiRange);
  const fcstFiltered = filterByRange(filterByYear(sales_forecast, forecastYear), forecastRange);


  // ── Model forecast pivot (period → { model: count }) ────────────────────────
  const allModels = modelFcst?.models ?? [];

  /** Stacked bar data for "All Models" view */
  const modelStackData = (() => {
    if (!modelFcst) return [];
    const periods = [...new Set(modelFcst.rows.map(r => r.period))].sort();
    return filterByRange(filterByYear(
      periods.map(p => {
        const obj: Record<string, number | string | boolean> = { period: p, is_forecast: false };
        let isFcst = false;
        for (const r of modelFcst.rows.filter(x => x.period === p)) {
          obj[r.model] = r.actual ?? r.forecast;
          if (r.is_forecast) isFcst = true;
        }
        obj.is_forecast = isFcst;
        return obj as { period: string; is_forecast: boolean } & Record<string, number>;
      }),
      forecastYear,
    ), forecastRange);
  })();

  /** Single-model line data */
  const singleModelData = (() => {
    if (!modelFcst || selectedModel === "All Models") return [];
    return filterByRange(filterByYear(
      modelFcst.rows
        .filter(r => r.model === selectedModel)
        .sort((a, b) => a.period.localeCompare(b.period))
        .map(r => ({ period: r.period, actual: r.actual, forecast: r.forecast, is_forecast: r.is_forecast })),
      forecastYear,
    ), forecastRange);
  })();

  const TABS: { key: Tab; label: string }[] = [
    { key: "mcsi",     label: "MCSI Bike Sales (Stage 1)"    },
    { key: "forecast", label: "Unit Sales Forecast (Stage 2)" },
  ];

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Bike Sales &amp; Fleet Analytics</h2>
      <p className="text-xs text-slate-500 -mt-4">Stages 1–3 · MCSI motorcycle sales, unit sales forecast, units-in-operation (UIO) growth model</p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total Bikes Sold"  value={fmt(mcsi.total_sold)}            sub={`${mcsi.date_from} → ${mcsi.date_to}`} color="blue"/>
        <KpiCard label="Latest Monthly"    value={latestActual ? fmt(latestActual.actual ?? latestActual.forecast) : "—"} sub={latestActual?.period ?? ""} color="green"/>
        <KpiCard label="12-mo Forecast"   value={fmt(forecastOnly.reduce((s,r)=>s+r.forecast,0))} sub="next 12 months" color="purple"/>
        <KpiCard label="Current UIO"       value={fmt(latestUIO?.uio_total ?? 0)}   sub={`→ ${fmt(projectedUIO?.uio_total ?? 0)} by ${projectedUIO?.period ?? ""}`} color="teal"/>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex gap-1 mb-5 border-b border-slate-100 pb-2 flex-wrap">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${tab === t.key ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* ── MCSI tab ── */}
        {tab === "mcsi" && (
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Monthly trend */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-slate-700">Monthly Sales — Units &amp; Revenue</h3>
                  <div className="flex items-center gap-2">
                    <YearPicker years={getYears(mcsi.monthly_trend)} value={mcsiYear} onChange={setMcsiYear}/>
                    <TimePicker value={mcsiRange} onChange={setMcsiRange}/>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={210}>
                  <ComposedChart data={mcsiTrend} margin={{ top:5, right:44, left:0, bottom:5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                      tickFormatter={p => monthLabel(p, mcsiYear !== "All")}/>
                    <YAxis yAxisId="left"  tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown, name: unknown) => [`${name === "Revenue (LKR)" ? "LKR " : ""}${fmt(Number(v))}`, String(name)]}/>
                    <Legend wrapperStyle={{ fontSize: 11 }}/>
                    <Bar   yAxisId="left"  dataKey="sold"        name="Units Sold"    fill="#4361EE" radius={[3,3,0,0]} opacity={0.85}/>
                    <Line  yAxisId="right" dataKey="revenue_lkr" name="Revenue (LKR)" stroke="#F97316" strokeWidth={2} dot={false}/>
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* By model */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Sales by Model</h3>
                <ResponsiveContainer width="100%" height={210}>
                  <BarChart data={mcsi.by_model} layout="vertical" margin={{ top:0, right:40, left:10, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="model" tick={{ fontSize: 9 }} width={130}/>
                    <Tooltip formatter={(v: unknown, n: unknown) => [Number(v).toLocaleString(), n === "count" ? "Units" : String(n)]}/>
                    <Bar dataKey="count" radius={[0,3,3,0]}>
                      {mcsi.by_model.map((d, i) => <Cell key={d.model} fill={MODEL_COLORS[i % MODEL_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Province breakdown */}
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-2">Sales by Province</h3>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={mcsi.by_province} margin={{ top:5, right:10, left:0, bottom:0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                  <XAxis dataKey="province" tick={{ fontSize: 11 }}/>
                  <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                  <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                  <Bar dataKey="count" radius={[4,4,0,0]}>
                    {mcsi.by_province.map((d, i) => <Cell key={d.province} fill={PROV_COLORS[i % PROV_COLORS.length]}/>)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Model table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-4">Model</th>
                    <th className="py-2 pr-4 text-right">Units Sold</th>
                    <th className="py-2 text-right">Share %</th>
                  </tr>
                </thead>
                <tbody>
                  {mcsi.by_model.map((r, i) => (
                    <tr key={r.model} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-4 flex items-center gap-2">
                        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }}/>
                        {r.model}
                      </td>
                      <td className="py-2 pr-4 text-right font-semibold">{r.count.toLocaleString()}</td>
                      <td className="py-2 text-right text-slate-500">{r.pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

          </div>
        )}

        {/* ── Unit Sales Forecast tab ── */}
        {tab === "forecast" && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Latest Actual"  value={fmt(latestActual?.actual ?? 0)}    sub={latestActual?.period ?? ""} color="blue"/>
              <KpiCard label="Next Month"     value={fmt(forecastOnly[0]?.forecast ?? 0)} sub={forecastOnly[0]?.period ?? ""} color="purple"/>
              <KpiCard label="12-mo Total"    value={fmt(forecastOnly.reduce((s,r)=>s+r.forecast,0))} color="green"/>
              <KpiCard label="Target Gap"     value={forecastOnly[0] ? `${(forecastOnly[0].target_gap ?? 0).toLocaleString()}` : "—"} sub="next month vs target" color={(forecastOnly[0]?.target_gap ?? 0) < 0 ? "red" : "green"}/>
            </div>

            {/* Controls row */}
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-500">Model:</span>
                <select
                  className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
                  value={selectedModel}
                  onChange={e => setSelectedModel(e.target.value)}
                >
                  <option value="All Models">All Models (Total)</option>
                  <optgroup label="By Model">
                    {allModels.map(m => <option key={m} value={m}>{m}</option>)}
                  </optgroup>
                </select>
              </div>

              <div className="flex-1"/>
              <div className="flex items-center gap-2">
                <YearPicker years={getYears(sales_forecast)} value={forecastYear} onChange={setForecastYear}/>
                <TimePicker value={forecastRange} onChange={setForecastRange}/>
              </div>
            </div>

            {/* ── Sales Targets — always-visible section ── */}
            {(() => {
              const defVal = Math.round(Number(draftYearly) / 12) || 0;
              const monthlySum = Object.entries(draftMonthly)
                .filter(([k]) => k.startsWith(`${TARGET_YEAR}-`))
                .reduce((s, [, v]) => s + (Number(v) || 0), 0);
              const yearlyNum = Number(draftYearly) || 0;
              const delta = monthlySum - yearlyNum;
              return (
                <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 space-y-4">
                  <h3 className="text-sm font-semibold text-slate-700">Sales Targets</h3>

                  {/* ── Yearly row ── */}
                  <div className="flex flex-wrap items-center gap-4">
                    <div className="flex items-center gap-2">
                      <label className="text-xs font-medium text-slate-600">Yearly Target (units):</label>
                      <input
                        type="number"
                        value={draftYearly}
                        onChange={e => handleYearlyChange(e.target.value)}
                        className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
                      />
                    </div>
                    <p className="text-xs text-slate-500 flex-1">
                      Default per month = {defVal.toLocaleString()} units &nbsp;·&nbsp; Custom months show in <span className="text-blue-700 font-semibold">blue</span>
                    </p>
                  </div>

                  {/* ── Monthly grid ── */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                        Monthly Targets — {TARGET_YEAR}
                      </p>
                      <button
                        onClick={resetMonthlyToDefault}
                        className="text-[11px] text-slate-400 hover:text-red-500 transition-colors"
                      >
                        Reset all to default
                      </button>
                    </div>
                    <div className="grid grid-cols-6 gap-2">
                      {MONTH_LABELS.map((lbl, idx) => {
                        const k = monthKey(TARGET_YEAR, idx + 1);
                        const isCustom = pinnedMonths.has(k);
                        return (
                          <div key={k}>
                            <p className="text-[10px] text-slate-500 text-center mb-1">{lbl}</p>
                            <input
                              type="number"
                              value={draftMonthly[k] ?? defVal}
                              onChange={e => handleMonthChange(k, e.target.value)}
                              onFocus={() => loadBreakdown(k, Number(draftMonthly[k] ?? defVal))}
                              className={`w-full rounded-lg px-1 py-1.5 text-xs text-center focus:outline-none transition-colors cursor-pointer ${
                                breakdownMonth === k
                                  ? "ring-2 ring-amber-400 border-amber-400 bg-amber-50 font-bold text-amber-800"
                                  : isCustom
                                    ? "border-2 border-blue-400 bg-blue-50 font-bold text-blue-800"
                                    : "border border-slate-200 bg-white text-slate-700"
                              }`}
                            />
                          </div>
                        );
                      })}
                    </div>
                    {/* Sum vs yearly */}
                    <div className="flex items-center gap-3 mt-2 text-xs text-slate-500">
                      <span>Σ monthly = <strong className={delta === 0 ? "text-green-600" : "text-amber-600"}>{monthlySum.toLocaleString()}</strong></span>
                      <span>vs yearly {yearlyNum.toLocaleString()}</span>
                      {delta !== 0 && (
                        <span className={`font-semibold ${delta > 0 ? "text-red-500" : "text-amber-600"}`}>
                          {delta > 0 ? "+" : ""}{delta.toLocaleString()} {delta > 0 ? "over" : "under"}
                        </span>
                      )}
                      {delta === 0 && <span className="text-green-600 font-semibold">✓ balanced</span>}
                    </div>
                  </div>

                  {/* ── Save ── */}
                  <div className="flex items-center justify-end pt-1 border-t border-blue-200">
                    <button onClick={handleSaveTargets} disabled={saving}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-blue text-white hover:bg-blue-700 transition-colors disabled:opacity-50">
                      <Check size={13}/> {saving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              );
            })()}

            {/* ── Breakdown panel (yearly default / month on focus) ── */}
            {breakdownMonth && (
              <div className="bg-white rounded-xl shadow-sm border border-amber-200 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold text-slate-700">
                      {breakdownMonth === "Yearly" ? "Yearly Target" : `${breakdownMonth} — Target`}
                      <span className="ml-1.5 text-amber-600 font-bold">
                        ({(breakdownMonth === "Yearly"
                          ? Number(draftYearly)
                          : Number(draftMonthly[breakdownMonth] ?? 0)
                        ).toLocaleString()} units)
                      </span>
                    </h3>
                    {breakdownMonth !== "Yearly" && (
                      <button
                        onClick={() => loadBreakdown("Yearly", Number(draftYearly) || 0)}
                        className="text-xs text-slate-400 hover:text-amber-600 transition-colors underline underline-offset-2"
                      >
                        ← Yearly
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-slate-400">Based on last 12 months MCSI mix</p>
                </div>
                {breakdownLoading && <p className="text-xs text-slate-400 animate-pulse">Loading…</p>}
                {breakdown && (
                  <div className="overflow-x-auto">
                    <table className="text-xs w-full">
                      <thead>
                        <tr className="border-b border-slate-200 text-left text-slate-500 uppercase tracking-wide">
                          <th className="py-1.5 pr-3 font-medium">Model</th>
                          <th className="py-1.5 pr-3 font-medium">Color</th>
                          <th className="py-1.5 pr-3 text-right font-medium">Historical (12m)</th>
                          <th className="py-1.5 pr-3 text-right font-medium">Share %</th>
                          <th className="py-1.5 text-right font-bold text-slate-700">Allocated</th>
                        </tr>
                      </thead>
                      <tbody>
                        {breakdown.map((r, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-amber-50/40 transition-colors">
                            <td className="py-1.5 pr-3 font-medium text-slate-700">{r.model}</td>
                            <td className="py-1.5 pr-3">
                              <span className="inline-flex items-center gap-1.5">
                                <span className="w-2.5 h-2.5 rounded-full inline-block shrink-0 border border-white shadow-sm"
                                  style={{ background: colorHex(r.color) }}/>
                                {r.color}
                              </span>
                            </td>
                            <td className="py-1.5 pr-3 text-right text-slate-500">{r.historical_units.toLocaleString()}</td>
                            <td className="py-1.5 pr-3 text-right text-slate-500">{r.share_pct.toFixed(1)}%</td>
                            <td className="py-1.5 text-right font-bold text-amber-700">{r.allocated_units.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* ── All Models: total forecast chart ── */}
            {selectedModel === "All Models" && (
              <>
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-1">Total — Actual vs Forecast (80% CI)</h3>
                  <p className="text-xs text-slate-400 mb-3">Shaded band = 80% confidence interval. Blue background = forecast horizon.</p>
                  <ResponsiveContainer width="100%" height={260}>
                    <AreaChart data={fcstFiltered} margin={{ top:5, right:10, left:0, bottom:5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                        tickFormatter={p => monthLabel(p, forecastYear !== "All")}/>
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                      <Tooltip formatter={(v: unknown) => v != null ? Number(v).toLocaleString() : "—"}/>
                      <Legend/>
                      <Area type="monotone" dataKey="upper_80" stroke="none" fill="#4361EE" fillOpacity={0.15} name="Upper 80%"/>
                      <Area type="monotone" dataKey="lower_80" stroke="none" fill="#fff" fillOpacity={1} name="Lower 80%" legendType="none"/>
                      <Area type="monotone" dataKey="forecast" stroke="#4361EE" strokeWidth={2} fill="none" name="Forecast" strokeDasharray="5 3"/>
                      <Area type="monotone" dataKey="actual"   stroke="#2CC56F" strokeWidth={2} fill="none" name="Actual" dot={{ r: 3 }}/>
                      <Area type="monotone" dataKey="target"   stroke="#FFC107" strokeWidth={1.5} fill="none" name="Target" strokeDasharray="3 2"/>
                    </AreaChart>
                  </ResponsiveContainer>
                </div>

                {/* Stacked bar by model */}
                {modelFcst && (
                  <div>
                    <h3 className="text-sm font-semibold text-slate-700 mb-1">Sales &amp; Forecast by Model</h3>
                    <p className="text-xs text-slate-400 mb-3">Stacked bars show each model's contribution. Lighter colors = forecast periods.</p>
                    <ResponsiveContainer width="100%" height={240}>
                      <BarChart data={modelStackData} margin={{ top:5, right:10, left:0, bottom:5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                        <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                          tickFormatter={p => monthLabel(p, forecastYear !== "All")}/>
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                        <Tooltip formatter={(v: unknown, n: unknown) => [Number(v).toLocaleString(), String(n)]}/>
                        <Legend wrapperStyle={{ fontSize: 10 }}/>
                        {allModels.map((m, i) => (
                          <Bar key={m} dataKey={m} stackId="a" fill={MODEL_COLORS[i % MODEL_COLORS.length]}
                            name={m} radius={i === allModels.length - 1 ? [3,3,0,0] : undefined}
                          >
                            {modelStackData.map((entry, idx) => (
                              <Cell key={`${m}-${idx}`} fillOpacity={entry.is_forecast ? 0.45 : 1}/>
                            ))}
                          </Bar>
                        ))}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </>
            )}

            {/* ── Single model: actuals + forecast line ── */}
            {selectedModel !== "All Models" && (
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-1">
                  {selectedModel} — Actual vs Forecast
                </h3>
                <p className="text-xs text-slate-400 mb-3">
                  Forecast = total forecast × this model's historical share ({
                    allModels.length > 0
                      ? ((modelFcst?.rows.filter(r => r.model === selectedModel && !r.is_forecast).reduce((s,r) => s + (r.actual ?? 0), 0) ?? 0) /
                        (modelFcst?.rows.filter(r => !r.is_forecast).reduce((s,r) => s + (r.actual ?? 0), 0) || 1) * 100).toFixed(1)
                      : "—"
                  }% of total).
                </p>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={singleModelData} margin={{ top:5, right:10, left:0, bottom:5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                      tickFormatter={p => monthLabel(p, forecastYear !== "All")}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown, n: unknown) => [v != null ? Number(v).toLocaleString() : "—", String(n)]}/>
                    <Legend/>
                    <Line type="monotone" dataKey="actual"   stroke="#2CC56F" strokeWidth={2.5} dot={{ r: 4 }} name="Actual" connectNulls={false}/>
                    <Line type="monotone" dataKey="forecast" stroke="#4361EE" strokeWidth={2} strokeDasharray="6 3" dot={false} name="Forecast"/>
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Table */}
            {selectedModel === "All Models" ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-3">Period</th>
                      <th className="py-2 pr-3 text-right">Forecast</th>
                      <th className="py-2 pr-3 text-right">Lower 80%</th>
                      <th className="py-2 pr-3 text-right">Upper 80%</th>
                      <th className="py-2 pr-3 text-right">Actual</th>
                      <th className="py-2 pr-3 text-right">Target</th>
                      <th className="py-2 text-right">Target Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fcstFiltered.map(r => (
                      <tr key={r.period} className={`border-b border-slate-50 hover:bg-slate-50/50 ${r.is_forecast ? "bg-blue-50/30" : ""}`}>
                        <td className="py-2 pr-3 font-mono text-xs">{r.period}</td>
                        <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.forecast.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right text-xs text-slate-400">{r.lower_80.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right text-xs text-slate-400">{r.upper_80.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right">{r.actual != null ? r.actual.toLocaleString() : <span className="text-slate-300">—</span>}</td>
                        <td className="py-2 pr-3 text-right text-xs text-amber-600">{r.target != null ? r.target.toLocaleString() : "—"}</td>
                        <td className="py-2 text-right text-xs" style={{ color: (r.target_gap ?? 0) < 0 ? "#EF4444" : "#2CC56F" }}>
                          {r.target_gap != null ? r.target_gap.toLocaleString() : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-3">Period</th>
                      <th className="py-2 pr-3 text-right">Actual</th>
                      <th className="py-2 text-right">Forecast (mix-adjusted)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {singleModelData.map(r => (
                      <tr key={r.period} className={`border-b border-slate-50 hover:bg-slate-50/50 ${r.is_forecast ? "bg-blue-50/30" : ""}`}>
                        <td className="py-2 pr-3 font-mono text-xs">{r.period}</td>
                        <td className="py-2 pr-3 text-right text-green-600 font-semibold">
                          {r.actual != null ? r.actual.toLocaleString() : <span className="text-slate-300">—</span>}
                        </td>
                        <td className="py-2 text-right font-semibold text-brand-blue">{r.forecast.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
