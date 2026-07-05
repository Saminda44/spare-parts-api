import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, AreaChart, Area,
} from "recharts";
import {
  fetchOverview, fetchMcsiEda, fetchSparePartsEda,
  type OverviewData, type McsiEdaData, type SparePartsEdaData,
} from "../api/client";
import {
  ChevronRight, Bike, Package, Archive,
  ShoppingCart, BarChart2, Activity,
} from "lucide-react";

const STATUS_COLOR: Record<string, string> = {
  stockout: "#EF4444", critical: "#F97316", low: "#FFC107", ok: "#2CC56F", excess: "#4361EE",
};
const URGENCY_COLOR: Record<string, string> = {
  immediate: "#EF4444", soon: "#FFC107", planned: "#4361EE",
};
const DEMAND_COLOR: Record<string, string> = {
  smooth: "#2CC56F", erratic: "#F97316", intermittent: "#FFC107",
  lumpy: "#EF4444", slow_moving: "#94A3B8", new: "#4361EE",
};

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)         return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

function MiniKpi({ label, value, sub, color = "slate" }: { label: string; value: string; sub?: string; color?: string }) {
  const border: Record<string, string> = {
    blue: "border-blue-400", green: "border-green-400", red: "border-red-400",
    amber: "border-amber-400", purple: "border-violet-400", teal: "border-teal-400",
    slate: "border-slate-300",
  };
  const text: Record<string, string> = {
    blue: "text-blue-700", green: "text-green-600", red: "text-red-600",
    amber: "text-amber-600", purple: "text-violet-700", teal: "text-teal-600",
    slate: "text-slate-700",
  };
  return (
    <div className={`bg-white border-l-4 ${border[color]} rounded-lg p-3 shadow-sm`}>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 mb-0.5">{label}</p>
      <p className={`text-xl font-bold leading-tight ${text[color]}`}>{value}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5 leading-snug">{sub}</p>}
    </div>
  );
}

function SectionHeader({ icon, title, subtitle, to, onNav }: {
  icon: React.ReactNode; title: string; subtitle: string; to: string; onNav: (to: string) => void;
}) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2.5">
        <div className="text-slate-500">{icon}</div>
        <div>
          <h3 className="text-base font-bold text-slate-800 leading-tight">{title}</h3>
          <p className="text-[11px] text-slate-500">{subtitle}</p>
        </div>
      </div>
      <button
        onClick={() => onNav(to)}
        className="flex items-center gap-1 text-xs font-medium text-brand-blue hover:underline"
      >
        View details <ChevronRight size={14}/>
      </button>
    </div>
  );
}

export function Overview() {
  const navigate = useNavigate();
  const [overview, setOverview]     = useState<OverviewData | null>(null);
  const [mcsi,     setMcsi]         = useState<McsiEdaData | null>(null);
  const [spare,    setSpare]        = useState<SparePartsEdaData | null>(null);

  useEffect(() => {
    fetchOverview().then(setOverview);
    fetchMcsiEda().then(setMcsi);
    fetchSparePartsEda().then(setSpare);
  }, []);

  // ── MC Analysis derived ────────────────────────────────────────────────────
  const mcsiTrend12 = (mcsi?.monthly_trend ?? []).slice(-12).map(p => ({
    month: p.period.slice(0, 7), sold: p.sold,
  }));

  // ── Inventory Analysis derived ─────────────────────────────────────────────
  const statusData = Object.entries(overview?.stock_status ?? {})
    .map(([k, v]) => ({ name: k, value: v }));
  const urgData    = Object.entries(overview?.urgency_counts ?? {})
    .filter(([k]) => k !== "none")
    .map(([k, v]) => ({ name: k, value: v }));

  // ── Spare Parts derived ────────────────────────────────────────────────────
  const demandCatData = Object.entries(spare?.demand_category_counts ?? {})
    .map(([k, v]) => ({ name: k, value: v }))
    .sort((a, b) => b.value - a.value);

  return (
    <div className="flex-1 p-6 space-y-5 overflow-y-auto">
      <div>
        <h2 className="text-xl font-bold text-slate-800">Dashboard Overview</h2>
        <p className="text-xs text-slate-500 mt-0.5">At-a-glance summary across MC Analysis, Inventory, and Spare Parts</p>
      </div>

      {/* ── Section 1: MC Analysis ─────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm p-5 border-t-4 border-blue-500">
        <SectionHeader
          icon={<Bike size={20}/>}
          title="MC Analysis"
          subtitle="Motorcycle sales, dealer performance, geography · MCSI data"
          to="/mcsi-eda"
          onNav={navigate}
        />

        {!mcsi ? (
          <p className="text-xs text-slate-400 italic">Loading…</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* KPIs */}
            <div className="grid grid-cols-2 gap-2.5">
              <MiniKpi label="Total Bikes Sold"  value={fmt(mcsi.kpis.sold)}               color="blue"/>
              <MiniKpi label="Total Revenue"     value={`LKR ${fmt(mcsi.kpis.total_revenue_lkr)}`} color="green"/>
              <MiniKpi label="Active Dealers"    value={mcsi.kpis.active_dealers.toLocaleString()} sub={`${mcsi.kpis.active_provinces} provinces`} color="teal"/>
              <MiniKpi label="Models Sold"       value={mcsi.kpis.models_sold.toLocaleString()} sub={`${mcsi.kpis.months_of_data} mo of data`} color="purple"/>
            </div>
            {/* Monthly trend sparkline */}
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Monthly Units Sold (last 12 mo)</p>
              <ResponsiveContainer width="100%" height={110}>
                <AreaChart data={mcsiTrend12} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="mcsiGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#4361EE" stopOpacity={0.25}/>
                      <stop offset="95%" stopColor="#4361EE" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                  <XAxis dataKey="month" tick={{ fontSize: 9 }} interval={2}/>
                  <YAxis tick={{ fontSize: 9 }} tickFormatter={fmt} width={32}/>
                  <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "Sold"]}/>
                  <Area type="monotone" dataKey="sold" stroke="#4361EE" strokeWidth={2} fill="url(#mcsiGrad)" dot={false}/>
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>

      {/* ── Section 2: Inventory Analysis ─────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm p-5 border-t-4 border-amber-500">
        <SectionHeader
          icon={<Package size={20}/>}
          title="Inventory Analysis"
          subtitle="Stock health, order urgency, ABC classification, ROL / ROQ policy"
          to="/inventory"
          onNav={navigate}
        />

        {!overview ? (
          <p className="text-xs text-slate-400 italic">Loading…</p>
        ) : (
          <div className="space-y-4">
            {/* KPI row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
              <div className="cursor-pointer hover:opacity-80 transition-opacity" onClick={() => navigate("/inventory?status=stockout")}>
                <MiniKpi label="Stockout SKUs"   value={fmt(overview.kpis.stockout_skus)} sub={`${fmt(overview.kpis.critical_skus)} critical`} color="red"/>
              </div>
              <div className="cursor-pointer hover:opacity-80 transition-opacity" onClick={() => navigate("/inventory?status=excess")}>
                <MiniKpi label="Excess Stock"    value={fmt(overview.kpis.excess_skus)}   sub={`LKR ${fmt(overview.kpis.excess_stock_value_lkr)} tied up`} color="amber"/>
              </div>
              <div className="cursor-pointer hover:opacity-80 transition-opacity" onClick={() => navigate("/orders?urgency=immediate")}>
                <MiniKpi label="Immediate Orders" value={fmt(overview.kpis.immediate_orders)} sub={`${fmt(overview.kpis.soon_orders)} soon · ${fmt(overview.kpis.planned_orders)} planned`} color="blue"/>
              </div>
              <MiniKpi label="Avg Coverage" value={`${overview.kpis.avg_coverage_months.toFixed(1)} mo`} sub={`LKR ${fmt(overview.kpis.total_order_value_lkr)} order value`} color="teal"/>
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Stock status donut */}
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Stock Status Distribution</p>
                <div className="flex items-center gap-4">
                  <ResponsiveContainer width={130} height={110}>
                    <PieChart>
                      <Pie data={statusData} cx="50%" cy="50%" innerRadius={32} outerRadius={52} dataKey="value" nameKey="name">
                        {statusData.map(d => <Cell key={d.name} fill={STATUS_COLOR[d.name] ?? "#94A3B8"}/>)}
                      </Pie>
                      <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex flex-col gap-1">
                    {statusData.map(d => (
                      <span key={d.name} className="flex items-center gap-1.5 text-[11px] text-slate-600">
                        <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: STATUS_COLOR[d.name] ?? "#94A3B8" }}/>
                        <span className="capitalize">{d.name}</span>
                        <span className="font-semibold text-slate-800">{d.value.toLocaleString()}</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Order urgency */}
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Order Urgency</p>
                <ResponsiveContainer width="100%" height={110}>
                  <BarChart data={urgData} layout="vertical" margin={{ top: 2, right: 30, left: 10, bottom: 2 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={60}/>
                    <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {urgData.map(d => <Cell key={d.name} fill={URGENCY_COLOR[d.name] ?? "#94A3B8"}/>)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Quick links */}
            <div className="flex flex-wrap gap-2 pt-1 border-t border-slate-100">
              {[
                { label: "Inventory Table", to: "/inventory", icon: <Archive size={12}/> },
                { label: "Order Plan",      to: "/orders",    icon: <ShoppingCart size={12}/> },
                { label: "Classification",  to: "/classification", icon: <BarChart2 size={12}/> },
                { label: "RL Policy",       to: "/rl",        icon: <Activity size={12}/> },
              ].map(link => (
                <button key={link.to} onClick={() => navigate(link.to)}
                  className="flex items-center gap-1 text-[11px] font-medium text-slate-500 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-md px-2.5 py-1 transition-colors">
                  {link.icon} {link.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Section 3: Spare Parts Analysis ───────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm p-5 border-t-4 border-violet-500">
        <SectionHeader
          icon={<BarChart2 size={20}/>}
          title="Spare Parts Analysis"
          subtitle="SKU demand patterns, issue value, intermittent demand · Stage 8"
          to="/eda"
          onNav={navigate}
        />

        {!spare ? (
          <p className="text-xs text-slate-400 italic">Loading…</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* KPIs */}
            <div className="space-y-2.5">
              <div className="grid grid-cols-2 gap-2.5">
                <MiniKpi label="Total SKUs"       value={fmt(spare.total_skus)}            sub={`${fmt(spare.in_ssop_count)} in catalogue`} color="purple"/>
                <MiniKpi label="Total Issue Value" value={`LKR ${fmt(spare.total_issue_value_lkr)}`} color="blue"/>
                <MiniKpi label="Median CV"        value={spare.median_cv.toFixed(2)}       sub="demand variability" color="amber"/>
                <MiniKpi label="Intermittent SKUs" value={fmt(spare.intermittent_skus.length)} sub={`p₀ ≥ 0.5`} color="red"/>
              </div>
              {/* Top SKUs mini list */}
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Top SKUs by Issue Value</p>
                <div className="space-y-1">
                  {spare.top_skus.slice(0, 4).map(s => (
                    <div key={s.material_9} className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-600 truncate max-w-[65%]">{s.description || s.material_9}</span>
                      <span className="font-semibold text-violet-700 shrink-0">LKR {fmt(s.total_issue_value_lkr)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Demand category breakdown */}
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Demand Category Breakdown</p>
              <ResponsiveContainer width="100%" height={155}>
                <BarChart data={demandCatData} layout="vertical" margin={{ top: 2, right: 30, left: 10, bottom: 2 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                  <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 10 }} width={80}/>
                  <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "SKUs"]}/>
                  <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                    {demandCatData.map(d => (
                      <Cell key={d.name} fill={DEMAND_COLOR[d.name.toLowerCase().replace(" ", "_")] ?? "#7C3AED"}/>
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>

              {/* Quick links */}
              <div className="flex flex-wrap gap-2 pt-2 mt-1 border-t border-slate-100">
                {[
                  { label: "Demand Forecast", to: "/forecast" },
                  { label: "Spare Parts EDA", to: "/eda"      },
                  { label: "Part Master",     to: "/parts"    },
                ].map(link => (
                  <button key={link.to} onClick={() => navigate(link.to)}
                    className="text-[11px] font-medium text-slate-500 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-md px-2.5 py-1 transition-colors">
                    {link.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
