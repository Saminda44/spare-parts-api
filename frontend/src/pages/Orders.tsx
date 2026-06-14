import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { Download } from "lucide-react";
import { fetchPolicy, fetchSanity, type PolicyRow, type SanityRow } from "../api/client";
import { KpiCard } from "../components/KpiCard";

const URGENCY_COLOR: Record<string, string> = { immediate: "#EF4444", soon: "#FFC107", planned: "#4361EE", none: "#94A3B8" };
const TIER_COLOR: Record<string, string>    = { critical: "#EF4444", managed: "#FFC107", watch: "#4361EE", rationalise: "#94A3B8" };
const SS_COLOR: Record<string, string>      = { "ML-Quantile": "#7C3AED", "Classical": "#94A3B8" };

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "orders" | "sanity";

export function Orders() {
  const [searchParams] = useSearchParams();
  const [data, setData]     = useState<{ total: number; rows: PolicyRow[]; urgency_counts: Record<string, number>; tier_counts: Record<string, number>; ss_method_counts: Record<string, number> } | null>(null);
  const [sanity, setSanity] = useState<SanityRow[]>([]);
  const [urgency, setUrgency] = useState(searchParams.get("urgency") ?? "");
  const [tier, setTier]       = useState("");
  const [ssMethod, setSsMethod] = useState("");
  const [search, setSearch]   = useState("");
  const [tab, setTab]         = useState<Tab>(searchParams.get("flagged") === "true" ? "sanity" : "orders");

  useEffect(() => {
    fetchPolicy({ limit: 500 }).then(setData);
    fetchSanity(200).then(setSanity);
  }, []);
  useEffect(() => {
    fetchPolicy({ urgency: urgency || undefined, tier: tier || undefined, ss_method: ssMethod || undefined, limit: 500 }).then(setData);
  }, [urgency, tier, ssMethod]);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const urgBar  = Object.entries(data.urgency_counts).filter(([k]) => k !== "none").map(([k, v]) => ({ name: k, value: v }));
  const tierBar = Object.entries(data.tier_counts).map(([k, v]) => ({ name: k, value: v }));
  const ssBar   = Object.entries(data.ss_method_counts).map(([k, v]) => ({ name: k, value: v }));

  const filtered = data.rows.filter(r =>
    !search || r.material_9.toLowerCase().includes(search.toLowerCase()) || r.description.toLowerCase().includes(search.toLowerCase())
  );
  const totalOrderVal = filtered.reduce((s, r) => s + r.net_requirement * r.unit_value_lkr, 0);

  const TABS = [
    { key: "orders" as Tab, label: `Order Plan (${data.total.toLocaleString()})` },
    { key: "sanity" as Tab, label: `Sanity Review (${sanity.length})` },
  ];

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Order Recommendation Plan</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Filtered SKUs"  value={fmt(filtered.length)}                       color="blue"/>
        <KpiCard label="Immediate"      value={fmt(data.urgency_counts.immediate ?? 0)}    color="red"/>
        <KpiCard label="Soon"           value={fmt(data.urgency_counts.soon ?? 0)}         color="amber"/>
        <KpiCard label="Order Value"    value={`LKR ${fmt(totalOrderVal)}`}                color="purple"/>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Order Urgency</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={urgBar} layout="vertical" margin={{ top: 0, right: 30, left: 10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
              <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {urgBar.map(d => <Cell key={d.name} fill={URGENCY_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Policy Tier</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={tierBar} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="name" tick={{ fontSize: 11 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {tierBar.map(d => <Cell key={d.name} fill={TIER_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Safety Stock Method</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={ssBar} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="name" tick={{ fontSize: 11 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {ssBar.map(d => <Cell key={d.name} fill={SS_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5">
        {/* Tab switcher */}
        <div className="flex gap-1 mb-4 border-b border-slate-100 pb-2 items-center">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${tab === t.key ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}
            >
              {t.label}
            </button>
          ))}
          <div className="flex-1"/>
          <a
            href={`/api/v1/policy/export.xlsx${urgency ? `?urgency=${urgency}` : ""}`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-green-600 text-white hover:bg-green-700 transition-colors font-medium"
            download
          >
            <Download size={13}/> Export Excel
          </a>
        </div>

        {tab === "orders" && (
          <>
            <div className="flex gap-3 mb-4 flex-wrap">
              <input
                className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[180px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                placeholder="Search SKU or description…" value={search} onChange={e => setSearch(e.target.value)}
              />
              <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={urgency} onChange={e => setUrgency(e.target.value)}>
                <option value="">All urgencies</option>
                {Object.keys(data.urgency_counts).map(u => <option key={u} value={u}>{u}</option>)}
              </select>
              <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={tier} onChange={e => setTier(e.target.value)}>
                <option value="">All tiers</option>
                {Object.keys(data.tier_counts).map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={ssMethod} onChange={e => setSsMethod(e.target.value)}>
                <option value="">All SS methods</option>
                {Object.keys(data.ss_method_counts).map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-2">SKU</th>
                    <th className="py-2 pr-2">Description</th>
                    <th className="py-2 pr-2">Tier</th>
                    <th className="py-2 pr-2">SS Method</th>
                    <th className="py-2 pr-2 text-right">SL%</th>
                    <th className="py-2 pr-2 text-right">z</th>
                    <th className="py-2 pr-2 text-right">SS</th>
                    <th className="py-2 pr-2 text-right">ROL</th>
                    <th className="py-2 pr-2 text-right">ROQ</th>
                    <th className="py-2 pr-2 text-right">Net Req</th>
                    <th className="py-2 pr-2 text-right">CV</th>
                    <th className="py-2">Urgency</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, 100).map(r => (
                    <tr key={r.material_9} className={`border-b border-slate-50 hover:bg-slate-50/50 ${r.sanity_flag ? "bg-amber-50/50" : ""}`}>
                      <td className="py-2 pr-2 font-mono text-xs text-slate-700">
                        {r.material_9}
                        {r.sanity_flag && <span className="ml-1 text-amber-500 text-xs" title={r.sanity_note}>⚠</span>}
                      </td>
                      <td className="py-2 pr-2 text-slate-600 max-w-[140px] truncate" title={r.description}>{r.description}</td>
                      <td className="py-2 pr-2">
                        <span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span>
                      </td>
                      <td className="py-2 pr-2">
                        <span className="text-xs px-1.5 py-0.5 rounded font-medium" style={{ background: (SS_COLOR[r.ss_method] ?? "#94A3B8") + "22", color: SS_COLOR[r.ss_method] ?? "#64748B" }}>{r.ss_method}</span>
                      </td>
                      <td className="py-2 pr-2 text-right text-xs">{(r.service_level * 100).toFixed(1)}%</td>
                      <td className="py-2 pr-2 text-right text-xs text-slate-400">{r.z_score.toFixed(2)}</td>
                      <td className="py-2 pr-2 text-right">{r.safety_stock.toFixed(0)}</td>
                      <td className="py-2 pr-2 text-right">{r.rol.toFixed(0)}</td>
                      <td className="py-2 pr-2 text-right">{r.roq.toFixed(0)}</td>
                      <td className="py-2 pr-2 text-right font-semibold text-slate-800">{r.net_requirement.toFixed(0)}</td>
                      <td className="py-2 pr-2 text-right text-xs text-slate-400">{r.cv.toFixed(2)}</td>
                      <td className="py-2">
                        <span className="text-xs px-2 py-0.5 rounded-full font-medium text-white" style={{ background: URGENCY_COLOR[r.order_urgency] ?? "#94A3B8" }}>{r.order_urgency}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {filtered.length.toLocaleString()}</p>}
            </div>
          </>
        )}

        {tab === "sanity" && (
          <div className="overflow-x-auto">
            <p className="text-xs text-slate-500 mb-3">
              {sanity.length} SKUs where ROL or ROQ is &gt;3× recent demand. These require manual review before ordering.
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                  <th className="py-2 pr-3">SKU</th>
                  <th className="py-2 pr-3">Description</th>
                  <th className="py-2 pr-3">ABC</th>
                  <th className="py-2 pr-3">Tier</th>
                  <th className="py-2 pr-3 text-right">Avg Demand</th>
                  <th className="py-2 pr-3 text-right">ROL</th>
                  <th className="py-2 pr-3 text-right">ROQ</th>
                  <th className="py-2 pr-3 text-right">Net Req</th>
                  <th className="py-2 pr-3">Urgency</th>
                  <th className="py-2">Sanity Note</th>
                </tr>
              </thead>
              <tbody>
                {sanity.map(r => (
                  <tr key={r.material_9} className="border-b border-slate-50 hover:bg-amber-50/30">
                    <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                    <td className="py-2 pr-3 text-slate-600 max-w-[150px] truncate" title={r.description}>{r.description}</td>
                    <td className="py-2 pr-3">
                      <span className="text-xs px-2 py-0.5 rounded-full font-bold text-white" style={{ background: r.abc === "A" ? "#EF4444" : r.abc === "B" ? "#FFC107" : "#2CC56F" }}>{r.abc}</span>
                    </td>
                    <td className="py-2 pr-3">
                      <span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: { critical:"#EF4444", managed:"#FFC107", watch:"#4361EE", rationalise:"#94A3B8" }[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span>
                    </td>
                    <td className="py-2 pr-3 text-right">{r.avg_monthly_demand.toFixed(1)}</td>
                    <td className="py-2 pr-3 text-right">{r.rol.toFixed(0)}</td>
                    <td className="py-2 pr-3 text-right">{r.roq.toFixed(0)}</td>
                    <td className="py-2 pr-3 text-right font-semibold">{r.net_requirement.toFixed(0)}</td>
                    <td className="py-2 pr-3">
                      <span className="text-xs px-2 py-0.5 rounded-full font-medium text-white" style={{ background: URGENCY_COLOR[r.order_urgency] ?? "#94A3B8" }}>{r.order_urgency}</span>
                    </td>
                    <td className="py-2 text-xs text-amber-700 max-w-[200px]">{r.sanity_note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
