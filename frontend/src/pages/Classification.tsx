import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { fetchClassification, type ClassificationRow } from "../api/client";
import { KpiCard } from "../components/KpiCard";

const ABC_COLOR: Record<string, string>  = { A: "#EF4444", B: "#FFC107", C: "#2CC56F" };
const XYZ_COLOR: Record<string, string>  = { X: "#4361EE", Y: "#06B6D4", Z: "#94A3B8" };
const TIER_COLOR: Record<string, string> = { critical: "#EF4444", managed: "#FFC107", watch: "#4361EE", rationalise: "#94A3B8" };
const SEG_COLORS = ["#EF4444","#F97316","#FFC107","#4361EE","#2CC56F","#7C3AED","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export function Classification() {
  const [data, setData] = useState<{
    total: number; rows: ClassificationRow[];
    abc_counts: Record<string, number>; xyz_counts: Record<string, number>;
    fsn_counts: Record<string, number>; segment_counts: Record<string, number>;
    demand_category_counts: Record<string, number>; tier_counts: Record<string, number>;
  } | null>(null);
  const [abc, setAbc]   = useState("");
  const [xyz, setXyz]   = useState("");
  const [tier, setTier] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => { fetchClassification({ limit: 500 }).then(setData); }, []);
  useEffect(() => {
    fetchClassification({ abc: abc || undefined, xyz: xyz || undefined, tier: tier || undefined, limit: 500 }).then(setData);
  }, [abc, xyz, tier]);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const abcBar  = Object.entries(data.abc_counts).map(([k, v]) => ({ name: k, value: v }));
  const xyzBar  = Object.entries(data.xyz_counts).map(([k, v]) => ({ name: k, value: v }));
  const segBar  = Object.entries(data.segment_counts).map(([k, v], i) => ({ name: k, value: v, fill: SEG_COLORS[i % SEG_COLORS.length] }));
  const catBar  = Object.entries(data.demand_category_counts).map(([k, v]) => ({ name: k, value: v }));
  const tierBar = Object.entries(data.tier_counts).map(([k, v]) => ({ name: k, value: v }));

  const filtered = data.rows.filter(r =>
    !search || r.material_9.toLowerCase().includes(search.toLowerCase()) ||
    r.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">SKU Classification</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total SKUs" value={fmt(data.total)} color="blue"/>
        <KpiCard label="A-Class" value={fmt(data.abc_counts.A ?? 0)} sub="High value · tight control" color="red"/>
        <KpiCard label="B-Class" value={fmt(data.abc_counts.B ?? 0)} sub="Medium value"              color="amber"/>
        <KpiCard label="C-Class" value={fmt(data.abc_counts.C ?? 0)} sub="Low value · bulk order"   color="green"/>
      </div>

      {/* Charts row 1 — ABC / XYZ / Category */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">ABC (Value)</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={abcBar} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {abcBar.map(d => <Cell key={d.name} fill={ABC_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">XYZ (Demand Variability)</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={xyzBar} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {xyzBar.map(d => <Cell key={d.name} fill={XYZ_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Demand Category</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={catBar} layout="vertical" margin={{ top: 0, right: 30, left: 10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
              <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
              <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={80}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" fill="#4361EE" radius={[0, 3, 3, 0]}/>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ABC × FSN matrix heatmap */}
      {(() => {
        const matrix: Record<string, Record<string, number>> = { A: { F: 0, S: 0, N: 0 }, B: { F: 0, S: 0, N: 0 }, C: { F: 0, S: 0, N: 0 } };
        data.rows.forEach(r => { if (matrix[r.abc] && r.fsn) matrix[r.abc][r.fsn] = (matrix[r.abc][r.fsn] ?? 0) + 1; });
        const maxCell = Math.max(1, ...Object.values(matrix).flatMap(row => Object.values(row)));
        const cellBg = (count: number) => {
          const intensity = Math.round((count / maxCell) * 200);
          return `rgb(${255 - Math.round(intensity * 0.5)}, ${255 - intensity}, ${255 - Math.round(intensity * 0.8)})`;
        };
        return (
          <div className="bg-white rounded-xl shadow-sm p-5">
            <h3 className="text-sm font-semibold text-slate-700 mb-1">ABC × FSN Matrix</h3>
            <p className="text-xs text-slate-400 mb-4">Darker = more SKUs in that segment. Computed from fetched rows.</p>
            <div className="overflow-x-auto">
              <table className="mx-auto border-collapse">
                <thead>
                  <tr>
                    <th className="w-16 text-xs text-slate-400 pb-2 pr-3 text-right">ABC ╲ FSN</th>
                    {["F — Fast", "S — Slow", "N — Non"].map(h => (
                      <th key={h} className="w-36 text-center text-xs font-semibold text-slate-600 pb-2 px-2">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(["A", "B", "C"] as const).map(abc => (
                    <tr key={abc}>
                      <td className="pr-3 py-2 text-right">
                        <span className="text-sm font-bold px-2 py-1 rounded text-white" style={{ background: ABC_COLOR[abc] }}>{abc}</span>
                      </td>
                      {(["F", "S", "N"] as const).map(fsn => {
                        const count = matrix[abc][fsn] ?? 0;
                        return (
                          <td key={fsn} className="px-2 py-2 text-center">
                            <div
                              className="rounded-lg p-4 min-w-[120px]"
                              style={{ background: cellBg(count) }}
                            >
                              <p className="text-xl font-bold text-slate-800">{count.toLocaleString()}</p>
                              <p className="text-[10px] text-slate-500 mt-0.5">{abc}-{fsn}</p>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-slate-300 mt-3 text-center">
              A-F (high value, fast moving) = tightest control · C-N (low value, non-moving) = rationalise
            </p>
          </div>
        );
      })()}

      {/* Charts row 2 — ML segments / Policy tier */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">ML Demand Segments (K-Means)</h3>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={segBar} layout="vertical" margin={{ top: 0, right: 40, left: 5, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
              <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
              <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={130}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[0, 3, 3, 0]}>
                {segBar.map(d => <Cell key={d.name} fill={d.fill}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Policy Tier</h3>
          <ResponsiveContainer width="100%" height={190}>
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
            {Object.keys(data.tier_counts).map(t => <option key={t} value={t}>{t}</option>)}
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
              {filtered.slice(0, 100).map(r => (
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
          {filtered.length > 100 && <p className="text-xs text-slate-400 mt-2 text-center">Showing 100 of {filtered.length.toLocaleString()}</p>}
        </div>
      </div>
    </div>
  );
}
