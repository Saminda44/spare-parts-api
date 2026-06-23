import { useCallback, useEffect, useRef, useState } from "react";
import {
  Search, X, Loader2, Database, RefreshCw,
  Play, CheckCircle2, AlertTriangle, Package, Layers,
} from "lucide-react";
import {
  fetchPartsFromCatalog, fetchPartMasterStatus, rebuildPartMaster,
  type CatalogDerivedPartsData, type CatalogDerivedPartRow,
  type PartMasterRebuildStatus,
} from "../api/client";

// ── KPI card ──────────────────────────────────────────────────────────────────

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm px-5 py-4 space-y-0.5">
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className="text-2xl font-bold text-slate-800 leading-tight">{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

// ── Model badge ───────────────────────────────────────────────────────────────

const MODEL_COLORS: Record<string, string> = {
  "FZ & FZS": "#4361EE", "R 15": "#EF4444", "RAY": "#F97316",
  "FAZER": "#7C3AED", "ALFA": "#06B6D4", "CRUX": "#FFC107",
  "SALUTO": "#10B981", "SZ": "#EC4899", "YBR": "#94A3B8",
  "FASINO": "#0EA5E9", "LIBERO": "#84CC16", "GLADIATOR": "#F59E0B",
  "MT": "#2CC56F", "AEROX": "#FF6B6B", "NMAX": "#9B59B6",
  "ENTICER": "#E67E22", "YBX": "#1ABC9C",
};
function modelBg(name: string) { return MODEL_COLORS[name.trim()] ?? "#64748B"; }

function ModelBadges({ models }: { models: string }) {
  if (!models) return <span className="text-slate-300 text-xs">—</span>;
  const list = models.split(", ").filter(Boolean);
  return (
    <div className="flex flex-wrap gap-1">
      {list.map(m => {
        // "AEROX B65J" → model="AEROX", variant="B65J"
        // "AEROX B65J/DBNM8" → model="AEROX", variant="B65J", colour="DBNM8"
        const [modelPart, colourPart] = m.split("/");
        const tokens = modelPart.trim().split(" ");
        const modelName = tokens[0];
        const variant   = tokens.slice(1).join(" ");
        return (
          <span
            key={m}
            className="inline-flex items-center gap-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded text-white leading-tight whitespace-nowrap"
            style={{ background: modelBg(modelName) }}
          >
            {modelName}
            {variant && <span className="opacity-80"> {variant}</span>}
            {colourPart && <span className="opacity-60 text-[9px]">/{colourPart}</span>}
          </span>
        );
      })}
    </div>
  );
}

// ── Build-index panel (empty state) ───────────────────────────────────────────

function BuildIndexPanel({ onDone }: { onDone: () => void }) {
  const [status,   setStatus]   = useState<PartMasterRebuildStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(() => {
    fetchPartMasterStatus().then(s => {
      setStatus(s);
      if (!s.running && s.last_result?.ok) onDone();
    });
  }, [onDone]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  useEffect(() => {
    if (status?.running) {
      pollRef.current = setInterval(loadStatus, 3000);
    } else {
      if (pollRef.current) clearInterval(pollRef.current);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [status?.running, loadStatus]);

  const handleBuild = async () => {
    setStarting(true);
    await rebuildPartMaster(true);
    setTimeout(loadStatus, 800);
    setStarting(false);
  };

  const running = status?.running || starting;

  return (
    <div className="flex flex-col items-center justify-center py-24 gap-5 text-center">
      <Database size={48} className="text-slate-200" />
      <div>
        <p className="text-base font-semibold text-slate-700">Part master not built yet</p>
        <p className="text-sm text-slate-400 mt-1 max-w-md">
          Reads every PDF catalogue through the AI agent (runs once, then cached),
          aggregates unique part numbers and their compatible models, and builds
          the cross-catalogue part master.
        </p>
      </div>
      <button
        onClick={handleBuild}
        disabled={running}
        className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-brand-blue text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {running
          ? <><Loader2 size={15} className="animate-spin" /> Building…</>
          : <><Play size={15} /> Build Part Master from All PDFs</>}
      </button>
      {status?.running && (
        <p className="text-xs text-slate-400">
          Running agent on unprocessed PDFs — this may take several minutes…
        </p>
      )}
      {status?.last_result && !status.last_result.ok && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-4 py-2">
          <AlertTriangle size={14} /> {status.last_result.error}
        </div>
      )}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export function PartMaster() {
  const [data,        setData]        = useState<CatalogDerivedPartsData | null>(null);
  const [search,      setSearch]      = useState("");
  const [debSearch,   setDebSearch]   = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [kindFilter,  setKindFilter]  = useState<"" | "shared" | "colour_specific">("");
  const [loading,     setLoading]     = useState(true);
  const [rebuilding,  setRebuilding]  = useState(false);
  const [rebuildStatus, setRebuildStatus] = useState<PartMasterRebuildStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchPartsFromCatalog()
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  // Client-side filtering
  const filtered: CatalogDerivedPartRow[] = (data?.rows ?? []).filter(r => {
    const q = debSearch.toLowerCase();
    const okSearch = !q
      || r.part_no.toLowerCase().includes(q)
      || r.description.toLowerCase().includes(q);
    const okModel = !modelFilter
      || r.compatible_models.toLowerCase().includes(modelFilter.toLowerCase());
    const okKind = !kindFilter || r.kind === kindFilter;
    return okSearch && okModel && okKind;
  });

  // Rebuild flow — reads agent builds and aggregates
  const handleRebuild = async () => {
    setRebuilding(true);
    await rebuildPartMaster(true);
    pollRef.current = setInterval(async () => {
      const s = await fetchPartMasterStatus();
      setRebuildStatus(s);
      if (!s.running) {
        clearInterval(pollRef.current!);
        setRebuilding(false);
        setRebuildStatus(null);
        load();
      }
    }, 3000);
  };
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // ── Not indexed yet ──────────────────────────────────────────────────────────
  if (!loading && data && !data.indexed) {
    return (
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="space-y-2 mb-6">
          <h2 className="text-xl font-bold text-slate-800">Part Master</h2>
          <p className="text-xs text-slate-500">
            Derived from PDF catalogues via AI agent · unique parts across all models
          </p>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-8">
          <BuildIndexPanel onDone={load} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 overflow-y-auto">
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-slate-800">Part Master</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {data
                ? `${data.total.toLocaleString()} unique part numbers · ${data.total_models} models · ${data.agent_master ? "AI-agent catalogue index" : "legacy index"}`
                : "Loading…"}
            </p>
          </div>
          <button
            onClick={handleRebuild}
            disabled={rebuilding || loading}
            title="Run agent on all unprocessed PDFs then rebuild the part master"
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40 transition-colors"
          >
            {rebuilding
              ? <><Loader2 size={12} className="animate-spin" /> Rebuilding…</>
              : <><RefreshCw size={12} /> Rebuild from All PDFs</>}
          </button>
        </div>

        {/* Rebuild progress banner */}
        {rebuilding && rebuildStatus && (
          <div className="flex items-center gap-3 px-4 py-3 bg-blue-50 border border-blue-100 rounded-xl text-sm text-blue-700">
            <Loader2 size={15} className="animate-spin shrink-0" />
            <span>
              Processing PDFs — {rebuildStatus.cached_pdfs} cached so far.
              {rebuildStatus.last_result?.agents_run != null &&
                ` ${rebuildStatus.last_result.agents_run} new agents run.`}
            </span>
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Kpi
            label="Unique Parts"
            value={loading ? "…" : (data?.total ?? 0).toLocaleString()}
            sub="distinct part numbers"
          />
          <Kpi
            label="Models Covered"
            value={loading ? "…" : (data?.total_models ?? 0).toString()}
            sub="catalogue models indexed"
          />
          <Kpi
            label="Showing"
            value={loading ? "…" : filtered.length.toLocaleString()}
            sub="after current filters"
          />
          <Kpi
            label="Index"
            value={data?.agent_master ? "AI Agent" : data?.indexed ? "Legacy" : "Not built"}
            sub={data?.agent_master ? "agent-derived, full variant detail" : data?.indexed ? "basic extraction" : "click Rebuild"}
          />
        </div>

        {/* Table card */}
        <div className="bg-white rounded-xl shadow-sm p-5 space-y-4">
          {/* Filter bar */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative flex-1 min-w-[220px]">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                className="w-full pl-8 pr-7 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
                placeholder="Search part no. or description…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              {search && (
                <button onClick={() => setSearch("")}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500">
                  <X size={12} />
                </button>
              )}
            </div>

            <select
              value={modelFilter}
              onChange={e => setModelFilter(e.target.value)}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
            >
              <option value="">All Models</option>
              {(data?.models ?? []).map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>

            <select
              value={kindFilter}
              onChange={e => setKindFilter(e.target.value as "" | "shared" | "colour_specific")}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
            >
              <option value="">All Types</option>
              <option value="shared">Shared (universal)</option>
              <option value="colour_specific">Colour-specific</option>
            </select>

            <span className="text-xs text-slate-400 ml-auto">
              {filtered.length.toLocaleString()} part{filtered.length !== 1 ? "s" : ""}
            </span>
          </div>

          {/* Table */}
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3 text-slate-400">
              <Loader2 size={28} className="animate-spin" />
              <p className="text-sm">Loading part master…</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2 text-slate-400">
              <Package size={36} className="text-slate-200" />
              <p className="text-sm font-medium">No parts match the current filter</p>
            </div>
          ) : (
            <div className="overflow-auto rounded-xl border border-slate-200 shadow-sm" style={{ maxHeight: "65vh" }}>
              <table className="w-full text-sm border-collapse">
                <thead className="sticky top-0 z-10">
                  <tr style={{ background: "#1B3A6B" }}>
                    {["Part No.", "Description", "Kind", "Compatible Models", "Variants"].map(h => (
                      <th key={h}
                        className="py-2.5 px-3 text-left text-xs font-bold text-white whitespace-nowrap border-r border-blue-800 last:border-r-0">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => (
                    <tr key={r.part_no} className={i % 2 === 0 ? "bg-white" : "bg-slate-50/60"}>
                      <td className="py-2 px-3 border-b border-slate-100 font-mono text-xs text-slate-700 whitespace-nowrap">
                        {r.part_no}
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100 text-slate-700 max-w-[300px]">
                        <span title={r.description}>{r.description || <span className="text-slate-300">—</span>}</span>
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100 whitespace-nowrap">
                        {r.kind === "colour_specific" ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-amber-100 text-amber-700">Colour</span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-emerald-100 text-emerald-700">Shared</span>
                        )}
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100">
                        <ModelBadges models={r.compatible_models} />
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100 text-center text-xs text-slate-400">
                        {r.variant_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Footer */}
          {data?.indexed && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              {data.agent_master
                ? <><CheckCircle2 size={12} className="text-emerald-500" />
                    AI-agent part master · {data.total.toLocaleString()} unique parts from {data.total_models} models</>
                : <><Layers size={12} className="text-slate-300" />
                    Legacy extraction index · rebuild to get full variant detail</>
              }
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
