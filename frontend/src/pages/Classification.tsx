import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, Legend,
} from "recharts";
import {
  fetchClassification, type ClassificationRow,
  fetchInventory, fetchCoverageHistogram, fetchAtRisk, fetchExcess,
  type InventoryRow, type AtRiskRow, type ExcessRow,
  fetchMovements, type MovementsData,
  fetchSparePartsEda, type SparePartsEdaData,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TimePicker, filterByRange, type TimeRange, YearPicker, filterByYear, getYears, monthLabel } from "../components/TimePicker";

// ── Colour maps ───────────────────────────────────────────────────────────────
const CLASS_COLOR: Record<string, string> = { issue: "#EF4444", receipt: "#2CC56F", transfer: "#4361EE" };
const CAT_COLORS  = ["#EF4444","#F97316","#FFC107","#4361EE","#2CC56F","#7C3AED","#94A3B8"];
const ABC_COLOR:    Record<string, string> = { A: "#EF4444", B: "#FFC107", C: "#2CC56F" };
const XYZ_COLOR:    Record<string, string> = { X: "#4361EE", Y: "#06B6D4", Z: "#94A3B8" };
const TIER_COLOR:   Record<string, string> = { critical: "#EF4444", managed: "#FFC107", watch: "#4361EE", rationalise: "#94A3B8" };
const STATUS_COLOR: Record<string, string> = { stockout: "#EF4444", critical: "#F97316", low: "#FFC107", ok: "#2CC56F", excess: "#4361EE" };
const URGENCY_COLOR:Record<string, string> = { immediate: "#EF4444", soon: "#FFC107", planned: "#4361EE", none: "#94A3B8" };
const SEG_COLORS = ["#EF4444","#F97316","#FFC107","#4361EE","#2CC56F","#7C3AED","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type MainTab = "classification" | "inventory" | "movements" | "spare";
type InvTab  = "all" | "at-risk" | "excess";

export function Classification() {
  const [searchParams] = useSearchParams();

  // ── Top-level tab ──────────────────────────────────────────────────────────
  const [mainTab, setMainTab] = useState<MainTab>(
    searchParams.get("tab") === "inventory" ? "inventory" : "classification"
  );

  // ── Classification state ───────────────────────────────────────────────────
  const [clsData, setClsData] = useState<{
    total: number; rows: ClassificationRow[];
    abc_counts: Record<string, number>; xyz_counts: Record<string, number>;
    fsn_counts: Record<string, number>; segment_counts: Record<string, number>;
    demand_category_counts: Record<string, number>; tier_counts: Record<string, number>;
  } | null>(null);
  const [abc,    setAbc]    = useState("");
  const [xyz,    setXyz]    = useState("");
  const [tier,   setTier]   = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetchClassification({ limit: 500 }).then(setClsData);
  }, []);
  useEffect(() => {
    fetchClassification({ abc: abc || undefined, xyz: xyz || undefined, tier: tier || undefined, limit: 500 }).then(setClsData);
  }, [abc, xyz, tier]);

  // ── Inventory state ────────────────────────────────────────────────────────
  const [invData,  setInvData]  = useState<{ total: number; rows: InventoryRow[]; status_counts: Record<string, number>; total_value_lkr: number; excess_value_lkr: number } | null>(null);
  const [hist,     setHist]     = useState<{ bin_start: number; bin_end: number; count: number }[]>([]);
  const [atRisk,   setAtRisk]   = useState<AtRiskRow[]>([]);
  const [excess,   setExcess]   = useState<ExcessRow[]>([]);
  const [invStatus, setInvStatus] = useState(searchParams.get("status") ?? "");
  const [invSearch, setInvSearch] = useState("");
  const [invTab,    setInvTab]  = useState<InvTab>(
    searchParams.get("status") === "excess" ? "excess" : searchParams.get("status") === "stockout" ? "at-risk" : "all"
  );

  useEffect(() => {
    fetchInventory({ limit: 500 }).then(setInvData);
    fetchCoverageHistogram().then(setHist);
    fetchAtRisk(50).then(setAtRisk);
    fetchExcess(100).then(setExcess);
  }, []);
  useEffect(() => {
    fetchInventory({ status: invStatus || undefined, limit: 500 }).then(setInvData);
  }, [invStatus]);

  // ── Movements state (Stage 7) ──────────────────────────────────────────────
  const [movData, setMovData] = useState<MovementsData | null>(null);
  const [movYear, setMovYear] = useState<number | "All">("All");
  const [movRange, setMovRange] = useState<TimeRange>("YTD");

  useEffect(() => { fetchMovements().then(setMovData); }, []);

  // ── Spare Parts EDA state (Stage 8) ───────────────────────────────────────
  const [spareData, setSpareData] = useState<SparePartsEdaData | null>(null);

  useEffect(() => { fetchSparePartsEda().then(setSpareData); }, []);

  // ── Derived ────────────────────────────────────────────────────────────────
  const clsFiltered = clsData?.rows.filter(r =>
    !search ||
    r.material_9.toLowerCase().includes(search.toLowerCase()) ||
    r.description.toLowerCase().includes(search.toLowerCase())
  ) ?? [];

  const invFiltered = invData?.rows.filter(r =>
    !invSearch ||
    r.material_9.toLowerCase().includes(invSearch.toLowerCase()) ||
    r.description.toLowerCase().includes(invSearch.toLowerCase())
  ) ?? [];

  const INV_TABS: { key: InvTab; label: string }[] = [
    { key: "all",     label: `All (${invData?.total.toLocaleString() ?? "…"})` },
    { key: "at-risk", label: `At Risk (${atRisk.length})` },
    { key: "excess",  label: `Excess (${excess.length})` },
  ];

  return (
    <div className="flex-1 p-6 space-y-5 overflow-y-auto">

      {/* ── Top-level tab bar ── */}
      <div className="flex gap-1 border-b border-slate-200 pb-0">
        {([
          { key: "classification" as MainTab, label: "SKU Classification"  },
          { key: "inventory"      as MainTab, label: "Inventory Status"     },
          { key: "movements"      as MainTab, label: "Stock Movements"      },
          { key: "spare"          as MainTab, label: "Spare Parts EDA"      },
        ]).map(t => (
          <button key={t.key} onClick={() => setMainTab(t.key)}
            className={`px-5 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
              mainTab === t.key
                ? "border-brand-blue text-brand-blue bg-white"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-50"
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════
          TAB 1 — SKU Classification
      ══════════════════════════════════════════════════════════ */}
      {mainTab === "classification" && (
        clsData ? (
          <div className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Total SKUs" value={fmt(clsData.total)}                  color="blue"/>
              <KpiCard label="A-Class"    value={fmt(clsData.abc_counts.A ?? 0)}      sub="High value · tight control" color="red"/>
              <KpiCard label="B-Class"    value={fmt(clsData.abc_counts.B ?? 0)}      sub="Medium value"               color="amber"/>
              <KpiCard label="C-Class"    value={fmt(clsData.abc_counts.C ?? 0)}      sub="Low value · bulk order"     color="green"/>
            </div>

            {/* Charts row 1 */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">ABC (Value)</h3>
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={Object.entries(clsData.abc_counts).map(([k,v]) => ({ name: k, value: v }))} margin={{ top:5, right:5, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[4,4,0,0]}>
                      {Object.keys(clsData.abc_counts).map(k => <Cell key={k} fill={ABC_COLOR[k] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">XYZ (Demand Variability)</h3>
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={Object.entries(clsData.xyz_counts).map(([k,v]) => ({ name: k, value: v }))} margin={{ top:5, right:5, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[4,4,0,0]}>
                      {Object.keys(clsData.xyz_counts).map(k => <Cell key={k} fill={XYZ_COLOR[k] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Demand Category</h3>
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={Object.entries(clsData.demand_category_counts).map(([k,v]) => ({ name: k, value: v }))} layout="vertical" margin={{ top:0, right:30, left:10, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={80}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" fill="#4361EE" radius={[0,3,3,0]}/>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* ABC × FSN matrix */}
            {(() => {
              const matrix: Record<string, Record<string, number>> = { A: { F:0, S:0, N:0 }, B: { F:0, S:0, N:0 }, C: { F:0, S:0, N:0 } };
              clsData.rows.forEach(r => { if (matrix[r.abc] && r.fsn) matrix[r.abc][r.fsn] = (matrix[r.abc][r.fsn] ?? 0) + 1; });
              const maxCell = Math.max(1, ...Object.values(matrix).flatMap(row => Object.values(row)));
              const cellBg = (count: number) => {
                const intensity = Math.round((count / maxCell) * 200);
                return `rgb(${255 - Math.round(intensity * 0.5)}, ${255 - intensity}, ${255 - Math.round(intensity * 0.8)})`;
              };
              return (
                <div className="bg-white rounded-xl shadow-sm p-5">
                  <h3 className="text-sm font-semibold text-slate-700 mb-1">ABC × FSN Matrix</h3>
                  <p className="text-xs text-slate-400 mb-4">Darker = more SKUs in that segment.</p>
                  <div className="overflow-x-auto">
                    <table className="mx-auto border-collapse">
                      <thead>
                        <tr>
                          <th className="w-16 text-xs text-slate-400 pb-2 pr-3 text-right">ABC ╲ FSN</th>
                          {["F — Fast","S — Slow","N — Non"].map(h => (
                            <th key={h} className="w-36 text-center text-xs font-semibold text-slate-600 pb-2 px-2">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(["A","B","C"] as const).map(a => (
                          <tr key={a}>
                            <td className="pr-3 py-2 text-right">
                              <span className="text-sm font-bold px-2 py-1 rounded text-white" style={{ background: ABC_COLOR[a] }}>{a}</span>
                            </td>
                            {(["F","S","N"] as const).map(f => {
                              const count = matrix[a][f] ?? 0;
                              return (
                                <td key={f} className="px-2 py-2 text-center">
                                  <div className="rounded-lg p-4 min-w-[120px]" style={{ background: cellBg(count) }}>
                                    <p className="text-xl font-bold text-slate-800">{count.toLocaleString()}</p>
                                    <p className="text-[10px] text-slate-500 mt-0.5">{a}-{f}</p>
                                  </div>
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

            {/* Charts row 2 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">ML Demand Segments (K-Means)</h3>
                <ResponsiveContainer width="100%" height={190}>
                  <BarChart data={Object.entries(clsData.segment_counts).map(([k,v],i) => ({ name: k, value: v, fill: SEG_COLORS[i % SEG_COLORS.length] }))} layout="vertical" margin={{ top:0, right:40, left:5, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={130}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[0,3,3,0]}>
                      {Object.entries(clsData.segment_counts).map(([k],i) => <Cell key={k} fill={SEG_COLORS[i % SEG_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Policy Tier</h3>
                <ResponsiveContainer width="100%" height={190}>
                  <BarChart data={Object.entries(clsData.tier_counts).map(([k,v]) => ({ name: k, value: v }))} margin={{ top:5, right:10, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="name" tick={{ fontSize: 11 }}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[4,4,0,0]}>
                      {Object.keys(clsData.tier_counts).map(k => <Cell key={k} fill={TIER_COLOR[k] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Table */}
            <div className="bg-white rounded-xl shadow-sm p-5">
              <div className="flex gap-3 mb-4 flex-wrap">
                <input
                  className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[180px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                  placeholder="Search SKU or description…" value={search} onChange={e => setSearch(e.target.value)}
                />
                <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={abc} onChange={e => setAbc(e.target.value)}>
                  <option value="">All ABC</option>
                  <option value="A">A — High value</option>
                  <option value="B">B — Medium value</option>
                  <option value="C">C — Low value</option>
                </select>
                <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={xyz} onChange={e => setXyz(e.target.value)}>
                  <option value="">All XYZ</option>
                  <option value="X">X — Stable</option>
                  <option value="Y">Y — Variable</option>
                  <option value="Z">Z — Irregular</option>
                </select>
                <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={tier} onChange={e => setTier(e.target.value)}>
                  <option value="">All Tiers</option>
                  {Object.keys(clsData.tier_counts).map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-3">SKU</th>
                      <th className="py-2 pr-3">Description</th>
                      <th className="py-2 pr-3">ABC</th>
                      <th className="py-2 pr-3">XYZ</th>
                      <th className="py-2 pr-3">FSN</th>
                      <th className="py-2 pr-3">Tier</th>
                      <th className="py-2 pr-3">Category</th>
                      <th className="py-2 pr-3">Segment</th>
                      <th className="py-2 pr-3 text-right">Avg Demand</th>
                      <th className="py-2 pr-3 text-right">CV</th>
                      <th className="py-2 pr-3 text-right">p_zero</th>
                      <th className="py-2 text-right">Issue Value (LKR)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clsFiltered.slice(0, 100).map(r => (
                      <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                        <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                        <td className="py-2 pr-3">
                          <span className="text-xs px-2 py-0.5 rounded-full font-bold text-white" style={{ background: ABC_COLOR[r.abc] ?? "#94A3B8" }}>{r.abc}</span>
                        </td>
                        <td className="py-2 pr-3">
                          <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ background: (XYZ_COLOR[r.xyz] ?? "#94A3B8") + "22", color: XYZ_COLOR[r.xyz] ?? "#64748B" }}>{r.xyz}</span>
                        </td>
                        <td className="py-2 pr-3 text-slate-500 text-xs">{r.fsn}</td>
                        <td className="py-2 pr-3">
                          <span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span>
                        </td>
                        <td className="py-2 pr-3 text-slate-500 text-xs">{r.demand_category ?? "—"}</td>
                        <td className="py-2 pr-3 text-slate-500 text-xs max-w-[120px] truncate" title={r.demand_segment ?? ""}>{r.demand_segment ?? "—"}</td>
                        <td className="py-2 pr-3 text-right">{r.avg_monthly_demand.toFixed(1)}</td>
                        <td className="py-2 pr-3 text-right">{r.cv.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-right">{(r.p_zero * 100).toFixed(0)}%</td>
                        <td className="py-2 text-right">{fmt(r.total_issue_value_lkr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {clsFiltered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {clsFiltered.length.toLocaleString()}</p>}
              </div>
            </div>
          </div>
        ) : <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>
      )}

      {/* ══════════════════════════════════════════════════════════
          TAB 2 — Inventory Status
      ══════════════════════════════════════════════════════════ */}
      {mainTab === "inventory" && (
        invData ? (
          <div className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Total SKUs"   value={fmt(invData.total)}                          color="blue"/>
              <KpiCard label="Stock Value"  value={`LKR ${fmt(invData.total_value_lkr)}`}       color="green"/>
              <KpiCard label="Stockout"     value={fmt(invData.status_counts.stockout ?? 0)}    sub="Need immediate order" color="red"/>
              <KpiCard label="Excess Value" value={`LKR ${fmt(invData.excess_value_lkr)}`}      sub=">6 months coverage"   color="amber"/>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Status Breakdown</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={Object.entries(invData.status_counts).map(([k,v]) => ({ name: k, value: v }))} margin={{ top:5, right:10, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[4,4,0,0]}>
                      {Object.keys(invData.status_counts).map(k => <Cell key={k} fill={STATUS_COLOR[k] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Stock Coverage Distribution (months)</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={hist.map(b => ({ name: b.bin_start.toFixed(1), count: b.count }))} margin={{ top:5, right:10, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={7}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()} labelFormatter={l => `${l} months`}/>
                    <Bar dataKey="count" fill="#4361EE" radius={[2,2,0,0]}/>
                  </BarChart>
                </ResponsiveContainer>
                <div className="flex gap-4 mt-1 text-xs text-slate-400">
                  <span>— 3 mo = lead time</span>
                  <span>— 6 mo = excess threshold</span>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-xl shadow-sm p-5">
              <div className="flex gap-1 mb-4 border-b border-slate-100 pb-2">
                {INV_TABS.map(t => (
                  <button key={t.key} onClick={() => setInvTab(t.key)}
                    className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${invTab === t.key ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}>
                    {t.label}
                  </button>
                ))}
              </div>

              {invTab === "all" && (
                <>
                  <div className="flex gap-3 mb-4 flex-wrap">
                    <input
                      className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[180px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                      placeholder="Search SKU or description…" value={invSearch} onChange={e => setInvSearch(e.target.value)}
                    />
                    <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={invStatus} onChange={e => setInvStatus(e.target.value)}>
                      <option value="">All statuses</option>
                      {Object.keys(invData.status_counts).map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                          <th className="py-2 pr-3">SKU</th><th className="py-2 pr-3">Description</th>
                          <th className="py-2 pr-3">Tier</th><th className="py-2 pr-3">Method</th>
                          <th className="py-2 pr-3 text-right">Stock</th><th className="py-2 pr-3 text-right">Value (LKR)</th>
                          <th className="py-2 pr-3 text-right">Coverage (mo)</th><th className="py-2 pr-3 text-right">Post-Order Cov.</th>
                          <th className="py-2 pr-3 text-right">Receipts</th><th className="py-2 pr-3 text-right">Issues</th>
                          <th className="py-2">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {invFiltered.slice(0, 100).map((r: InventoryRow) => (
                          <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                            <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                            <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                            <td className="py-2 pr-3"><span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span></td>
                            <td className="py-2 pr-3 text-xs text-slate-400">{r.method}</td>
                            <td className="py-2 pr-3 text-right">{r.stock_on_hand.toFixed(0)}</td>
                            <td className="py-2 pr-3 text-right">{fmt(r.stock_value_lkr)}</td>
                            <td className="py-2 pr-3 text-right">{r.coverage_months === 999 ? "∞" : r.coverage_months.toFixed(1)}</td>
                            <td className="py-2 pr-3 text-right font-semibold" style={{ color: (() => { if (r.avg_monthly_demand <= 0) return "#94A3B8"; const c = (r.stock_on_hand + r.forecast_lt) / r.avg_monthly_demand; return c < 3 ? "#EF4444" : c < 6 ? "#F97316" : "#2CC56F"; })() }}>
                              {r.avg_monthly_demand <= 0 ? "—" : (() => { const c = (r.stock_on_hand + r.forecast_lt) / r.avg_monthly_demand; return c > 900 ? "∞" : c.toFixed(1); })()}
                            </td>
                            <td className="py-2 pr-3 text-right">{r.total_receipts.toFixed(0)}</td>
                            <td className="py-2 pr-3 text-right">{r.total_issues.toFixed(0)}</td>
                            <td className="py-2"><span className="text-xs px-2 py-0.5 rounded-full font-medium text-white" style={{ background: STATUS_COLOR[r.stock_status] ?? "#94A3B8" }}>{r.stock_status}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {invFiltered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {invFiltered.length.toLocaleString()}</p>}
                  </div>
                </>
              )}

              {invTab === "at-risk" && (
                <div className="overflow-x-auto">
                  <p className="text-xs text-slate-500 mb-3">Top {atRisk.length} SKUs with immediate or soon order urgency, sorted by net requirement descending.</p>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">SKU</th><th className="py-2 pr-3">Description</th>
                        <th className="py-2 pr-3">ABC</th><th className="py-2 pr-3">Tier</th>
                        <th className="py-2 pr-3 text-right">Stock</th><th className="py-2 pr-3">Status</th>
                        <th className="py-2 pr-3 text-right">Coverage (mo)</th><th className="py-2 pr-3 text-right">Net Req</th>
                        <th className="py-2 pr-3 text-right">Unit Value (LKR)</th><th className="py-2">Urgency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {atRisk.map((r: AtRiskRow) => (
                        <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                          <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                          <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 rounded-full font-bold text-white" style={{ background: r.abc === "A" ? "#EF4444" : r.abc === "B" ? "#FFC107" : "#2CC56F" }}>{r.abc}</span></td>
                          <td className="py-2 pr-3"><span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span></td>
                          <td className="py-2 pr-3 text-right">{r.stock_on_hand.toFixed(0)}</td>
                          <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 rounded-full font-medium text-white" style={{ background: STATUS_COLOR[r.stock_status] ?? "#94A3B8" }}>{r.stock_status}</span></td>
                          <td className="py-2 pr-3 text-right">{r.coverage_months.toFixed(1)}</td>
                          <td className="py-2 pr-3 text-right font-bold text-red-600">{r.net_requirement.toFixed(0)}</td>
                          <td className="py-2 pr-3 text-right">{r.unit_value_lkr.toFixed(0)}</td>
                          <td className="py-2"><span className="text-xs px-2 py-0.5 rounded-full font-medium text-white" style={{ background: URGENCY_COLOR[r.order_urgency] ?? "#94A3B8" }}>{r.order_urgency}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {invTab === "excess" && (
                <div className="overflow-x-auto">
                  <p className="text-xs text-slate-500 mb-3">Top {excess.length} SKUs with excess stock (&gt;6 months coverage), sorted by stock value descending.</p>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">SKU</th><th className="py-2 pr-3">Description</th>
                        <th className="py-2 pr-3">ABC</th><th className="py-2 pr-3">Tier</th>
                        <th className="py-2 pr-3 text-right">Stock QTY</th><th className="py-2 pr-3 text-right">Value (LKR)</th>
                        <th className="py-2 pr-3 text-right">Coverage (mo)</th><th className="py-2 text-right">Avg Demand</th>
                      </tr>
                    </thead>
                    <tbody>
                      {excess.map((r: ExcessRow) => (
                        <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                          <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                          <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 rounded-full font-bold text-white" style={{ background: r.abc === "A" ? "#EF4444" : r.abc === "B" ? "#FFC107" : "#2CC56F" }}>{r.abc}</span></td>
                          <td className="py-2 pr-3"><span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span></td>
                          <td className="py-2 pr-3 text-right">{r.stock_on_hand.toFixed(0)}</td>
                          <td className="py-2 pr-3 text-right font-semibold text-amber-500">{fmt(r.stock_value_lkr)}</td>
                          <td className="py-2 pr-3 text-right">{r.coverage_months > 900 ? "∞" : r.coverage_months.toFixed(1)}</td>
                          <td className="py-2 text-right">{r.avg_monthly_demand.toFixed(1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        ) : <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>
      )}

      {/* ══════════════════════════════════════════════════════════
          TAB 3 — Stock Movements (Stage 7)
      ══════════════════════════════════════════════════════════ */}
      {mainTab === "movements" && (
        movData ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Total Records" value={fmt(movData.total_records)}          color="blue"/>
              <KpiCard label="Issues"        value={fmt(movData.by_class.issue ?? 0)}    sub="outbound to customers" color="red"/>
              <KpiCard label="Receipts"      value={fmt(movData.by_class.receipt ?? 0)}  sub="inbound from suppliers" color="green"/>
              <KpiCard label="Transfers"     value={fmt(movData.by_class.transfer ?? 0)} sub="internal movement" color="purple"/>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Movement Class Breakdown</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart
                    data={Object.entries(movData.by_class).map(([k, v]) => ({ name: k, value: v }))}
                    layout="vertical" margin={{ top:0, right:40, left:10, bottom:0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={90}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[0,3,3,0]}>
                      {Object.keys(movData.by_class).map(k => <Cell key={k} fill={CLASS_COLOR[k] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl shadow-sm p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-slate-700">Monthly Issues vs Receipts</h3>
                  <div className="flex items-center gap-2">
                    <YearPicker years={getYears(movData.monthly_trend)} value={movYear} onChange={setMovYear}/>
                    <TimePicker value={movRange} onChange={setMovRange}/>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart
                    data={filterByRange(filterByYear((() => {
                      const periods = [...new Set(movData.monthly_trend.map(r => r.period))].sort();
                      return periods.map(p => {
                        const rows = movData.monthly_trend.filter(r => r.period === p);
                        const get  = (cls: string) => rows.find(r => r.movement_class === cls)?.qty ?? 0;
                        return { period: p, issue: get("issue"), receipt: get("receipt"), transfer: get("transfer") };
                      });
                    })(), movYear), movRange)}
                    margin={{ top:5, right:10, left:0, bottom:5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                      tickFormatter={p => monthLabel(p, movYear !== "All")}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()} labelFormatter={p => String(p)}/>
                    <Legend/>
                    <Line type="monotone" dataKey="issue"    stroke="#EF4444" strokeWidth={2} dot={false} name="Issue"/>
                    <Line type="monotone" dataKey="receipt"  stroke="#2CC56F" strokeWidth={2} dot={false} name="Receipt"/>
                    <Line type="monotone" dataKey="transfer" stroke="#4361EE" strokeWidth={1.5} dot={false} name="Transfer" strokeDasharray="4 2"/>
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-white rounded-xl shadow-sm p-5 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-4">Movement Class</th>
                    <th className="py-2 text-right">Record Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(movData.by_class).sort(([,a],[,b])=>b-a).map(([cls, cnt]) => (
                    <tr key={cls} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-4 flex items-center gap-2">
                        <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: CLASS_COLOR[cls] ?? "#94A3B8" }}/>
                        {cls}
                      </td>
                      <td className="py-2 text-right font-semibold">{cnt.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>
      )}

      {/* ══════════════════════════════════════════════════════════
          TAB 4 — Spare Parts EDA (Stage 8)
      ══════════════════════════════════════════════════════════ */}
      {mainTab === "spare" && (
        spareData ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Total SKUs"        value={fmt(spareData.total_skus)}                                       color="blue"/>
              <KpiCard label="Total Issue Value" value={`LKR ${fmt(spareData.total_issue_value_lkr)}`}                  sub="cumulative parts issued" color="purple"/>
              <KpiCard label="Median p_zero"     value={`${(spareData.median_p_zero * 100).toFixed(0)}%`}               sub="months with zero demand" color="amber"/>
              <KpiCard label="Median CV"         value={spareData.median_cv.toFixed(2)}                                 sub="demand variability" color="teal"/>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="In SSOP"              value={fmt(spareData.in_ssop_count)}                                  sub="in supersession table" color="amber"/>
              <KpiCard label="Non-Moving"           value={fmt(spareData.demand_category_counts.non_moving ?? 0)}        sub="p_zero=1 across period" color="red"/>
              <KpiCard label="Intermittent / Lumpy" value={fmt(spareData.intermittent_skus.length)}                     sub="p_zero ≥ 70%, ever active" color="amber"/>
              <KpiCard label="Smooth Demand"        value={fmt(spareData.demand_category_counts.smooth ?? 0)}            sub="regular fast movers" color="green"/>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-1">Demand Category Distribution</h3>
                <p className="text-xs text-slate-400 mb-2">Based on CV and p_zero thresholds (Croston / ADIDA categorisation)</p>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart
                    data={Object.entries(spareData.demand_category_counts).map(([k, v]) => ({ name: k, value: v }))}
                    layout="vertical" margin={{ top:0, right:40, left:5, bottom:0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={100}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[0,3,3,0]}>
                      {Object.keys(spareData.demand_category_counts).map((k, i) => <Cell key={k} fill={CAT_COLORS[i % CAT_COLORS.length]}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-1">P(zero demand) Histogram</h3>
                <p className="text-xs text-slate-400 mb-2">Fraction of months with zero demand. p_zero ≈ 1 → non-moving.</p>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={spareData.p_zero_bins} margin={{ top:5, right:10, left:0, bottom:0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                    <XAxis dataKey="bin" tick={{ fontSize: 9 }}/>
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()} labelFormatter={l => `p_zero = ${l}`}/>
                    <Bar dataKey="count" radius={[3,3,0,0]}>
                      {spareData.p_zero_bins.map((b, i) => (
                        <Cell key={i} fill={b.lo >= 0.9 ? "#EF4444" : b.lo >= 0.5 ? "#FFC107" : "#2CC56F"}/>
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-white rounded-xl shadow-sm p-5">
              <h3 className="text-sm font-semibold text-slate-700 mb-1">Top 20 SKUs by Issue Value (Pareto)</h3>
              <p className="text-xs text-slate-400 mb-3">Ranked by cumulative LKR value issued.</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-2 w-8">#</th>
                      <th className="py-2 pr-3">Part No.</th><th className="py-2 pr-3">Description</th>
                      <th className="py-2 pr-3">Category</th><th className="py-2 pr-3 text-right">Issue Value (LKR)</th>
                      <th className="py-2 pr-3 text-right">Issue Qty</th><th className="py-2 text-right">Cum. Share %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {spareData.top_skus.map(r => (
                      <tr key={r.rank} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-2 text-xs text-slate-400">{r.rank}</td>
                        <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                        <td className="py-2 pr-3 text-xs text-slate-600 max-w-[200px] truncate" title={r.description}>{r.description}</td>
                        <td className="py-2 pr-3">
                          <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                            style={{
                              background: r.demand_category === "smooth" ? "#DCFCE7" : r.demand_category === "erratic" ? "#FEF9C3" : r.demand_category === "intermittent" ? "#DBEAFE" : r.demand_category === "lumpy" ? "#FEE2E2" : "#F1F5F9",
                              color: r.demand_category === "smooth" ? "#16A34A" : r.demand_category === "erratic" ? "#CA8A04" : r.demand_category === "intermittent" ? "#2563EB" : r.demand_category === "lumpy" ? "#DC2626" : "#64748B",
                            }}>
                            {r.demand_category ?? "—"}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right font-semibold text-slate-800">{fmt(r.total_issue_value_lkr)}</td>
                        <td className="py-2 pr-3 text-right text-slate-500 text-xs">{r.total_issue_qty.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                        <td className="py-2 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-16 bg-slate-100 rounded-full h-1.5">
                              <div className="bg-brand-blue h-1.5 rounded-full" style={{ width: `${Math.min(r.cumulative_share_pct, 100)}%` }}/>
                            </div>
                            <span className="text-xs font-medium text-slate-700 w-10 text-right">{r.cumulative_share_pct.toFixed(1)}%</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {spareData.intermittent_skus.length > 0 && (
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-1">
                  Intermittent &amp; Lumpy SKUs
                  <span className="ml-2 text-xs font-normal text-slate-400">(p_zero ≥ 70%, at least 1 active month)</span>
                </h3>
                <p className="text-xs text-slate-400 mb-3">These SKUs require Croston / ADIDA / SBA forecasting.</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">Part No.</th><th className="py-2 pr-3">Description</th>
                        <th className="py-2 pr-3">Category</th><th className="py-2 pr-3 text-right">p_zero</th>
                        <th className="py-2 pr-3 text-right">CV</th><th className="py-2 pr-3 text-right">Active Mo.</th>
                        <th className="py-2 pr-3 text-right">Avg/Mo</th><th className="py-2 text-right">Issue Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {spareData.intermittent_skus.slice(0, 100).map((r, i) => (
                        <tr key={i} className="border-b border-slate-50 hover:bg-blue-50/20">
                          <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                          <td className="py-2 pr-3 text-xs text-slate-600 max-w-[200px] truncate" title={r.description}>{r.description}</td>
                          <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">{r.demand_category}</span></td>
                          <td className="py-2 pr-3 text-right text-xs" style={{ color: r.p_zero >= 0.9 ? "#DC2626" : r.p_zero >= 0.7 ? "#CA8A04" : "#16A34A" }}>
                            {(r.p_zero * 100).toFixed(0)}%
                          </td>
                          <td className="py-2 pr-3 text-right text-xs text-slate-500">{r.cv.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-right text-xs text-slate-500">{r.active_months}</td>
                          <td className="py-2 pr-3 text-right text-xs text-slate-500">{r.avg_monthly_demand.toFixed(1)}</td>
                          <td className="py-2 text-right text-xs font-medium text-slate-700">{fmt(r.total_issue_value_lkr)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {spareData.intermittent_skus.length > 100 && (
                    <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {spareData.intermittent_skus.length.toLocaleString()}</p>
                  )}
                </div>
              </div>
            )}

            <div className="bg-white rounded-xl shadow-sm p-5">
              <h3 className="text-sm font-semibold text-slate-700 mb-2">Data Ingestion Log</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-4">File</th>
                      <th className="py-2 pr-4 text-right">Rows In</th>
                      <th className="py-2 pr-4 text-right">Inserted</th>
                      <th className="py-2 text-right">Duplicates Skipped</th>
                    </tr>
                  </thead>
                  <tbody>
                    {spareData.ingestion_summary.map((r, i) => (
                      <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-4 font-mono text-xs text-slate-700">{r.filename}</td>
                        <td className="py-2 pr-4 text-right">{Number(r.rows_in).toLocaleString()}</td>
                        <td className="py-2 pr-4 text-right text-green-600 font-medium">{Number(r.inserted).toLocaleString()}</td>
                        <td className="py-2 text-right text-amber-600">{Number(r.duplicates_skipped).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>
      )}
    </div>
  );
}
