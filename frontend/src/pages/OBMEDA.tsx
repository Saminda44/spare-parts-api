import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, Legend,
} from "recharts";
import {
  fetchOrdersEda, fetchSalesEda,
  type OrdersEdaData, type SalesEdaData,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TimePicker, filterByRange, type TimeRange, YearPicker, filterByYear, getYears, monthLabel } from "../components/TimePicker";

const CAT_COLORS = ["#EF4444","#F97316","#FFC107","#4361EE","#2CC56F","#7C3AED","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "orders" | "sales";

export function OBMEDA() {
  const [ordersData, setOrdersData] = useState<OrdersEdaData | null>(null);
  const [salesData,  setSalesData]  = useState<SalesEdaData  | null>(null);
  const [tab,         setTab]         = useState<Tab>("orders");
  const [ordersYear,  setOrdersYear]  = useState<number | "All">("All");
  const [ordersRange, setOrdersRange] = useState<TimeRange>("YTD");
  const [salesYear,   setSalesYear]   = useState<number | "All">("All");
  const [salesRange,  setSalesRange]  = useState<TimeRange>("YTD");

  useEffect(() => {
    fetchOrdersEda("OBM").then(d => {
      setOrdersData(d);
      const yrs = getYears(d.monthly_trend);
      if (yrs.length) setOrdersYear(yrs[0]);
    });
    fetchSalesEda("OBM").then(d => {
      setSalesData(d);
      const yrs = getYears(d.monthly_trend);
      if (yrs.length) setSalesYear(yrs[0]);
    });
  }, []);

  const TABS = [
    { key: "orders" as Tab, label: "Orders EDA (Stage 4)" },
    { key: "sales"  as Tab, label: "Sales EDA (Stage 5)"  },
  ];

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <div>
        <h2 className="text-xl font-bold text-slate-800">Outboard Motor (OBM) Parts EDA</h2>
        <p className="text-xs text-slate-500 mt-0.5">Stages 4 &amp; 5 · OBM dealer orders &amp; sales performance · Separate from motorcycle spare parts</p>
      </div>

      {/* Global KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Purchase Orders"  value={ordersData ? fmt(ordersData.total_po)    : "…"} color="blue"/>
        <KpiCard label="Avg Fill Rate"    value={ordersData ? `${(ordersData.avg_fill_rate*100).toFixed(1)}%` : "…"} sub={`${ordersData?.fill_rate_lt1_count ?? 0} lines short-shipped`} color="green"/>
        <KpiCard label="OBM Revenue"      value={salesData  ? `LKR ${fmt(salesData.total_revenue_lkr)}` : "…"} color="purple"/>
        <KpiCard label="Avg Lead Time"    value={ordersData ? `${ordersData.avg_lead_time_days.toFixed(1)} days` : "…"} color="teal"/>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5">
        <div className="flex gap-1 mb-5 border-b border-slate-100 pb-2">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${tab === t.key ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Orders EDA ── */}
        {tab === "orders" && (
          ordersData ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Total POs"       value={fmt(ordersData.total_po)}            color="blue"/>
                <KpiCard label="Returns"          value={fmt(ordersData.total_returns)}        color="red"/>
                <KpiCard label="Avg Fill Rate"    value={`${(ordersData.avg_fill_rate*100).toFixed(2)}%`} sub={`${ordersData.fill_rate_lt1_count} short-shipped`} color="green"/>
                <KpiCard label="Avg Lead Time"    value={`${ordersData.avg_lead_time_days.toFixed(1)} days`} sub="Good issue − created" color="purple"/>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-700">Monthly OBM Order Volume</h3>
                    <div className="flex items-center gap-2">
                      <YearPicker years={getYears(ordersData.monthly_trend)} value={ordersYear} onChange={setOrdersYear}/>
                      <TimePicker value={ordersRange} onChange={setOrdersRange}/>
                    </div>
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={filterByRange(filterByYear(ordersData.monthly_trend, ordersYear), ordersRange)} margin={{ top:5, right:10, left:0, bottom:5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                        tickFormatter={p => monthLabel(p, ordersYear !== "All")}/>
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                      <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()} labelFormatter={p => String(p)}/>
                      <Legend/>
                      <Bar dataKey="po_count"     fill="#4361EE" name="Purchase Orders" radius={[2,2,0,0]} stackId="a"/>
                      <Bar dataKey="return_count" fill="#EF4444" name="Returns"         radius={[2,2,0,0]} stackId="a"/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Top OBM Dealers by Order Count</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={ordersData.top_dealers} layout="vertical" margin={{ top:0, right:40, left:5, bottom:0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                      <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                      <YAxis type="category" dataKey="dealer" tick={{ fontSize: 9 }} width={160}/>
                      <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                      <Bar dataKey="order_count" fill="#06B6D4" name="Orders" radius={[0,3,3,0]}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Rejection log */}
              {ordersData.rejections.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Rejection Log — Fully Short-Shipped Lines</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                          <th className="py-2 pr-3">Material</th>
                          <th className="py-2 pr-3">Description</th>
                          <th className="py-2 pr-3">Customer</th>
                          <th className="py-2 pr-3">Date</th>
                          <th className="py-2 pr-3 text-right">Ordered</th>
                          <th className="py-2 pr-3 text-right">Lost</th>
                          <th className="py-2 text-right">Fill Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ordersData.rejections.map((r, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-red-50/20">
                            <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material}</td>
                            <td className="py-2 pr-3 text-slate-600 max-w-[160px] truncate" title={r.description}>{r.description}</td>
                            <td className="py-2 pr-3 text-xs text-slate-500 max-w-[130px] truncate" title={r.customer}>{r.customer}</td>
                            <td className="py-2 pr-3 text-xs text-slate-400">{r.document_date}</td>
                            <td className="py-2 pr-3 text-right">{r.order_qty.toFixed(0)}</td>
                            <td className="py-2 pr-3 text-right text-red-600 font-semibold">{r.lost_qty.toFixed(0)}</td>
                            <td className="py-2 text-right">
                              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                                style={{ background: r.fill_rate >= 0.99 ? "#DCFCE7" : r.fill_rate >= 0.8 ? "#FEF9C3" : "#FEE2E2", color: r.fill_rate >= 0.99 ? "#16A34A" : r.fill_rate >= 0.8 ? "#CA8A04" : "#DC2626" }}>
                                {(r.fill_rate * 100).toFixed(0)}%
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
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}

        {/* ── Sales EDA ── */}
        {tab === "sales" && (
          salesData ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <KpiCard label="OBM Revenue"  value={`LKR ${fmt(salesData.total_revenue_lkr)}`} color="purple"/>
                <KpiCard label="Total Units"  value={fmt(salesData.total_units)}                  color="blue"/>
                <KpiCard label="Channels"     value={Object.keys(salesData.by_channel).length.toString()} sub={Object.keys(salesData.by_channel).join(" · ")} color="teal"/>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-700">Monthly OBM Parts Revenue (LKR)</h3>
                    <div className="flex items-center gap-2">
                      <YearPicker years={getYears(salesData.monthly_trend)} value={salesYear} onChange={setSalesYear}/>
                      <TimePicker value={salesRange} onChange={setSalesRange}/>
                    </div>
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={filterByRange(filterByYear(salesData.monthly_trend, salesYear), salesRange)} margin={{ top:5, right:10, left:0, bottom:5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                      <XAxis dataKey="period" tick={{ fontSize: 10 }} interval={0}
                        tickFormatter={p => monthLabel(p, salesYear !== "All")}/>
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt}/>
                      <Tooltip formatter={(v: unknown) => fmt(Number(v))} labelFormatter={p => String(p)}/>
                      <Line type="monotone" dataKey="revenue_lkr" stroke="#06B6D4" strokeWidth={2} dot={{ r: 3 }} name="Revenue (LKR)"/>
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Revenue by Product Category</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart
                      data={Object.entries(salesData.by_category).map(([k, v], i) => ({ name: k, value: v, fill: CAT_COLORS[i % CAT_COLORS.length] }))}
                      layout="vertical" margin={{ top:0, right:40, left:10, bottom:0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                      <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                      <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={150}/>
                      <Tooltip formatter={(v: unknown) => `LKR ${fmt(Number(v))}`}/>
                      <Bar dataKey="value" radius={[0,3,3,0]}>
                        {Object.entries(salesData.by_category).map(([k], i) => <Cell key={k} fill={CAT_COLORS[i % CAT_COLORS.length]}/>)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {Object.keys(salesData.by_channel).length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Revenue by Channel</h3>
                  <div className="flex gap-4 flex-wrap">
                    {Object.entries(salesData.by_channel).map(([ch, val]) => (
                      <div key={ch} className="flex-1 min-w-[160px] bg-slate-50 rounded-lg p-4">
                        <p className="text-xs text-slate-500 uppercase tracking-wider">{ch}</p>
                        <p className="text-xl font-bold text-slate-800 mt-1">LKR {fmt(val)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}
      </div>
    </div>
  );
}
