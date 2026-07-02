import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  ScatterChart, Scatter, ZAxis,
} from "recharts";
import {
  fetchMarketBasket, fetchMarketBasketML,
  type MarketBasketData, type MarketBasketMLData,
  type AssociationRule, type CoOccurrenceGroup, type LargeInvoice,
} from "../api/client";
import { KpiCard } from "../components/KpiCard";

const LIFT_COLOR = (lift: number) =>
  lift >= 10 ? "#EF4444" : lift >= 5 ? "#F97316" : lift >= 2 ? "#FFC107" : "#2CC56F";

const CONF_COLOR = (conf: number) =>
  conf >= 0.8 ? "#16A34A" : conf >= 0.5 ? "#4361EE" : conf >= 0.2 ? "#FFC107" : "#94A3B8";

function pct(n: number) { return `${(n * 100).toFixed(1)}%`; }

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

type SortKey = "lift" | "confidence" | "support";
type GroupSortKey = "count" | "lift" | "support";
type SizeFilter = "all" | "2" | "3" | "4" | "5+";

export function MarketBasket() {
  const [data, setData] = useState<MarketBasketData | null>(null);
  const [loading, setLoading] = useState(false);
  const [minSupport,    setMinSupport]    = useState(0.01);
  const [minConfidence, setMinConfidence] = useState(0.05);
  const [minLift,       setMinLift]       = useState(1.0);
  const [sortKey,       setSortKey]       = useState<SortKey>("lift");
  const [search,        setSearch]        = useState("");
  const [groupSearch,   setGroupSearch]   = useState("");
  const [groupSort,     setGroupSort]     = useState<GroupSortKey>("count");
  const [sizeFilter,    setSizeFilter]    = useState<SizeFilter>("all");
  const [invSearch,     setInvSearch]     = useState("");
  const [activeTab,     setActiveTab]     = useState<"pairs" | "invoices" | "rules" | "itemsets" | "materials" | "ml">("pairs");
  const [mlData,        setMlData]        = useState<MarketBasketMLData | null>(null);
  const [mlLoading,     setMlLoading]     = useState(false);
  const [mlItemSearch,  setMlItemSearch]  = useState("");
  const [mlCustSearch,  setMlCustSearch]  = useState("");
  const [_selectedItem, setSelectedItem]  = useState<string>("");

  const load = (sup: number, conf: number, lift: number) => {
    setLoading(true);
    setData(null);
    fetchMarketBasket(sup, conf, lift)
      .then(setData)
      .finally(() => setLoading(false));
  };

  const loadML = () => {
    if (mlData || mlLoading) return;
    setMlLoading(true);
    fetchMarketBasketML()
      .then(d => { setMlData(d); if (d.item_recommendations.length > 0) setSelectedItem(d.item_recommendations[0].item); })
      .finally(() => setMlLoading(false));
  };

  useEffect(() => { load(minSupport, minConfidence, minLift); }, []);

  const sizeNum = sizeFilter === "all" ? 0 : sizeFilter === "5+" ? -1 : +sizeFilter;
  const sortedGroups: CoOccurrenceGroup[] = [...(data?.groups ?? [])]
    .filter(g => sizeNum === 0 || (sizeNum === -1 ? g.items.length >= 5 : g.items.length === sizeNum))
    .sort((a, b) => b[groupSort] - a[groupSort]);
  const filteredGroups = sortedGroups.filter(g =>
    !groupSearch || g.items.some(m => m.toLowerCase().includes(groupSearch.toLowerCase()))
  );

  const sorted: AssociationRule[] = [...(data?.rules ?? [])].sort((a, b) => b[sortKey] - a[sortKey]);
  const filtered = sorted.filter(r =>
    !search ||
    [...r.antecedents, ...r.consequents].some(m => m.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div className="flex-1 p-6 space-y-5 overflow-y-auto">
      <div>
        <h2 className="text-xl font-bold text-slate-800">Market Basket Analysis</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Co-purchase patterns from service billing invoices · Basket = Billing Document · Item = Material
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total Invoices"      value={data ? fmt(data.total_baskets)          : "…"} color="blue"/>
        <KpiCard label="Multi-Item Invoices" value={data ? fmt(data.multi_item_baskets)      : "…"} sub="invoices with 2+ parts" color="green"/>
        <KpiCard label="Largest Invoice"     value={data ? `${data.max_basket_size} parts`  : "…"} sub="max materials on one invoice" color="purple"/>
        <KpiCard label="Association Rules"   value={data ? fmt(data.total_rules)             : "…"} sub={`lift ≥ ${minLift}, conf ≥ ${pct(minConfidence)}`} color="amber"/>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Mining Parameters</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <label className="block text-xs text-slate-500 mb-1">
              Min Support: <span className="font-semibold text-slate-700">{pct(minSupport)}</span>
              <span className="ml-2 text-slate-400">(= {Math.round(minSupport * (data?.total_baskets ?? 448))} invoices)</span>
            </label>
            <input type="range" min="0.005" max="0.1" step="0.005" value={minSupport}
              onChange={e => setMinSupport(+e.target.value)}
              className="w-full accent-brand-blue"/>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">
              Min Confidence: <span className="font-semibold text-slate-700">{pct(minConfidence)}</span>
            </label>
            <input type="range" min="0.01" max="1.0" step="0.01" value={minConfidence}
              onChange={e => setMinConfidence(+e.target.value)}
              className="w-full accent-brand-blue"/>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">
              Min Lift: <span className="font-semibold text-slate-700">{minLift.toFixed(1)}×</span>
            </label>
            <input type="range" min="1.0" max="20" step="0.5" value={minLift}
              onChange={e => setMinLift(+e.target.value)}
              className="w-full accent-brand-blue"/>
          </div>
        </div>
        <button
          onClick={() => load(minSupport, minConfidence, minLift)}
          disabled={loading}
          className="mt-4 px-5 py-2 bg-brand-blue text-white text-sm font-medium rounded-lg hover:bg-brand-blue/90 disabled:opacity-50 transition-colors">
          {loading ? "Mining…" : "Run Analysis"}
        </button>
      </div>

      {!data && !loading && (
        <div className="text-center text-slate-400 py-12">Adjust parameters and click Run Analysis.</div>
      )}
      {loading && (
        <div className="text-center text-slate-400 py-12">Running FP-Growth…</div>
      )}

      {data && (
        <>
          {/* Tabs */}
          <div className="flex gap-1 border-b border-slate-200 flex-wrap">
            {(["pairs", "invoices", "rules", "itemsets", "materials", "ml"] as const).map(t => (
              <button key={t} onClick={() => { setActiveTab(t); if (t === "ml") loadML(); }}
                className={`px-5 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors capitalize ${
                  activeTab === t
                    ? "border-brand-blue text-brand-blue bg-white"
                    : "border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-50"
                }`}>
                {t === "pairs"    ? `Co-Purchases (${data.groups.length})`
                 : t === "invoices" ? `Invoices (${data.large_invoices.length})`
                 : t === "rules"    ? `Rules (${data.total_rules})`
                 : t === "itemsets" ? `Frequent Itemsets (${data.frequent_itemsets.length})`
                 : t === "ml"       ? "🤖 ML Insights"
                 : "Top Materials"}
              </button>
            ))}
          </div>

          {/* ── Co-Purchases tab (groups of 2, 3, 4) ── */}
          {activeTab === "pairs" && (
            <div className="space-y-4">
              {/* Summary KPIs */}
              {(() => {
                const pairs3  = data.groups.filter(g => g.items.length === 2).length;
                const triples = data.groups.filter(g => g.items.length === 3).length;
                const quads   = data.groups.filter(g => g.items.length === 4).length;
                const large   = data.groups.filter(g => g.items.length >= 5).length;
                return (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <KpiCard label="Pairs (2 items)"   value={String(pairs3)}  sub="2 materials on same invoice"   color="blue"/>
                    <KpiCard label="Triples (3 items)" value={String(triples)} sub="3 materials on same invoice"   color="green"/>
                    <KpiCard label="Quads (4 items)"   value={String(quads)}   sub="4 materials on same invoice"   color="purple"/>
                    <KpiCard label="5+ item groups"    value={String(large)}   sub={`up to ${data.max_basket_size} items on one invoice`} color="red"/>
                  </div>
                );
              })()}

              {/* Top 20 bar chart (default: sorted by count, all sizes) */}
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">
                  Top 20 Co-Purchase Groups by Count
                  <span className="ml-2 text-xs font-normal text-slate-400">(coloured by lift strength)</span>
                </h3>
                <ResponsiveContainer width="100%" height={340}>
                  <BarChart
                    data={[...(data.groups)].sort((a,b) => b.count - a.count).slice(0, 20).map(g => ({
                      name: g.items.join(" + "),
                      count: g.count,
                      size: g.items.length,
                      lift: g.lift,
                    }))}
                    layout="vertical" margin={{ top: 0, right: 60, left: 5, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 7 }} width={300}/>
                    <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "Invoices together"]}/>
                    <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                      {[...(data.groups)].sort((a,b) => b.count - a.count).slice(0, 20).map((g, i) => (
                        <Cell key={i} fill={LIFT_COLOR(g.lift)}/>
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="flex gap-4 mt-2 text-xs text-slate-400 flex-wrap">
                  <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-red-500"/>&nbsp;Lift ≥ 10×</span>
                  <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-orange-400"/>&nbsp;Lift 5–10×</span>
                  <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-yellow-400"/>&nbsp;Lift 2–5×</span>
                  <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-green-500"/>&nbsp;Lift 1–2×</span>
                </div>
              </div>

              {/* Full table with filters */}
              <div className="bg-white rounded-xl shadow-sm p-5">
                <div className="flex gap-3 mb-4 flex-wrap items-center">
                  <input
                    className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[220px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                    placeholder="Search material name…"
                    value={groupSearch} onChange={e => setGroupSearch(e.target.value)}
                  />
                  <div className="flex items-center gap-1">
                    {(["all", "2", "3", "4", "5+"] as SizeFilter[]).map(s => (
                      <button key={s} onClick={() => setSizeFilter(s)}
                        className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${sizeFilter === s ? "bg-brand-blue text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                        {s === "all" ? "All" : s === "2" ? "Pairs" : s === "3" ? "Triples" : s === "4" ? "Quads" : "5+ items"}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-1">
                    {(["count", "lift", "support"] as GroupSortKey[]).map(k => (
                      <button key={k} onClick={() => setGroupSort(k)}
                        className={`px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize ${groupSort === k ? "bg-slate-700 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                        ↓ {k}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">#</th>
                        <th className="py-2 pr-3 text-center">Size</th>
                        <th className="py-2 pr-4">Materials Sold Together</th>
                        <th className="py-2 pr-4 text-right">Invoice Count</th>
                        <th className="py-2 pr-4 text-right">Support</th>
                        <th className="py-2 text-right">Lift</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredGroups.map((g, i) => (
                        <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                          <td className="py-2 pr-3 text-xs text-slate-400">{i + 1}</td>
                          <td className="py-2 pr-3 text-center">
                            <span className={`text-xs px-2 py-0.5 rounded-full font-bold text-white ${g.items.length === 2 ? "bg-brand-blue" : g.items.length === 3 ? "bg-green-500" : g.items.length === 4 ? "bg-purple-500" : "bg-red-500"}`}>
                              {g.items.length}
                            </span>
                          </td>
                          <td className="py-2 pr-4">
                            <div className="flex flex-wrap gap-1">
                              {g.items.map(m => (
                                <span key={m} className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-medium border border-slate-200 truncate max-w-[200px]" title={m}>
                                  {m}
                                </span>
                              ))}
                            </div>
                          </td>
                          <td className="py-2 pr-4 text-right">
                            <div className="flex items-center justify-end gap-2">
                              <div className="w-14 bg-slate-100 rounded-full h-1.5">
                                <div className="bg-brand-blue h-1.5 rounded-full"
                                  style={{ width: `${Math.min((g.count / (data.groups[0]?.count ?? 1)) * 100, 100)}%` }}/>
                              </div>
                              <span className="font-bold text-slate-800 w-5 text-right">{g.count}</span>
                            </div>
                          </td>
                          <td className="py-2 pr-4 text-right text-xs text-slate-500">{pct(g.support)}</td>
                          <td className="py-2 text-right">
                            <span className="text-sm font-bold" style={{ color: LIFT_COLOR(Math.min(g.lift, 100)) }}>
                              {g.lift >= 1000 ? `${(g.lift/1000).toFixed(0)}K×` : g.lift >= 100 ? `${g.lift.toFixed(0)}×` : `${g.lift.toFixed(1)}×`}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {filteredGroups.length === 0 && (
                    <p className="text-center text-slate-400 text-sm py-6">No groups match the filter.</p>
                  )}
                </div>

                <div className="mt-4 pt-3 border-t border-slate-100 text-xs text-slate-400 space-y-1">
                  <p><b>Invoice Count</b>: number of invoices containing all materials in this group simultaneously</p>
                  <p><b>Support</b>: share of all invoices that contain this exact combination</p>
                  <p><b>Lift</b>: how much more often this group sells together vs. pure random chance (higher = stronger bundle). For 3+ items lift compounds — very high values are expected.</p>
                </div>
              </div>
            </div>
          )}

          {/* ── Invoices tab (all multi-item invoices with full item list) ── */}
          {activeTab === "invoices" && (
            <div className="space-y-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <div className="flex gap-3 mb-4">
                  <input
                    className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[220px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                    placeholder="Search payer or material…"
                    value={invSearch} onChange={e => setInvSearch(e.target.value)}
                  />
                  <span className="text-xs text-slate-400 self-center">
                    {data.large_invoices.length} invoices with 3+ items · sorted by size
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                        <th className="py-2 pr-3">Invoice</th>
                        <th className="py-2 pr-3">Date</th>
                        <th className="py-2 pr-4">Payer</th>
                        <th className="py-2 pr-3 text-center">Items</th>
                        <th className="py-2">Materials on Invoice</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.large_invoices as LargeInvoice[])
                        .filter(inv =>
                          !invSearch ||
                          inv.payer.toLowerCase().includes(invSearch.toLowerCase()) ||
                          inv.items.some(m => m.toLowerCase().includes(invSearch.toLowerCase()))
                        )
                        .map((inv, i) => (
                          <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50 align-top">
                            <td className="py-2 pr-3 font-mono text-xs text-slate-500">{inv.billing_document}</td>
                            <td className="py-2 pr-3 text-xs text-slate-500 whitespace-nowrap">{inv.billing_date}</td>
                            <td className="py-2 pr-4 text-xs text-slate-700 max-w-[160px] truncate" title={inv.payer}>{inv.payer}</td>
                            <td className="py-2 pr-3 text-center">
                              <span className={`text-xs px-2 py-0.5 rounded-full font-bold text-white ${inv.item_count <= 4 ? "bg-brand-blue" : inv.item_count <= 7 ? "bg-orange-500" : "bg-red-600"}`}>
                                {inv.item_count}
                              </span>
                            </td>
                            <td className="py-2">
                              <div className="flex flex-wrap gap-1">
                                {inv.items.map(m => (
                                  <span key={m} className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200" title={m}>
                                    {m.length > 35 ? m.slice(0, 33) + "…" : m}
                                  </span>
                                ))}
                              </div>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* ── Rules tab ── */}
          {activeTab === "rules" && (
            <div className="space-y-4">
              {data.total_rules === 0 ? (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 text-sm text-amber-700">
                  No rules found with the current thresholds. Try lowering Min Support or Min Confidence.
                </div>
              ) : (
                <>
                  {/* Lift distribution chart */}
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-3">Top 20 Rules by Lift</h3>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart
                        data={[...data.rules].sort((a,b) => b.lift - a.lift).slice(0, 20).map(r => ({
                          name: `${r.antecedents.join(", ")} → ${r.consequents.join(", ")}`,
                          lift: r.lift,
                          confidence: +(r.confidence * 100).toFixed(1),
                        }))}
                        layout="vertical" margin={{ top:0, right:60, left:5, bottom:0 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                        <XAxis type="number" tick={{ fontSize: 10 }} label={{ value: "Lift", position: "insideRight", offset: 10, fontSize: 10 }}/>
                        <YAxis type="category" dataKey="name" tick={{ fontSize: 8 }} width={220}/>
                        <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "Lift"]}/>
                        <Bar dataKey="lift" radius={[0,3,3,0]}>
                          {[...data.rules].sort((a,b) => b.lift - a.lift).slice(0, 20).map((r, i) => (
                            <Cell key={i} fill={LIFT_COLOR(r.lift)}/>
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>

                  {/* Rules table */}
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <div className="flex gap-3 mb-4 flex-wrap items-center">
                      <input
                        className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm flex-1 min-w-[220px] focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                        placeholder="Search material name…" value={search} onChange={e => setSearch(e.target.value)}
                      />
                      <div className="flex items-center gap-2 text-sm text-slate-500">
                        Sort by:
                        {(["lift","confidence","support"] as SortKey[]).map(k => (
                          <button key={k} onClick={() => setSortKey(k)}
                            className={`px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize ${sortKey===k ? "bg-brand-blue text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                            {k}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                            <th className="py-2 pr-4">If customer buys…</th>
                            <th className="py-2 pr-4">They also buy…</th>
                            <th className="py-2 pr-3 text-right">Support</th>
                            <th className="py-2 pr-3 text-right">Confidence</th>
                            <th className="py-2 pr-3 text-right">Lift</th>
                            <th className="py-2 text-right">Conviction</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((r, i) => (
                            <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                              <td className="py-2 pr-4 text-xs font-mono text-slate-700 max-w-[220px]">
                                {r.antecedents.join(" + ")}
                              </td>
                              <td className="py-2 pr-4 text-xs font-mono text-brand-blue max-w-[220px]">
                                {r.consequents.join(" + ")}
                              </td>
                              <td className="py-2 pr-3 text-right text-xs text-slate-500">{pct(r.support)}</td>
                              <td className="py-2 pr-3 text-right">
                                <span className="text-xs px-2 py-0.5 rounded-full font-semibold text-white"
                                  style={{ background: CONF_COLOR(r.confidence) }}>
                                  {pct(r.confidence)}
                                </span>
                              </td>
                              <td className="py-2 pr-3 text-right">
                                <span className="text-sm font-bold" style={{ color: LIFT_COLOR(r.lift) }}>
                                  {r.lift.toFixed(1)}×
                                </span>
                              </td>
                              <td className="py-2 text-right text-xs text-slate-500">
                                {r.conviction !== null ? r.conviction.toFixed(2) : "∞"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {filtered.length === 0 && (
                        <p className="text-center text-slate-400 text-sm py-6">No rules match the search.</p>
                      )}
                    </div>

                    {/* Legend */}
                    <div className="mt-4 pt-3 border-t border-slate-100 flex gap-6 text-xs text-slate-500 flex-wrap">
                      <span><b>Support</b>: fraction of baskets containing both antecedent and consequent</span>
                      <span><b>Confidence</b>: P(consequent | antecedent) — how often the rule is correct</span>
                      <span><b>Lift &gt; 1</b>: items co-occur more than by chance (higher = stronger association)</span>
                      <span><b>Conviction</b>: how much the rule would degrade without the association (∞ = perfect rule)</span>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ── Itemsets tab ── */}
          {activeTab === "itemsets" && (
            <div className="bg-white rounded-xl shadow-sm p-5">
              <h3 className="text-sm font-semibold text-slate-700 mb-1">Frequent Itemsets</h3>
              <p className="text-xs text-slate-400 mb-4">
                Combinations of parts that appear together in at least {pct(minSupport)} of invoices.
                Larger itemsets = stronger bundles.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-4">Items</th>
                      <th className="py-2 pr-4 text-right">Invoice Count</th>
                      <th className="py-2 text-right">Support</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.frequent_itemsets.map((fi, i) => (
                      <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-4">
                          <div className="flex flex-wrap gap-1">
                            {fi.items.map(m => (
                              <span key={m} className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-brand-blue font-medium border border-blue-100">
                                {m}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="py-2 pr-4 text-right font-semibold">{fi.count}</td>
                        <td className="py-2 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-20 bg-slate-100 rounded-full h-1.5">
                              <div className="bg-brand-blue h-1.5 rounded-full" style={{ width: `${Math.min(fi.support * 100 / (data.frequent_itemsets[0]?.support ?? 1) * 100, 100)}%` }}/>
                            </div>
                            <span className="text-xs font-medium text-slate-700 w-12 text-right">{pct(fi.support)}</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── Top Materials tab ── */}
          {activeTab === "materials" && (
            <div className="space-y-4">
              <div className="bg-white rounded-xl shadow-sm p-5">
                <h3 className="text-sm font-semibold text-slate-700 mb-3">Top 30 Materials by Invoice Frequency</h3>
                <ResponsiveContainer width="100%" height={320}>
                  <BarChart
                    data={data.top_materials.slice(0, 20).map(m => ({ name: m.material, count: m.count, support: +(m.support * 100).toFixed(1) }))}
                    layout="vertical" margin={{ top:0, right:60, left:5, bottom:0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false}/>
                    <XAxis type="number" tick={{ fontSize: 10 }}/>
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 9 }} width={220}/>
                    <Tooltip formatter={(v: unknown) => [Number(v).toLocaleString(), "Invoice count"]}/>
                    <Bar dataKey="count" fill="#4361EE" radius={[0,3,3,0]}/>
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl shadow-sm p-5 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                      <th className="py-2 pr-4">#</th>
                      <th className="py-2 pr-4">Material</th>
                      <th className="py-2 pr-4 text-right">Invoice Count</th>
                      <th className="py-2 text-right">Support</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_materials.map((m, i) => (
                      <tr key={m.material} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="py-2 pr-4 text-xs text-slate-400">{i + 1}</td>
                        <td className="py-2 pr-4 font-mono text-xs text-slate-700">{m.material}</td>
                        <td className="py-2 pr-4 text-right font-semibold">{m.count}</td>
                        <td className="py-2 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-20 bg-slate-100 rounded-full h-1.5">
                              <div className="bg-brand-blue h-1.5 rounded-full" style={{ width: `${Math.min(m.support / (data.top_materials[0]?.support ?? 1) * 100, 100)}%` }}/>
                            </div>
                            <span className="text-xs font-medium text-slate-700 w-12 text-right">{pct(m.support)}</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {/* ── ML Insights tab ── */}
          {activeTab === "ml" && (
            <div className="space-y-5">
              {mlLoading && (
                <div className="text-center text-slate-400 py-16">
                  <div className="text-2xl mb-2">🤖</div>
                  <div className="text-sm">Training Item2Vec + SVD models… ~30s</div>
                </div>
              )}

              {!mlLoading && !mlData && (
                <div className="text-center text-slate-400 py-12 text-sm">Loading ML models…</div>
              )}

              {mlData && (
                <>
                  {/* Model info KPIs */}
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <KpiCard label="Items Embedded"    value={String(mlData.model_info.n_items_trained)} sub="Item2Vec vocab"        color="blue"/>
                    <KpiCard label="Embedding Dim"     value={String(mlData.model_info.embedding_dim)}   sub="Word2Vec vector size"  color="purple"/>
                    <KpiCard label="SVD Components"    value={String(mlData.model_info.n_components_svd)} sub="Latent factors"       color="green"/>
                    <KpiCard label="Item Clusters"     value={String(mlData.model_info.n_clusters)}      sub="KMeans on embeddings"  color="amber"/>
                    <KpiCard label="Customer Recs"     value={String(mlData.customer_recommendations.length)} sub="payers with recs" color="teal"/>
                  </div>

                  {/* UMAP scatter */}
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-1">Item2Vec Embedding Map (UMAP 2D)</h3>
                    <p className="text-xs text-slate-400 mb-3">Items close together are bought together often. Colour = KMeans cluster.</p>
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                      <div className="lg:col-span-2">
                        <ResponsiveContainer width="100%" height={360}>
                          <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9"/>
                            <XAxis type="number" dataKey="x" name="UMAP-1" tick={{ fontSize: 9 }} tickLine={false}/>
                            <YAxis type="number" dataKey="y" name="UMAP-2" tick={{ fontSize: 9 }} tickLine={false}/>
                            <ZAxis type="number" dataKey="freq" range={[30, 300]} name="Frequency"/>
                            <Tooltip
                              cursor={{ strokeDasharray: "3 3" }}
                              content={({ active, payload }) => {
                                if (!active || !payload?.length) return null;
                                const d = payload[0].payload as { item: string; freq: number; cluster: number };
                                return (
                                  <div className="bg-white border border-slate-200 rounded-lg p-2 text-xs shadow">
                                    <div className="font-semibold text-slate-800 max-w-[200px]">{d.item}</div>
                                    <div className="text-slate-500">Cluster {d.cluster} · freq {d.freq}</div>
                                  </div>
                                );
                              }}
                            />
                            {mlData.clusters.map(c => (
                              <Scatter
                                key={c.cluster_id}
                                name={`Cluster ${c.cluster_id}`}
                                data={mlData.umap_coords.filter(p => p.cluster === c.cluster_id)}
                                fill={["#4361EE","#2CC56F","#FFC107","#EF4444","#7C3AED","#06B6D4","#F97316","#EC4899"][c.cluster_id % 8]}
                                fillOpacity={0.75}
                              />
                            ))}
                          </ScatterChart>
                        </ResponsiveContainer>
                      </div>
                      {/* Cluster legend */}
                      <div className="space-y-2 overflow-y-auto max-h-[360px]">
                        {mlData.clusters.map(c => (
                          <div key={c.cluster_id} className="bg-slate-50 rounded-lg p-2">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="w-3 h-3 rounded-full shrink-0" style={{ background: ["#4361EE","#2CC56F","#FFC107","#EF4444","#7C3AED","#06B6D4","#F97316","#EC4899"][c.cluster_id % 8] }}/>
                              <span className="text-xs font-semibold text-slate-700">Cluster {c.cluster_id} ({c.count} items)</span>
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {c.items.slice(0, 4).map(it => (
                                <span key={it} className="text-[10px] px-1.5 py-0.5 bg-white border border-slate-200 rounded text-slate-600 max-w-[120px] truncate" title={it}>{it}</span>
                              ))}
                              {c.items.length > 4 && <span className="text-[10px] text-slate-400">+{c.items.length - 4}</span>}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Item similarity explorer */}
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-3">Item Similarity Explorer</h3>
                    <div className="flex gap-3 mb-4 flex-wrap items-center">
                      <div className="flex-1 min-w-[220px]">
                        <input
                          className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                          placeholder="Search item…"
                          value={mlItemSearch}
                          onChange={e => setMlItemSearch(e.target.value)}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-[480px] overflow-y-auto">
                      {mlData.item_recommendations
                        .filter(r => !mlItemSearch || r.item.toLowerCase().includes(mlItemSearch.toLowerCase()))
                        .filter(r => r.item2vec.length > 0 || r.svd.length > 0)
                        .slice(0, 18)
                        .map(r => (
                          <div key={r.item} className="border border-slate-100 rounded-lg p-3 hover:border-brand-blue/30 transition-colors">
                            <div className="text-xs font-semibold text-slate-800 mb-1 truncate" title={r.item}>{r.item}</div>
                            <div className="text-[10px] text-slate-400 mb-2">freq: {r.freq}</div>
                            {r.item2vec.length > 0 && (
                              <div className="mb-2">
                                <div className="text-[10px] font-bold text-brand-blue uppercase tracking-wide mb-1">Item2Vec</div>
                                {r.item2vec.slice(0, 3).map(s => (
                                  <div key={s.item} className="flex items-center justify-between text-[10px] py-0.5">
                                    <span className="text-slate-600 truncate max-w-[130px]" title={s.item}>{s.item}</span>
                                    <span className="text-brand-blue font-mono shrink-0 ml-1">{(s.score * 100).toFixed(1)}%</span>
                                  </div>
                                ))}
                              </div>
                            )}
                            {r.svd.length > 0 && (
                              <div>
                                <div className="text-[10px] font-bold text-brand-green uppercase tracking-wide mb-1">SVD CF</div>
                                {r.svd.slice(0, 3).map(s => (
                                  <div key={s.item} className="flex items-center justify-between text-[10px] py-0.5">
                                    <span className="text-slate-600 truncate max-w-[130px]" title={s.item}>{s.item}</span>
                                    <span className="text-brand-green font-mono shrink-0 ml-1">{(s.score * 100).toFixed(1)}%</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                    </div>
                  </div>

                  {/* Customer recommendations */}
                  <div className="bg-white rounded-xl shadow-sm p-5">
                    <h3 className="text-sm font-semibold text-slate-700 mb-1">Customer Recommendations (SVD Collaborative Filtering)</h3>
                    <p className="text-xs text-slate-400 mb-3">Items predicted for each customer based on purchase history of similar customers.</p>
                    <input
                      className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                      placeholder="Search payer name…"
                      value={mlCustSearch}
                      onChange={e => setMlCustSearch(e.target.value)}
                    />
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase">
                            <th className="py-2 pr-4">Payer</th>
                            <th className="py-2 pr-4 text-center">Purchased</th>
                            <th className="py-2">Top Recommendations</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mlData.customer_recommendations
                            .filter(c => !mlCustSearch || c.payer.toLowerCase().includes(mlCustSearch.toLowerCase()))
                            .slice(0, 30)
                            .map((c, i) => (
                              <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50 align-top">
                                <td className="py-2 pr-4 text-xs text-slate-700 max-w-[180px] truncate" title={c.payer}>{c.payer}</td>
                                <td className="py-2 pr-4 text-center">
                                  <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 font-medium">{c.purchased.length}</span>
                                </td>
                                <td className="py-2">
                                  <div className="flex flex-wrap gap-1">
                                    {c.recommendations.slice(0, 3).map(r => (
                                      <span key={r.item} className="text-[10px] px-2 py-0.5 rounded-full bg-brand-blue/10 text-brand-blue border border-brand-blue/20 max-w-[160px] truncate" title={r.item}>
                                        {r.item.length > 30 ? r.item.slice(0, 28) + "…" : r.item}
                                        <span className="ml-1 text-brand-blue/60">({r.score.toFixed(2)})</span>
                                      </span>
                                    ))}
                                  </div>
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
