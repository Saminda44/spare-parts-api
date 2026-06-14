import { useCallback, useEffect, useRef, useState } from "react";
import {
  Search, X, Loader2, Database, RefreshCw,
  Play, CheckCircle2, AlertTriangle, Package,
} from "lucide-react";
import {
  fetchPartsFromCatalog, fetchExtractionStatus, runBatchExtraction,
  type CatalogDerivedPartsData, type CatalogDerivedPartRow,
  type ExtractionStatus,
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
      {list.map(m => (
        <span
          key={m}
          className="inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded text-white leading-tight whitespace-nowrap"
          style={{ background: modelBg(m) }}
        >
          {m}
        </span>
      ))}
    </div>
  );
}

// ── Build-index panel (shown when not indexed yet) ─────────────────────────────

function BuildIndexPanel({ onDone }: { onDone: () => void }) {
  const [status,   setStatus]   = useState<ExtractionStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(() => {
    fetchExtractionStatus().then(s => {
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
    await runBatchExtraction();
    setTimeout(loadStatus, 800);
    setStarting(false);
  };

  const running = status?.running || starting;

  return (
    <div className="flex flex-col items-center justify-center py-24 gap-5 text-center">
      <Database size={48} className="text-slate-200" />
      <div>
        <p className="text-base font-semibold text-slate-700">Catalogue index not built yet</p>
        <p className="text-sm text-slate-400 mt-1 max-w-sm">
          Extract all PDF catalogues to build the part master. This scans every
          PDF in the catalogue library and may take a few minutes.
        </p>
      </div>
      <button
        onClick={handleBuild}
        disabled={running}
        className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-brand-blue text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {running
          ? <><Loader2 size={15} className="animate-spin" /> Extracting…</>
          : <><Play size={15} /> Build Index from All PDFs</>}
      </button>
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
  const [data,      setData]      = useState<CatalogDerivedPartsData | null>(null);
  const [search,    setSearch]    = useState("");
  const [debSearch, setDebSearch] = useState("");
  const [modelFilter, setModelFilter] = useState("");
  const [loading,   setLoading]   = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
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

  // Client-side filtering (fast for ≤10k rows)
  const filtered: CatalogDerivedPartRow[] = (data?.rows ?? []).filter(r => {
    const q = debSearch.toLowerCase();
    const okSearch = !q
      || r.part_no.toLowerCase().includes(q)
      || r.description.toLowerCase().includes(q);
    const okModel = !modelFilter
      || r.compatible_models.toLowerCase().includes(modelFilter.toLowerCase());
    return okSearch && okModel;
  });

  // Rebuild flow
  const handleRebuild = async () => {
    setRebuilding(true);
    await runBatchExtraction();
    pollRef.current = setInterval(async () => {
      const s = await fetchExtractionStatus();
      if (!s.running) {
        clearInterval(pollRef.current!);
        setRebuilding(false);
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
            Derived from PDF catalogues · unique parts across all models
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
                ? `${data.total.toLocaleString()} unique part numbers across ${data.total_models} models · sourced from PDF catalogues`
                : "Loading…"}
            </p>
          </div>
          <button
            onClick={handleRebuild}
            disabled={rebuilding || loading}
            title="Re-extract all PDFs and rebuild the index"
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40 transition-colors"
          >
            {rebuilding
              ? <><Loader2 size={12} className="animate-spin" /> Rebuilding…</>
              : <><RefreshCw size={12} /> Rebuild Index</>}
          </button>
        </div>

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
            sub="catalogue folders indexed"
          />
          <Kpi
            label="Showing"
            value={loading ? "…" : filtered.length.toLocaleString()}
            sub="after current filters"
          />
          <Kpi
            label="Index Status"
            value={data?.indexed ? "Ready" : "Not built"}
            sub={data?.indexed ? "catalogue index up to date" : "click Rebuild Index"}
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
                    {["Part No.", "Description", "Compatible Models", "Sources"].map(h => (
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
                      <td className="py-2 px-3 border-b border-slate-100 text-slate-700 max-w-[280px]">
                        <span title={r.description}>{r.description || <span className="text-slate-300">—</span>}</span>
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100">
                        <ModelBadges models={r.compatible_models} />
                      </td>
                      <td className="py-2 px-3 border-b border-slate-100 text-center text-xs text-slate-400">
                        {r.source_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Index status footer */}
          {data?.indexed && (
            <div className="flex items-center gap-2 text-xs text-green-600">
              <CheckCircle2 size={12} />
              Index built · {data.total.toLocaleString()} unique parts from {data.total_models} model catalogues
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
