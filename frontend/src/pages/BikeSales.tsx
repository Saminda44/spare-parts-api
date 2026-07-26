import { useEffect, useState, Fragment } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  AreaChart, Area, Legend, Line, LineChart,
} from "recharts";
import { Check } from "lucide-react";
import {
  fetchBikes, fetchModelForecast, fetchTargets, saveTargets, fetchTargetBreakdown,
  fetchUpliftInputs, saveUpliftInputs, fetchDealerUpliftBaseline,
  fetchM1AgeDist,
  type BikesData, type ModelForecastData, type SalesTargets, type TargetBreakdownRow,
  type UpliftFactorsRow, type M1AgeDistResponse,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TimePicker, filterByRange, type TimeRange, YearPicker, filterByYear, getYears, monthLabel } from "../components/TimePicker";

const MODEL_COLORS = ["#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED","#06B6D4","#F97316","#94A3B8","#10B981","#EC4899"];

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

export function BikeSales() {
  const [data,      setData]      = useState<BikesData | null>(null);
  const [modelFcst, setModelFcst] = useState<ModelForecastData | null>(null);
  const [ageDist,   setAgeDist]   = useState<M1AgeDistResponse | null>(null);
  const [forecastYear,  setForecastYear]  = useState<number | "All">("All");
  const [forecastRange, setForecastRange] = useState<TimeRange>("YTD");
  const [selectedModel, setSelectedModel] = useState<string>("All Models");
  const [targets,      setTargets]      = useState<SalesTargets>({ yearly_target: 40000, monthly_overrides: {} });
  const [draftYearly,  setDraftYearly]  = useState("40000");
  const [draftMonthly, setDraftMonthly] = useState<Record<string, string>>({});
  const [pinnedMonths, setPinnedMonths] = useState<Set<string>>(new Set());
  const [saving,       setSaving]       = useState(false);
  const [breakdownMonth,   setBreakdownMonth]   = useState<string | null>(null);
  const [breakdown,        setBreakdown]        = useState<TargetBreakdownRow[] | null>(null);
  const [breakdownTarget,  setBreakdownTarget]  = useState<number | null>(null);
  const [breakdownLoading, setBreakdownLoading] = useState(false);

  // Blend weight: 1 = 100% forecast, 0 = 100% target
  const [blendWeight, setBlendWeight] = useState(0.5);

  // Uplift factors state
  const [upliftOpen,        setUpliftOpen]        = useState(false);
  const [upliftInputs,      setUpliftInputs]      = useState<Record<string, UpliftFactorsRow>>({});
  const [dealerBaseline,    setDealerBaseline]    = useState<Record<string, number>>({});
  const [savingUplift,      setSavingUplift]      = useState(false);

  useEffect(() => {
    fetchBikes().then(d => {
      setData(d);
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
    fetchUpliftInputs().then(rows => {
      const map: Record<string, UpliftFactorsRow> = {};
      rows.forEach(r => { map[r.month_key] = r; });
      setUpliftInputs(map);
    });
    fetchDealerUpliftBaseline().then(setDealerBaseline);
    fetchM1AgeDist().then(setAgeDist).catch(() => {/* module not yet run — silently skip */});
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

  // lockUpToMonth: months 1..lockUpToMonth keep their current value; only later unpinned months rebalance.
  function rebalanceFree(
    yearly: number,
    pinned: Set<string>,
    draft: Record<string, string>,
    changedKey?: string,
    changedVal?: string,
    lockUpToMonth: number = 0,
  ): Record<string, string> {
    const next = { ...draft };
    if (changedKey !== undefined) next[changedKey] = changedVal ?? "";
    let lockedSum = 0;
    const freeMths: string[] = [];
    for (let m = 1; m <= 12; m++) {
      const mk = monthKey(TARGET_YEAR, m);
      if (m <= lockUpToMonth || pinned.has(mk)) {
        lockedSum += Number(next[mk]) || 0;
      } else {
        freeMths.push(mk);
      }
    }
    const remaining = yearly - lockedSum;
    const freeCount = freeMths.length;
    if (freeCount > 0 && remaining >= 0) {
      const base = Math.floor(remaining / freeCount);
      const rem = remaining - base * freeCount;
      freeMths.forEach((mk, i) => { next[mk] = String(base + (i < rem ? 1 : 0)); });
    }
    // If freeCount === 0 or remaining < 0: leave values as-is — UI shows imbalance warning.
    return next;
  }

  function handleYearlyChange(val: string) {
    setDraftYearly(val);
    const yearly = Number(val) || 0;
    // Only rebalance from the current calendar month onward; past months are frozen.
    const lockUpto = new Date().getMonth(); // getMonth() is 0-based → equals (currentMonth - 1)
    setDraftMonthly(prev => rebalanceFree(yearly, pinnedMonths, prev, undefined, undefined, lockUpto));
  }

  function handleMonthChange(k: string, val: string) {
    const newPinned = new Set(pinnedMonths).add(k);
    setPinnedMonths(newPinned);
    // Freeze the edited month and everything before it; rebalance only what comes after.
    const m = parseInt(k.split("-")[1]); // 1-12
    setDraftMonthly(prev => rebalanceFree(Number(draftYearly) || 0, newPinned, prev, k, val, m));
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
    setBreakdownTarget(targetVal);
    setBreakdownLoading(true);
    setBreakdown(null);
    fetchTargetBreakdown(k, targetVal).then(d => { setBreakdown(d); setBreakdownLoading(false); });
  }

  function getUpliftRow(k: string): UpliftFactorsRow {
    return upliftInputs[k] ?? {
      month_key: k,
      promotion_pct: 0, new_model_pct: 0,
      dealer_pct: dealerBaseline[k] ?? 0,
      pricing_pct: 0, other_pct: 0,
    };
  }

  function setUpliftField(k: string, field: keyof Omit<UpliftFactorsRow, "month_key">, val: string) {
    setUpliftInputs(prev => ({
      ...prev,
      [k]: { ...getUpliftRow(k), [field]: parseFloat(val) || 0 },
    }));
  }

  function totalUpliftPct(k: string): number {
    const r = getUpliftRow(k);
    return r.promotion_pct + r.new_model_pct + r.dealer_pct + r.pricing_pct + r.other_pct;
  }

  function handleSaveUplift() {
    setSavingUplift(true);
    const rows = Object.values(upliftInputs);
    saveUpliftInputs(rows).finally(() => setSavingUplift(false));
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

  const { sales_forecast } = data;

  const actualSales  = sales_forecast.filter(r => !r.is_forecast);
  const latestActual = actualSales[actualSales.length - 1];
  const forecastOnly = sales_forecast.filter(r => r.is_forecast);

  // ── Time-filtered data ──────────────────────────────────────────────────────
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

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Unit Sales Forecast</h2>
      <p className="text-xs text-slate-500 -mt-4">Stage 2 · Model-level unit sales forecast with uplift adjustments and monthly targets</p>

      <div className="bg-white rounded-xl shadow-sm p-5">
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Latest Actual"  value={fmt(latestActual?.actual ?? 0)}    sub={latestActual?.period ?? ""} color="blue"/>
              <KpiCard label="Next Month"     value={fmt(forecastOnly[0]?.forecast ?? 0)} sub={forecastOnly[0]?.period ?? ""} color="purple"/>
              <KpiCard label="12-mo Total"    value={fmt(forecastOnly.reduce((s,r)=>s+r.forecast,0))} color="green"/>
              <KpiCard label="Target Gap"     value={forecastOnly[0] ? `${(forecastOnly[0].target_gap ?? 0).toLocaleString()}` : "—"} sub="next month vs target" color={(forecastOnly[0]?.target_gap ?? 0) < 0 ? "red" : "green"}/>
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
                            <p
                              onClick={() => loadBreakdown(k, Number(draftMonthly[k] ?? defVal))}
                              className={`text-[10px] text-center mb-1 cursor-pointer select-none transition-colors ${
                                breakdownMonth === k
                                  ? "text-amber-600 font-bold underline underline-offset-2"
                                  : "text-slate-500 hover:text-amber-500"
                              }`}
                            >{lbl}</p>
                            <input
                              type="number"
                              value={draftMonthly[k] ?? defVal}
                              onChange={e => handleMonthChange(k, e.target.value)}
                              className={`w-full rounded-lg px-1 py-1.5 text-xs text-center focus:outline-none focus:ring-2 focus:ring-brand-blue/30 transition-colors ${
                                isCustom
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
                        ({(breakdownTarget ?? 0).toLocaleString()} units)
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
                {breakdown && (() => {
                  // Build ordered model groups preserving API sort (desc allocated)
                  const groups: { model: string; rows: TargetBreakdownRow[] }[] = [];
                  const seen = new Map<string, TargetBreakdownRow[]>();
                  for (const r of breakdown) {
                    if (!seen.has(r.model)) {
                      const arr: TargetBreakdownRow[] = [];
                      seen.set(r.model, arr);
                      groups.push({ model: r.model, rows: arr });
                    }
                    seen.get(r.model)!.push(r);
                  }
                  return (
                  <div className="overflow-x-auto">
                    <table className="text-xs w-full">
                      <thead>
                        <tr className="border-b-2 border-slate-200 text-left text-slate-500 uppercase tracking-wide">
                          <th className="py-1.5 pr-3 font-medium">Model / Color</th>
                          <th className="py-1.5 pr-3 text-right font-medium">Historical (12m)</th>
                          <th className="py-1.5 pr-3 text-right font-medium">Share %</th>
                          <th className="py-1.5 text-right font-bold text-slate-700">Allocated</th>
                        </tr>
                      </thead>
                      <tbody>
                        {groups.map(({ model, rows }) => {
                          const histTotal  = rows.reduce((s, r) => s + r.historical_units, 0);
                          const shareTotal = rows.reduce((s, r) => s + r.share_pct, 0);
                          const allocTotal = rows.reduce((s, r) => s + r.allocated_units, 0);
                          return (
                            <Fragment key={model}>
                              <tr className="bg-slate-50 border-t-2 border-slate-200">
                                <td className="py-2 pr-3 font-semibold text-slate-800">{model}</td>
                                <td className="py-2 pr-3 text-right text-slate-600 font-medium">{histTotal.toLocaleString()}</td>
                                <td className="py-2 pr-3 text-right text-slate-600 font-medium">{shareTotal.toFixed(1)}%</td>
                                <td className="py-2 text-right font-bold text-amber-700">{allocTotal.toLocaleString()}</td>
                              </tr>
                              {rows.map((r, i) => (
                                <tr key={i} className="border-b border-slate-100 hover:bg-amber-50/30 transition-colors">
                                  <td className="py-1 pl-5 pr-3">
                                    <span className="inline-flex items-center gap-1.5 text-slate-600">
                                      <span className="w-2 h-2 rounded-full shrink-0"
                                        style={{ background: colorHex(r.color) }}/>
                                      {r.color}
                                    </span>
                                  </td>
                                  <td className="py-1 pr-3 text-right text-slate-400">{r.historical_units.toLocaleString()}</td>
                                  <td className="py-1 pr-3 text-right text-slate-400">{r.share_pct.toFixed(1)}%</td>
                                  <td className="py-1 text-right text-amber-600 font-medium">{r.allocated_units.toLocaleString()}</td>
                                </tr>
                              ))}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  );
                })()}
              </div>
            )}

            {/* ── Forecast chart — controls + chart ── */}
            <div>
              {/* Controls row — always visible regardless of selected model */}
              <div className="flex flex-wrap items-center gap-3 mb-4">
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

              {/* ── Uplift Factors accordion ── */}
              {(() => {
                const FACTORS: { key: keyof Omit<UpliftFactorsRow, "month_key">; label: string }[] = [
                  { key: "promotion_pct",  label: "Promotions" },
                  { key: "new_model_pct", label: "New Model" },
                  { key: "dealer_pct",    label: "Dealer Exp." },
                  { key: "pricing_pct",   label: "Pricing" },
                  { key: "other_pct",     label: "Other" },
                ];
                return (
                  <div className="border border-slate-200 rounded-xl overflow-hidden">
                    {/* Accordion header */}
                    <button
                      onClick={() => setUpliftOpen(o => !o)}
                      className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
                    >
                      <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">
                        ▲ Uplift Factors
                        <span className="ml-2 font-normal text-slate-400 normal-case tracking-normal">
                          — adjust base forecast for promotions, new models, dealer growth, pricing
                        </span>
                      </span>
                      <span className="text-xs text-slate-400">{upliftOpen ? "▲ collapse" : "▼ expand"}</span>
                    </button>

                    {upliftOpen && (
                      <div className="p-4 space-y-3 bg-white">
                        <div className="overflow-x-auto">
                          <table className="text-xs w-full">
                            <thead>
                              <tr className="border-b border-slate-200">
                                <th className="py-1.5 pr-4 text-left font-medium text-slate-500 uppercase tracking-wide w-28">Factor</th>
                                {MONTH_LABELS.map((lbl, idx) => {
                                  const k = monthKey(TARGET_YEAR, idx + 1);
                                  const tot = totalUpliftPct(k);
                                  return (
                                    <th key={k} className="py-1.5 px-1 text-center font-medium text-slate-500 min-w-[58px]">
                                      <div>{lbl}</div>
                                      {tot !== 0 && (
                                        <div className={`text-[10px] font-bold ${tot > 0 ? "text-green-600" : "text-red-500"}`}>
                                          {tot > 0 ? "+" : ""}{tot.toFixed(1)}%
                                        </div>
                                      )}
                                    </th>
                                  );
                                })}
                              </tr>
                            </thead>
                            <tbody>
                              {FACTORS.map(({ key, label }) => (
                                <tr key={key} className="border-b border-slate-50">
                                  <td className="py-1.5 pr-4 font-medium text-slate-600">
                                    {label}
                                    {key === "dealer_pct" && (
                                      <span className="ml-1 text-[10px] text-slate-400">*auto</span>
                                    )}
                                  </td>
                                  {MONTH_LABELS.map((_, idx) => {
                                    const k = monthKey(TARGET_YEAR, idx + 1);
                                    const val = getUpliftRow(k)[key];
                                    return (
                                      <td key={k} className="py-1 px-1">
                                        <div className="relative">
                                          <input
                                            type="number"
                                            step="0.1"
                                            value={val === 0 ? "" : val}
                                            placeholder="0"
                                            onChange={e => setUpliftField(k, key, e.target.value)}
                                            className={`w-full rounded px-1 py-1 text-[11px] text-center border focus:outline-none focus:ring-1 focus:ring-brand-blue/40 ${
                                              val > 0 ? "border-green-300 bg-green-50 text-green-700" :
                                              val < 0 ? "border-red-300 bg-red-50 text-red-700" :
                                              "border-slate-200 bg-white text-slate-500"
                                            }`}
                                          />
                                          <span className="absolute right-1 top-1/2 -translate-y-1/2 text-[9px] text-slate-400 pointer-events-none">%</span>
                                        </div>
                                      </td>
                                    );
                                  })}
                                </tr>
                              ))}
                              {/* Total row */}
                              <tr className="bg-slate-50 font-semibold">
                                <td className="py-1.5 pr-4 text-slate-700">Total uplift</td>
                                {MONTH_LABELS.map((_, idx) => {
                                  const k = monthKey(TARGET_YEAR, idx + 1);
                                  const tot = totalUpliftPct(k);
                                  return (
                                    <td key={k} className={`py-1.5 px-1 text-center text-[11px] ${
                                      tot > 0 ? "text-green-600" : tot < 0 ? "text-red-500" : "text-slate-400"
                                    }`}>
                                      {tot !== 0 ? `${tot > 0 ? "+" : ""}${tot.toFixed(1)}%` : "—"}
                                    </td>
                                  );
                                })}
                              </tr>
                            </tbody>
                          </table>
                        </div>
                        <div className="flex items-center justify-between pt-1 border-t border-slate-100">
                          <p className="text-[11px] text-slate-400">
                            *Dealer Exp. auto-filled from MCSI active-dealer trend (0.8× elasticity) — editable
                          </p>
                          <button
                            onClick={handleSaveUplift}
                            disabled={savingUplift}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-blue text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
                          >
                            <Check size={13}/> {savingUplift ? "Saving…" : "Save Uplift"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

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
              <div className="space-y-3">
                {/* Blend weight slider */}
                <div className="flex items-center gap-3 px-1">
                  <span className="text-xs text-slate-500 shrink-0 w-28">Forecast ↔ Target blend</span>
                  <input
                    type="range" min={0} max={100} step={5}
                    value={Math.round(blendWeight * 100)}
                    onChange={e => setBlendWeight(Number(e.target.value) / 100)}
                    className="flex-1 accent-violet-600 h-1.5 cursor-pointer"
                  />
                  <span className="text-xs font-mono text-violet-600 shrink-0 w-24 text-right">
                    {Math.round(blendWeight * 100)}% fcst + {Math.round((1 - blendWeight) * 100)}% target
                  </span>
                </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-3">Period</th>
                      <th className="py-2 pr-3 text-right">Base Forecast</th>
                      <th className="py-2 pr-3 text-right text-violet-600">Adjusted</th>
                      <th className="py-2 pr-3 text-right text-emerald-600">Combined</th>
                      <th className="py-2 pr-3 text-right">Lower 80%</th>
                      <th className="py-2 pr-3 text-right">Upper 80%</th>
                      <th className="py-2 pr-3 text-right">Actual</th>
                      <th className="py-2 pr-3 text-right">Target</th>
                      <th className="py-2 text-right">Target Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fcstFiltered.map(r => {
                      const upliftPct = totalUpliftPct(r.period);
                      const adjusted = r.actual != null
                        ? r.actual
                        : Math.round(r.forecast * (1 + upliftPct / 100));
                      const combined = r.actual != null
                        ? r.actual
                        : r.target != null
                          ? Math.round(blendWeight * adjusted + (1 - blendWeight) * r.target)
                          : adjusted;
                      const combinedGap = r.target != null ? combined - r.target : null;
                      return (
                      <tr key={r.period} className={`border-b border-slate-50 hover:bg-slate-50/50 ${r.is_forecast ? "bg-blue-50/30" : ""}`}>
                        <td className="py-2 pr-3 font-mono text-xs">{r.period}</td>
                        <td className="py-2 pr-3 text-right font-semibold text-brand-blue">{r.forecast.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right font-bold text-violet-600">
                          {adjusted.toLocaleString()}
                          {r.actual == null && upliftPct !== 0 && (
                            <span className={`ml-1 text-[10px] font-normal ${upliftPct > 0 ? "text-green-500" : "text-red-400"}`}>
                              ({upliftPct > 0 ? "+" : ""}{upliftPct.toFixed(1)}%)
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-right font-bold text-emerald-600">{combined.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right text-xs text-slate-400">{r.lower_80.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right text-xs text-slate-400">{r.upper_80.toLocaleString()}</td>
                        <td className="py-2 pr-3 text-right">{r.actual != null ? r.actual.toLocaleString() : <span className="text-slate-300">—</span>}</td>
                        <td className="py-2 pr-3 text-right text-xs text-amber-600">{r.target != null ? r.target.toLocaleString() : "—"}</td>
                        <td className="py-2 text-right text-xs" style={{ color: (combinedGap ?? 0) < 0 ? "#EF4444" : "#2CC56F" }}>
                          {combinedGap != null ? combinedGap.toLocaleString() : "—"}
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
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
            </div>{/* end forecast section */}
          </div>

      </div>

      {/* ── Age Distribution (Module 1) ─────────────────────────────────── */}
      {ageDist && ageDist.rows.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
          <div>
            <h3 className="text-sm font-bold text-slate-800">Fleet Age Distribution (Module 1)</h3>
            <p className="text-xs text-slate-400 mt-0.5">Age cohort breakdown of the UIO fleet per model — older cohorts drive higher parts replacement demand.</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                  <th className="py-2 pr-4">Model</th>
                  <th className="py-2 pr-4">Age Cohort</th>
                  <th className="py-2 pr-4 text-right">Vehicle Count</th>
                  <th className="py-2 text-right">% of Fleet</th>
                </tr>
              </thead>
              <tbody>
                {ageDist.rows.map((r, i) => (
                  <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                    <td className="py-2 pr-4 font-medium text-slate-700">{r.model}</td>
                    <td className="py-2 pr-4 text-slate-600">{r.age_cohort}</td>
                    <td className="py-2 pr-4 text-right font-semibold text-brand-blue">{r.vehicle_count.toLocaleString()}</td>
                    <td className="py-2 text-right">
                      <span className="inline-flex items-center gap-1.5">
                        <div className="w-16 bg-slate-100 rounded-full h-1.5">
                          <div className="h-1.5 rounded-full bg-amber-500" style={{ width: `${Math.min(r.pct_of_fleet, 100)}%` }}/>
                        </div>
                        <span className="text-xs text-slate-600 font-medium">{r.pct_of_fleet.toFixed(1)}%</span>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
