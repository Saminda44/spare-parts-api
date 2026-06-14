import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, Legend, PieChart, Pie,
} from "recharts";
import {
  fetchOrdersEda, fetchSalesEda, fetchMovements, fetchSparePartsEda,
  type OrdersEdaData, type SalesEdaData, type MovementsData, type SparePartsEdaData,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { TimePicker, filterByRange, type TimeRange, YearPicker, filterByYear, getYears, monthLabel } from "../components/TimePicker";

const CLASS_COLOR: Record<string, string> = {
  issue: "#EF4444", receipt: "#2CC56F", transfer: "#4361EE",
  adjustment: "#FFC107", scrap: "#94A3B8", issue_rev: "#F97316",
  return: "#7C3AED", receipt_rev: "#06B6D4",
};
const CAT_COLORS = ["#EF4444","#F97316","#FFC107","#4361EE","#2CC56F","#7C3AED","#94A3B8"];

function fmt(n: number) {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type Tab = "orders" | "sales" | "movements" | "spare";

export function EDA() {
  const [ordersData,  setOrdersData]  = useState<OrdersEdaData | null>(null);
  const [salesData,   setSalesData]   = useState<SalesEdaData | null>(null);
  const [movData,     setMovData]     = useState<MovementsData | null>(null);
  const [spareData,   setSpareData]   = useState<SparePartsEdaData | null>(null);
  const [tab,         setTab]         = useState<Tab>("orders");
  const [ordersYear,  setOrdersYear]  = useState<number | "All">("All");
  const [ordersRange, setOrdersRange] = useState<TimeRange>("YTD");
  const [salesYear,   setSalesYear]   = useState<number | "All">("All");
  const [salesRange,  setSalesRange]  = useState<TimeRange>("YTD");
  const [movYear,     setMovYear]     = useState<number | "All">("All");
  const [movRange,    setMovRange]    = useState<TimeRange>("YTD");

  useEffect(() => {
    fetchOrdersEda("MC").then(d => {
      setOrdersData(d);
      const yrs = getYears(d.monthly_trend);
      if (yrs.length) setOrdersYear(yrs[0]);
    });
    fetchSalesEda("MC").then(d => {
      setSalesData(d);
      const yrs = getYears(d.monthly_trend);
      if (yrs.length) setSalesYear(yrs[0]);
    });
    fetchMovements().then(d => {
      setMovData(d);
      const yrs = getYears(d.monthly_trend);
      if (yrs.length) setMovYear(yrs[0]);
    });
    fetchSparePartsEda().then(setSpareData);
  }, []);

  const TABS = [
    { key: "orders"    as Tab, label: "Orders EDA (Stage 4)"       },
    { key: "sales"     as Tab, label: "Sales EDA (Stage 5)"        },
    { key: "movements" as Tab, label: "Stock Movements (Stage 7)"  },
    { key: "spare"     as Tab, label: "Spare Parts EDA (Stage 8)"  },
  ];

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto">
      <h2 className="text-xl font-bold text-slate-800">Motorcycle Spare Parts EDA</h2>
      <p className="text-xs text-slate-500 -mt-4">Stages 4, 5, 7, 8 · MC dealer orders &amp; sales, stock movements, demand characterisation</p>

      {/* Global KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Purchase Orders"  value={ordersData ? fmt(ordersData.total_po)    : "…"} color="blue"/>
        <KpiCard label="Avg Fill Rate"    value={ordersData ? `${(ordersData.avg_fill_rate*100).toFixed(1)}%` : "…"} sub={`${ordersData?.fill_rate_lt1_count ?? 0} lines short-shipped`} color="green"/>
        <KpiCard label="Parts Revenue"    value={salesData  ? `LKR ${fmt(salesData.total_revenue_lkr)}` : "…"} color="purple"/>
        <KpiCard label="Movement Records" value={movData    ? fmt(movData.total_records) : "…"} sub={movData ? `${movData.date_from} → ${movData.date_to}` : ""} color="teal"/>
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
                {/* Monthly trend */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-700">Monthly Order Volume</h3>
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

                {/* Top dealers */}
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Top Dealers by Order Count</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={ordersData.top_dealers} layout="vertical" margin={{ top:0, right:40, left:5, bottom:0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                      <XAxis type="number" tick={{ fontSize: 10 }} tickFormatter={fmt}/>
                      <YAxis type="category" dataKey="dealer" tick={{ fontSize: 9 }} width={140}/>
                      <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                      <Bar dataKey="order_count" fill="#4361EE" name="Orders" radius={[0,3,3,0]}/>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Order fulfillment breakdown */}
              {ordersData.orders_received_breakdown && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-700 mb-2">Order Fulfillment Breakdown</h3>
                    <p className="text-xs text-slate-400 mb-3">By Purchase Order document — classified across all line items per order.</p>
                    <ResponsiveContainer width="100%" height={180}>
                      <PieChart>
                        <Pie
                          data={[
                            { name: "Fully Filled",   value: ordersData.orders_received_breakdown.fully_filled,  fill: "#2CC56F" },
                            { name: "Partial Fill",   value: ordersData.orders_received_breakdown.partial_fill,  fill: "#FFC107" },
                            { name: "Complete Zero",  value: ordersData.orders_received_breakdown.complete_zero, fill: "#EF4444" },
                          ]}
                          dataKey="value" cx="50%" cy="50%" outerRadius={70} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                          labelLine={false}
                        >
                          {["#2CC56F","#FFC107","#EF4444"].map((c, i) => <Cell key={i} fill={c}/>)}
                        </Pie>
                        <Tooltip formatter={(v: unknown) => Number(v).toLocaleString()}/>
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="flex flex-col gap-2 justify-center">
                    {[
                      { label: "Total PO Documents", value: ordersData.orders_received_breakdown.total_documents, color: "slate" },
                      { label: "Fully Filled", value: ordersData.orders_received_breakdown.fully_filled, color: "green" },
                      { label: "Partially Filled", value: ordersData.orders_received_breakdown.partial_fill, color: "amber" },
                      { label: "Complete Zero (all rejected)", value: ordersData.orders_received_breakdown.complete_zero, color: "red" },
                    ].map(({ label, value, color }) => (
                      <div key={label} className="flex justify-between items-center bg-slate-50 rounded-lg px-4 py-2">
                        <span className="text-xs text-slate-600">{label}</span>
                        <span className={`font-bold text-sm text-${color}-600`}>{value.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Rejection reasons */}
              {ordersData.rejection_reasons.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Rejection Reasons</h3>
                  <p className="text-xs text-slate-400 mb-3">Fully-rejected PO lines grouped by reason code.</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                          <th className="py-2 pr-4">Reason</th>
                          <th className="py-2 pr-4 text-right">Lines</th>
                          <th className="py-2 pr-4 text-right">Qty</th>
                          <th className="py-2 text-right">Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ordersData.rejection_reasons.map((r, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-red-50/20">
                            <td className="py-2 pr-4 text-xs text-slate-700">{r.reason || "— unspecified —"}</td>
                            <td className="py-2 pr-4 text-right font-semibold text-red-600">{r.rejected_lines.toLocaleString()}</td>
                            <td className="py-2 pr-4 text-right text-xs text-slate-500">{r.rejected_qty.toFixed(0)}</td>
                            <td className="py-2 text-right">
                              <div className="flex items-center justify-end gap-2">
                                <div className="w-16 bg-slate-100 rounded-full h-1.5">
                                  <div className="bg-red-400 h-1.5 rounded-full" style={{ width: `${Math.min(r.share_pct, 100)}%` }}/>
                                </div>
                                <span className="text-xs text-slate-600 w-10 text-right">{r.share_pct.toFixed(1)}%</span>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Rejection log */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Rejection Log — Fully Short-Shipped Lines</h3>
                <p className="text-xs text-slate-400 mb-3">Lines where fill rate = 0 (order qty received = 0). Require follow-up.</p>
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
                            <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ background: r.fill_rate >= 0.99 ? "#DCFCE7" : r.fill_rate >= 0.8 ? "#FEF9C3" : "#FEE2E2", color: r.fill_rate >= 0.99 ? "#16A34A" : r.fill_rate >= 0.8 ? "#CA8A04" : "#DC2626" }}>
                              {(r.fill_rate * 100).toFixed(0)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}

        {/* ── Sales EDA ── */}
        {tab === "sales" && (
          salesData ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <KpiCard label="Total Revenue" value={`LKR ${fmt(salesData.total_revenue_lkr)}`}   color="purple"/>
                <KpiCard label="Total Units"   value={fmt(salesData.total_units)}                    color="blue"/>
                <KpiCard label="Channels"      value={Object.keys(salesData.by_channel).length.toString()} sub={Object.keys(salesData.by_channel).join(" · ")} color="teal"/>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Monthly revenue trend */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-semibold text-slate-700">Monthly Spare Parts Revenue (LKR)</h3>
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
                      <Line type="monotone" dataKey="revenue_lkr" stroke="#7C3AED" strokeWidth={2} dot={{ r: 3 }} name="Revenue (LKR)"/>
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                {/* By product category */}
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

              {/* Channel breakdown table */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Revenue by Channel</h3>
                <div className="flex gap-4">
                  {Object.entries(salesData.by_channel).map(([ch, val]) => (
                    <div key={ch} className="flex-1 bg-slate-50 rounded-lg p-4">
                      <p className="text-xs text-slate-500 uppercase tracking-wider">{ch}</p>
                      <p className="text-xl font-bold text-slate-800 mt-1">LKR {fmt(val)}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}

        {/* ── Stock Movements ── */}
        {tab === "movements" && (
          movData ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Total Records" value={fmt(movData.total_records)}         color="blue"/>
                <KpiCard label="Issues"         value={fmt(movData.by_class.issue ?? 0)}  sub="outbound to customers" color="red"/>
                <KpiCard label="Receipts"       value={fmt(movData.by_class.receipt ?? 0)} sub="inbound from suppliers" color="green"/>
                <KpiCard label="Transfers"      value={fmt(movData.by_class.transfer ?? 0)} sub="internal movement" color="purple"/>
              </div>

              {/* Movement class breakdown */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
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

                {/* Monthly issue vs receipt */}
                <div>
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

              {/* Movement class table */}
              <div className="overflow-x-auto">
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
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}

        {/* ── Spare Parts EDA ── */}
        {tab === "spare" && (
          spareData ? (
            <div className="space-y-5">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Total SKUs"       value={fmt(spareData.total_skus)}       color="blue"/>
                <KpiCard label="Total Issue Value" value={`LKR ${fmt(spareData.total_issue_value_lkr)}`} sub="cumulative parts issued" color="purple"/>
                <KpiCard label="Median p_zero"    value={`${(spareData.median_p_zero * 100).toFixed(0)}%`} sub="months with zero demand" color="amber"/>
                <KpiCard label="Median CV"        value={spareData.median_cv.toFixed(2)} sub="demand variability" color="teal"/>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="In SSOP"           value={fmt(spareData.in_ssop_count)}    sub="in supersession table" color="amber"/>
                <KpiCard label="Non-Moving"        value={fmt(spareData.demand_category_counts.non_moving ?? 0)} sub="p_zero=1 across period" color="red"/>
                <KpiCard label="Intermittent / Lumpy" value={fmt(spareData.intermittent_skus.length)} sub="p_zero ≥ 70%, ever active" color="orange"/>
                <KpiCard label="Smooth Demand"     value={fmt(spareData.demand_category_counts.smooth ?? 0)} sub="regular fast movers" color="green"/>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Demand category */}
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">Demand Category Distribution</h3>
                  <p className="text-xs text-slate-400 mb-2">Based on CV and p_zero thresholds (Croston / ADIDA categorisation)</p>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart
                      data={Object.entries(spareData.demand_category_counts).map(([k, v], i) => ({ name: k, value: v, fill: CAT_COLORS[i % CAT_COLORS.length] }))}
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

                {/* p_zero histogram */}
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-2">P(zero demand) Histogram</h3>
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

              {/* Top 20 SKUs by issue value — Pareto */}
              <div>
                <h3 className="text-sm font-semibold text-slate-700 mb-1">Top 20 SKUs by Issue Value (Pareto)</h3>
                <p className="text-xs text-slate-400 mb-3">Ranked by cumulative LKR value issued. Cumulative share shows concentration.</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-2 w-8">#</th>
                        <th className="py-2 pr-3">Part No.</th>
                        <th className="py-2 pr-3">Description</th>
                        <th className="py-2 pr-3">Category</th>
                        <th className="py-2 pr-3 text-right">Issue Value (LKR)</th>
                        <th className="py-2 pr-3 text-right">Issue Qty</th>
                        <th className="py-2 text-right">Cum. Share %</th>
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
                          <td className="py-2 pr-3 text-right font-semibold text-slate-800">
                            {fmt(r.total_issue_value_lkr)}
                          </td>
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

              {/* Intermittent / Lumpy SKUs */}
              {spareData.intermittent_skus.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-1">
                    Intermittent &amp; Lumpy SKUs
                    <span className="ml-2 text-xs font-normal text-slate-400">(p_zero ≥ 70%, at least 1 active month)</span>
                  </h3>
                  <p className="text-xs text-slate-400 mb-3">These SKUs require Croston / ADIDA / SBA forecasting — standard methods underestimate demand.</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                          <th className="py-2 pr-3">Part No.</th>
                          <th className="py-2 pr-3">Description</th>
                          <th className="py-2 pr-3">Category</th>
                          <th className="py-2 pr-3 text-right">p_zero</th>
                          <th className="py-2 pr-3 text-right">CV</th>
                          <th className="py-2 pr-3 text-right">Active Mo.</th>
                          <th className="py-2 pr-3 text-right">Avg/Mo</th>
                          <th className="py-2 text-right">Issue Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {spareData.intermittent_skus.slice(0, 100).map((r, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-blue-50/20">
                            <td className="py-2 pr-3 font-mono text-xs text-slate-700">{r.material_9}</td>
                            <td className="py-2 pr-3 text-xs text-slate-600 max-w-[200px] truncate" title={r.description}>{r.description}</td>
                            <td className="py-2 pr-3">
                              <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">{r.demand_category}</span>
                            </td>
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

              {/* Ingestion log */}
              <div>
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
          ) : <p className="text-slate-400 text-sm">Loading…</p>
        )}
      </div>
    </div>
  );
}
