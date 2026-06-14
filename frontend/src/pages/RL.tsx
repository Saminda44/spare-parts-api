import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ScatterChart, Scatter, ReferenceLine, Cell,
} from "recharts";
import { fetchRL, type RLRow, type RLSummary } from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TrendingDown } from "lucide-react";

const TIER_COLOR: Record<string, string> = { critical: "#EF4444", managed: "#FFC107", watch: "#4361EE", rationalise: "#94A3B8" };
const ABC_COLOR: Record<string, string>  = { A: "#EF4444", B: "#FFC107", C: "#2CC56F" };

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export function RL() {
  const [summary, setSummary] = useState<RLSummary | null>(null);
  const [rows, setRows]       = useState<RLRow[]>([]);
  const [search, setSearch]   = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [tier, setTier]       = useState("");

  useEffect(() => {
    fetchRL({ limit: 1000 }).then(d => { setSummary(d.summary); setRows(d.rows); });
  }, []);
  useEffect(() => {
    fetchRL({ flagged: flaggedOnly || undefined, tier: tier || undefined, limit: 1000 })
      .then(d => { setSummary(d.summary); setRows(d.rows); });
  }, [flaggedOnly, tier]);

  if (!summary) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  // Multiplier histogram buckets
  const multBuckets: Record<string, number> = {};
  rows.forEach(r => {
    const bucket = Math.round(r.rl_multiplier * 10) / 10;
    const key = bucket.toFixed(1);
    multBuckets[key] = (multBuckets[key] ?? 0) + 1;
  });
  const histData = Object.entries(multBuckets)
    .sort(([a], [b]) => +a - +b)
    .map(([k, v]) => ({ mult: k, count: v }));

  // Scatter (active, sampled 400)
  const scatterData = rows
    .filter(r => r.avg_monthly_demand > 0 && r.rule_based_roq > 0)
    .slice(0, 400)
    .map(r => ({ x: r.rule_based_roq, y: r.rl_recommended_qty, flag: r.rl_flag, tier: r.policy_tier }));

  const maxROQ = scatterData.length ? Math.max(...scatterData.map(d => d.x)) * 1.05 : 100;

  // Tier breakdown for RL
  const tierCounts: Record<string, number> = {};
  rows.forEach(r => { tierCounts[r.policy_tier] = (tierCounts[r.policy_tier] ?? 0) + 1; });
  const tierBar = Object.entries(tierCounts).map(([k, v]) => ({ name: k, value: v }));

  const filtered = rows.filter(r =>
    !search || r.material_9.toLowerCase().includes(search.toLowerCase()) || r.description.toLowerCase().includes(search.toLowerCase())
  );

  const totalCostSavings = rows.reduce((sum, r) => sum + (r.rule_based_roq - r.rl_recommended_qty) * r.unit_value_lkr, 0);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">RL Policy — PPO Agent</h2>
      <p className="text-xs text-slate-500 -mt-4">
        Trained on 200k timesteps × 32 parallel envs. Action space: [0.5, 1.5]× rule-based ROQ (CLAUDE.md §15 constraint).
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Scored SKUs"        value={fmt(summary.scored_skus)}               color="blue"/>
        <KpiCard label="Avg Multiplier"      value={summary.avg_multiplier.toFixed(3)+"×"}  sub="1.0 = rule-based baseline" color="purple"/>
        <KpiCard label="Order Reduction"     value={`${summary.avg_order_reduction_pct.toFixed(1)}%`} sub="avg vs rule-based" color="green"/>
        <KpiCard label="Flagged SKUs"        value={fmt(summary.flagged_skus)}              sub=">±50% deviation"    color={summary.flagged_skus > 0 ? "red" : "green"}/>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <KpiCard
          label="Est. Cost Savings (RL vs Rule)"
          value={`LKR ${totalCostSavings >= 0 ? "+" : ""}${fmt(totalCostSavings)}`}
          sub={totalCostSavings >= 0 ? "RL orders less than rule-based baseline" : "RL orders more than rule-based baseline"}
          color={totalCostSavings >= 0 ? "green" : "red"}
          icon={<TrendingDown size={18}/>}
        />
        <KpiCard
          label="SKUs: Reduce / Increase / Unchanged"
          value={`${fmt(summary.skus_reduce_order)} / ${fmt(summary.skus_increase_order)} / ${fmt(summary.skus_unchanged)}`}
          sub="mult < 1 / mult > 1 / mult = 1"
          color="teal"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Multiplier histogram */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-1">RL Order Multiplier Distribution</h3>
          <p className="text-xs text-slate-400 mb-3">Green = reduce order (mult&lt;1), red = increase (mult&gt;1), purple = neutral</p>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={histData} margin={{ top: 5, right: 10, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="mult" tick={{ fontSize: 10 }} label={{ value: "Multiplier", position: "insideBottom", offset: -10, fontSize: 11 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()} labelFormatter={l => `Mult = ${l}`}/>
              <ReferenceLine x="1.0" stroke="#94A3B8" strokeDasharray="4 2"/>
              <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                {histData.map(d => (
                  <Cell key={d.mult} fill={+d.mult < 1.0 ? "#2CC56F" : +d.mult > 1.0 ? "#EF4444" : "#7C3AED"}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Scatter: RL vs rule */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-1">RL Qty vs Rule-Based ROQ</h3>
          <p className="text-xs text-slate-400 mb-3">Points below dashed line = RL recommends fewer units</p>
          <ResponsiveContainer width="100%" height={210}>
            <ScatterChart margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis type="number" dataKey="x" name="Rule ROQ" tick={{ fontSize: 10 }}/>
              <YAxis type="number" dataKey="y" name="RL Qty"   tick={{ fontSize: 10 }}/>
              <Tooltip cursor={{ strokeDasharray: "3 3" }} formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: maxROQ, y: maxROQ }]} stroke="#94A3B8" strokeDasharray="4 2" label={{ value: "Break-even", fontSize: 9, fill: "#94A3B8" }}/>
              <Scatter data={scatterData}>
                {scatterData.map((d, i) => (
                  <Cell key={i} fill={d.flag ? "#EF4444" : d.y < d.x ? "#2CC56F" : d.y > d.x ? "#F97316" : "#94A3B8"} opacity={0.65}/>
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Policy tier breakdown */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Scored SKUs by Policy Tier</h3>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={tierBar} margin={{ top: 0, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
            <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
            <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
            <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {tierBar.map(d => <Cell key={d.name} fill={TIER_COLOR[d.name] ?? "#94A3B8"}/>)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex gap-3 mb-4 flex-wrap items-center">
          <input
            className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[180px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
            placeholder="Search SKU or description…" value={search} onChange={e => setSearch(e.target.value)}
          />
          <select className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none" value={tier} onChange={e => setTier(e.target.value)}>
            <option value="">All tiers</option>
            {Object.keys(tierCounts).map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
            <input type="checkbox" checked={flaggedOnly} onChange={e => setFlaggedOnly(e.target.checked)} className="rounded"/>
            Flagged only
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                <th className="py-2 pr-3">SKU</th>
                <th className="py-2 pr-3">Description</th>
                <th className="py-2 pr-3">ABC</th>
                <th className="py-2 pr-3">XYZ</th>
                <th className="py-2 pr-3">Tier</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3 text-right">Rule ROQ</th>
                <th className="py-2 pr-3 text-right">Multiplier</th>
                <th className="py-2 pr-3 text-right">RL Qty</th>
                <th className="py-2 pr-3 text-right">Coverage (mo)</th>
                <th className="py-2 pr-3 text-right">Unit Val (LKR)</th>
                <th className="py-2 pr-3 text-right">Cost Δ (LKR)</th>
                <th className="py-2">Flagged</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map(r => (
                <tr key={r.material_9} className="border-b border-slate-50 hover:bg-slate-50/50">
                  <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                  <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                  <td className="py-2 pr-3">
                    <span className="text-xs px-2 py-0.5 rounded-full font-bold text-white" style={{ background: ABC_COLOR[r.abc] ?? "#94A3B8" }}>{r.abc}</span>
                  </td>
                  <td className="py-2 pr-3 text-xs text-slate-400">{r.xyz}</td>
                  <td className="py-2 pr-3">
                    <span className="text-xs px-1.5 py-0.5 rounded font-medium text-white" style={{ background: TIER_COLOR[r.policy_tier] ?? "#94A3B8" }}>{r.policy_tier}</span>
                  </td>
                  <td className="py-2 pr-3 text-xs text-slate-400">{r.stock_status}</td>
                  <td className="py-2 pr-3 text-right text-slate-500">{r.rule_based_roq.toFixed(0)}</td>
                  <td className="py-2 pr-3 text-right font-semibold" style={{ color: r.rl_multiplier < 1.0 ? "#2CC56F" : r.rl_multiplier > 1.0 ? "#EF4444" : "#94A3B8" }}>
                    {r.rl_multiplier.toFixed(3)}×
                  </td>
                  <td className="py-2 pr-3 text-right font-bold text-slate-800">{r.rl_recommended_qty.toFixed(0)}</td>
                  <td className="py-2 pr-3 text-right text-xs">{r.coverage_months > 900 ? "∞" : r.coverage_months.toFixed(1)}</td>
                  <td className="py-2 pr-3 text-right text-xs text-slate-400">{fmt(r.unit_value_lkr)}</td>
                  <td className="py-2 pr-3 text-right text-xs font-semibold" style={{ color: (r.rule_based_roq - r.rl_recommended_qty) * r.unit_value_lkr >= 0 ? "#2CC56F" : "#EF4444" }}>
                    {((r.rule_based_roq - r.rl_recommended_qty) * r.unit_value_lkr >= 0 ? "+" : "") + fmt((r.rule_based_roq - r.rl_recommended_qty) * r.unit_value_lkr)}
                  </td>
                  <td className="py-2">
                    {r.rl_flag
                      ? <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">⚠ flagged</span>
                      : <span className="text-xs text-slate-300">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {filtered.length.toLocaleString()}</p>}
        </div>
      </div>
    </div>
  );
}
