import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, Legend } from "recharts";
import { fetchUIOComparison, type UIOComparisonData } from "../api/client";
import { KpiCard } from "../components/KpiCard";

const MODEL_COLORS = ["#4361EE","#EF4444","#2CC56F","#FFC107","#7C3AED","#06B6D4","#F97316","#94A3B8","#10B981","#EC4899"];

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export function UIO() {
  const [data, setData] = useState<UIOComparisonData | null>(null);

  useEffect(() => { fetchUIOComparison().then(setData); }, []);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const totalExternal = data.external.reduce((s, r) => s + r.uio, 0);
  const totalMcsi     = data.mcsi.reduce((s, r) => s + r.uio, 0);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Units in Operation (UIO)</h2>
      <p className="text-xs text-slate-500 -mt-4">Stage 3 · Fleet size from two sources: cohort-survival model (UIO.xlsx) and VIN-verified recent sales (MCSI.xlsx)</p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Historical Fleet (UIO.xlsx)" value={totalExternal.toLocaleString()} sub={`${data.external.length} model generations`} color="purple"/>
        <KpiCard label="MCSI UIO (VIN-verified)"    value={totalMcsi.toLocaleString()}     sub={`${data.mcsi.length} current models`}     color="blue"/>
        <KpiCard label="Top Model (Historical)"     value={data.external[0]?.model ?? "—"} sub={data.external[0] ? `${data.external[0].uio.toLocaleString()} UIO` : ""} color="teal"/>
        <KpiCard label="Top Model (MCSI)"           value={data.mcsi[0]?.model ?? "—"}     sub={data.mcsi[0] ? `${data.mcsi[0].uio.toLocaleString()} UIO` : ""}     color="green"/>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* UIO.xlsx — historical cohort model */}
          <div>
            <h3 className="text-sm font-semibold text-slate-700 mb-1">UIO by Model — Historical Fleet (UIO.xlsx)</h3>
            <p className="text-xs text-slate-400 mb-3">Cohort-survival: bikes sold per year × retention rate. All generations back to 2013. Top 20 shown.</p>
            <ResponsiveContainer width="100%" height={520}>
              <BarChart
                data={data.external.slice(0, 20).map(r => ({ name: r.model, uio: r.uio, sales: r.total_sales_units }))}
                layout="vertical" margin={{ top: 0, right: 50, left: 10, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={170}/>
                <Tooltip formatter={(v: unknown, n: string) => [Number(v).toLocaleString(), n === "uio" ? "UIO" : "Total Sales"]}/>
                <Legend/>
                <Bar dataKey="uio"   fill="#7C3AED" name="UIO"         radius={[0, 3, 3, 0]}/>
                <Bar dataKey="sales" fill="#E2D9F3" name="Total Sales" radius={[0, 3, 3, 0]}/>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* MCSI.xlsx — VIN-verified */}
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-1">UIO by Model — Recent Sales (MCSI.xlsx)</h3>
              <p className="text-xs text-slate-400 mb-3">VIN-verified sold bikes. Current models only.</p>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart
                  data={data.mcsi.map(r => ({ name: r.model, uio: r.uio }))}
                  layout="vertical" margin={{ top: 0, right: 50, left: 10, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={170}/>
                  <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "UIO"]}/>
                  <Bar dataKey="uio" radius={[0, 3, 3, 0]}>
                    {data.mcsi.map((_, i) => <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]}/>)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                    <th className="py-2 pr-4">Model</th>
                    <th className="py-2 pr-4 text-right">UIO</th>
                    <th className="py-2 text-right">Share %</th>
                  </tr>
                </thead>
                <tbody>
                  {data.mcsi.map((r, i) => (
                    <tr key={r.model} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="py-2 pr-4 flex items-center gap-2">
                        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }}/>
                        {r.model}
                      </td>
                      <td className="py-2 pr-4 text-right font-semibold">{r.uio.toLocaleString()}</td>
                      <td className="py-2 text-right text-slate-500">{r.uio_pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* Full historical table */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">All Models — UIO.xlsx (complete list)</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                <th className="py-2 pr-2 w-8">#</th>
                <th className="py-2 pr-4">Model</th>
                <th className="py-2 pr-4 text-right">Total Sales</th>
                <th className="py-2 text-right">UIO</th>
              </tr>
            </thead>
            <tbody>
              {data.external.map((r, i) => (
                <tr key={r.model} className="border-b border-slate-50 hover:bg-purple-50/20">
                  <td className="py-1.5 pr-2 text-xs text-slate-400">{i + 1}</td>
                  <td className="py-1.5 pr-4 text-xs text-slate-700">{r.model}</td>
                  <td className="py-1.5 pr-4 text-right text-xs text-slate-500">{r.total_sales_units.toLocaleString()}</td>
                  <td className="py-1.5 text-right font-semibold text-purple-700">{r.uio.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
