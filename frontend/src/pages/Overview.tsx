import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { fetchOverview, fetchTrend, type OverviewData, type MonthlyPoint } from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { Package, AlertTriangle, ShoppingCart, TrendingDown, DollarSign, Shield, Archive, Clock } from "lucide-react";

const STATUS_COLOR: Record<string, string> = {
  stockout: "#EF4444", critical: "#F97316", low: "#FFC107", ok: "#2CC56F", excess: "#4361EE",
};
const URGENCY_COLOR: Record<string, string> = {
  immediate: "#EF4444", soon: "#FFC107", planned: "#4361EE", none: "#94A3B8",
};

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export function Overview() {
  const navigate = useNavigate();
  const [data, setData] = useState<OverviewData | null>(null);
  const [trend, setTrend] = useState<MonthlyPoint[]>([]);

  useEffect(() => {
    fetchOverview().then(setData);
    fetchTrend().then(setTrend);
  }, []);

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">Loading…</div>;

  const { kpis, stock_status, abc_counts, urgency_counts, tier_counts, ss_method_counts } = data;
  const statusData  = Object.entries(stock_status).map(([k, v]) => ({ name: k, value: v }));
  const abcData     = Object.entries(abc_counts).map(([k, v]) => ({ name: k, value: v }));
  const urgData     = Object.entries(urgency_counts).filter(([k]) => k !== "none").map(([k, v]) => ({ name: k, value: v }));
  const tierData    = Object.entries(tier_counts).map(([k, v]) => ({ name: k, value: v }));
  const ssData      = Object.entries(ss_method_counts).map(([k, v]) => ({ name: k, value: v }));
  const trendData   = trend.slice(-24).map(p => ({ month: p.year_month_str.slice(0, 7), qty: p.issue_qty, val: p.issue_value_lkr }));

  const TIER_COLOR: Record<string, string> = { critical: "#EF4444", managed: "#FFC107", watch: "#4361EE", rationalise: "#94A3B8" };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Dashboard Overview</h2>

      {/* KPI row 1 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total SKUs"        value={fmt(kpis.total_skus)}            sub={`${fmt(kpis.active_skus)} active`}    color="blue"   icon={<Package size={18}/>}/>
        <div className="cursor-pointer hover:scale-[1.02] transition-transform" onClick={() => navigate("/inventory?status=stockout")}>
          <KpiCard label="Stockout SKUs"   value={fmt(kpis.stockout_skus)}          sub={`${fmt(kpis.critical_skus)} critical · click to view`} color="red"  icon={<AlertTriangle size={18}/>}/>
        </div>
        <div className="cursor-pointer hover:scale-[1.02] transition-transform" onClick={() => navigate("/inventory?status=excess")}>
          <KpiCard label="Excess Stock SKUs" value={fmt(kpis.excess_skus)}          sub={`LKR ${fmt(kpis.excess_stock_value_lkr)} tied up · click to view`} color="amber" icon={<Archive size={18}/>}/>
        </div>
        <KpiCard label="Avg Coverage"      value={`${kpis.avg_coverage_months.toFixed(1)} mo`} sub="active SKUs"              color="teal"   icon={<Clock size={18}/>}/>
      </div>

      {/* KPI row 2 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="cursor-pointer hover:scale-[1.02] transition-transform" onClick={() => navigate("/orders?urgency=immediate")}>
          <KpiCard label="Immediate Orders" value={fmt(kpis.immediate_orders)}      sub={`${fmt(kpis.soon_orders)} soon · ${fmt(kpis.planned_orders)} planned · click to view`} color="red" icon={<ShoppingCart size={18}/>}/>
        </div>
        <KpiCard label="Total Order Value" value={`LKR ${fmt(kpis.total_order_value_lkr)}`} sub="Immediate + soon + planned"  color="purple" icon={<DollarSign size={18}/>}/>
        <div className="cursor-pointer hover:scale-[1.02] transition-transform" onClick={() => navigate("/orders?flagged=true")}>
          <KpiCard label="Sanity Flagged"  value={fmt(kpis.sanity_flag_count)}      sub="ROL/ROQ > 3× demand · click to view" color="amber"  icon={<Shield size={18}/>}/>
        </div>
        <div className="cursor-pointer hover:scale-[1.02] transition-transform" onClick={() => navigate("/rl")}>
          <KpiCard label="RL Order Reduction" value={`${kpis.rl_avg_order_reduction_pct.toFixed(1)}%`} sub="avg vs rule-based · click for details" color="green" icon={<TrendingDown size={18}/>}/>
        </div>
      </div>

      {/* Charts row 1 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Stock status donut */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Stock Status Distribution</h3>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={statusData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} dataKey="value" nameKey="name">
                {statusData.map(d => <Cell key={d.name} fill={STATUS_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Pie>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-2 mt-1">
            {statusData.map(d => (
              <span key={d.name} className="flex items-center gap-1 text-xs text-slate-600">
                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: STATUS_COLOR[d.name] ?? "#94A3B8" }}/>
                {d.name} ({d.value.toLocaleString()})
              </span>
            ))}
          </div>
        </div>

        {/* ABC bar */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">ABC Classification</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={abcData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="name" tick={{ fontSize: 12 }}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {abcData.map(d => <Cell key={d.name} fill={d.name === "A" ? "#EF4444" : d.name === "B" ? "#FFC107" : "#2CC56F"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Order urgency */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Order Urgency</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={urgData} layout="vertical" margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
              <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {urgData.map(d => <Cell key={d.name} fill={URGENCY_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Charts row 2 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Policy tier */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Policy Tier</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={tierData} layout="vertical" margin={{ top: 0, right: 30, left: 5, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
              <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {tierData.map(d => <Cell key={d.name} fill={TIER_COLOR[d.name] ?? "#94A3B8"}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Safety stock method */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Safety Stock Method</h3>
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie data={ssData} cx="50%" cy="50%" innerRadius={45} outerRadius={70} dataKey="value" nameKey="name">
                <Cell fill="#7C3AED"/>
                <Cell fill="#94A3B8"/>
              </Pie>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-3 mt-1 justify-center">
            {ssData.map((d, i) => (
              <span key={d.name} className="flex items-center gap-1 text-xs text-slate-600">
                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: i === 0 ? "#7C3AED" : "#94A3B8" }}/>
                {d.name} ({d.value.toLocaleString()})
              </span>
            ))}
          </div>
        </div>

        {/* Demand trend */}
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Monthly Demand (last 24 mo)</h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={trendData} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
              <XAxis dataKey="month" tick={{ fontSize: 9 }} interval={3}/>
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
              <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
              <Bar dataKey="qty" fill="#4361EE" radius={[3, 3, 0, 0]}/>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
