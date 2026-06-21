import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileText, ArrowLeft, Search, X, Loader2,
  CheckCircle2, AlertTriangle, Upload,
} from "lucide-react";
import {
  fetchCatalog, fetchPdfTables, catalogFileUrl,
  fetchCatalogFolders, uploadCatalogPdf,
  fetchAgentBuilds,
  type CatalogData, type CatalogModel, type PdfTableResult, type ColourCode,
  type AgentResult, type AgentBuild,
} from "../api/client";

const MODEL_COLORS: Record<string, string> = {
  "FZ & FZS": "#4361EE", "R 15": "#EF4444", "New Model 2025": "#2CC56F",
  "RAY": "#F97316", "FAZER": "#7C3AED", "ALFA": "#06B6D4", "CRUX": "#FFC107",
  "SALUTO": "#10B981", "SZ": "#EC4899", "YBR": "#94A3B8", "FASINO": "#0EA5E9",
  "LIBERO": "#84CC16", "GLADIATOR": "#F59E0B",
  "NEW MOTORCYCLE & SCOOTER": "#6366F1",
};
function modelColor(name: string) { return MODEL_COLORS[name] ?? "#4361EE"; }

// ── Shared catalogue table ─────────────────────────────────────────────────────

interface CatalogueData {
  headers: string[];
  rows: string[][];
  total: number;
  sections: string[];
}

/** Pick the correct qty from a "/" -separated multi-variant string. */
function variantQty(qty: string, varIdx: number, numVariants: number): string {
  const parts = qty.split("/");
  if (parts.length === 1) return qty;
  const offset = Math.max(0, parts.length - numVariants);
  return parts[offset + varIdx] ?? qty;
}

function CatalogueTable({ data, relPath, pdfUrl, meta, variants, colourCodes, availableColours, manufactureYear }: {
  data: CatalogueData;
  relPath: string;
  pdfUrl?: string;
  meta?: { pages: number; sections: number; ocr: number; warnings: string[] };
  variants?: string[];
  colourCodes?: ColourCode[];
  availableColours?: string[];
  manufactureYear?: string;
}) {
  const [section,    setSection]    = useState("");
  const [search,     setSearch]     = useState("");
  const [debSearch,  setDebSearch]  = useState("");
  const [selVariant, setSelVariant] = useState<number>(0);
  const [selColour,  setSelColour]  = useState<string>("");
  // Sub-toggle when colour selected: "build" = agent-assembled, "extracted" = raw remarks-filter
  const [colourView, setColourView] = useState<"build" | "extracted">("build");

  // Agent state — loaded lazily on first colour selection, persists for the PDF lifetime
  const [agentResult,  setAgentResult]  = useState<AgentResult | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentError,   setAgentError]   = useState<string | null>(null);
  const agentFetching = useRef(false);

  useEffect(() => {
    const t = setTimeout(() => setDebSearch(search), 200);
    return () => clearTimeout(t);
  }, [search]);

  // Reset everything when the PDF changes
  useEffect(() => {
    setSection(""); setSearch(""); setSelVariant(0); setSelColour(""); setColourView("build");
    setAgentResult(null); setAgentLoading(false); setAgentError(null);
    agentFetching.current = false;
  }, [data]);

  // Pre-fetch agent immediately when a PDF with colour codes is opened so the
  // colour strip can show validated colours without waiting for a click.
  useEffect(() => {
    if (!colourCodes || colourCodes.length === 0) return;
    if (agentResult || agentFetching.current || agentLoading) return;
    agentFetching.current = true;
    setAgentLoading(true);
    setAgentError(null);
    fetchAgentBuilds(relPath)
      .then(d  => { setAgentResult(d); setAgentLoading(false); })
      .catch(e => { setAgentError(e?.message ?? "Agent error"); setAgentLoading(false); agentFetching.current = false; });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relPath]);

  const numVariants    = variants?.length ?? 0;
  const selVariantCode = variants?.[selVariant] ?? "";

  // Clear selected colour when switching variants if it's not in the new variant's roster
  useEffect(() => {
    if (!selColour || !agentResult || !selVariantCode) return;
    const roster = agentResult.rosters?.[selVariantCode];
    if (roster && !roster.includes(selColour)) {
      setSelColour("");
      setSection("");
    }
  }, [selVariantCode, agentResult, selColour]);

  // The assembled build for the active (variant, colour) selection
  const agentBuild: AgentBuild | undefined =
    agentResult && selColour
      ? agentResult.builds.find(
          b => b.variant === selVariantCode && b.colour === selColour.toUpperCase(),
        )
      : undefined;

  // Active colour name for display
  const selColourName = colourCodes?.find(c => c.abbreviation === selColour)?.name ?? selColour;

  // ── Rows for "Complete Build" mode ────────────────────────────────────────
  const buildRows: string[][] = agentBuild
    ? agentBuild.parts
        .filter(p => {
          const q = debSearch.toLowerCase();
          return (
            (!section || p.figure === section) &&
            (!q || p.part_no.toLowerCase().includes(q) || p.description.toLowerCase().includes(q))
          );
        })
        // element[6] = kind ("shared" | "colour_specific") — used for row styling
        .map(p => [p.figure, p.ref_no, p.part_no, p.description, p.qty, p.remarks, p.kind])
    : [];

  // ── Rows for "Extracted Table" mode (raw remarks-filter) ─────────────────
  const extractedRows: string[][] = data.rows
    .filter(row => {
      const okSection = !section || row[0] === section;
      const q = debSearch.toLowerCase();
      const okSearch = !q || row[2]?.toLowerCase().includes(q) || row[3]?.toLowerCase().includes(q);
      return okSection && okSearch;
    })
    .flatMap(row => {
      if (numVariants === 0) return [row];
      const origQty = row[4] ?? "";
      const vQty = variantQty(origQty, selVariant, numVariants);
      if (origQty.includes("/") && vQty === "") return [];
      return [[...row.slice(0, 4), vQty, ...row.slice(5)]];
    });

  // Which rows to show in the table
  const isColourSelected = !!selColour;
  const isBuildReady     = !!(agentBuild && colourView === "build");
  const displayRows      = isBuildReady ? buildRows : extractedRows;

  // Section options
  const sectionOptions: string[] = isBuildReady && agentBuild
    ? [...new Set(agentBuild.parts.map(p => p.figure).filter(Boolean))].sort()
    : data.sections;

  const internalCount = agentBuild ? agentBuild.parts.filter(p => p.kind === "shared").length : 0;
  const externalCount = agentBuild ? agentBuild.parts.filter(p => p.kind === "colour_specific").length : 0;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-3">
      {/* Extraction meta */}
      {meta && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
          <span className="font-medium text-slate-700">{data.total.toLocaleString()} parts extracted</span>
          <span>·</span><span>{meta.sections} sections</span>
          <span>·</span><span>{meta.pages} pages scanned</span>
          {manufactureYear && (
            <><span>·</span>
            <span className="font-medium text-blue-600">{manufactureYear} model</span></>
          )}
          {meta.ocr > 0 && (
            <><span>·</span>
            <span className="text-amber-600 flex items-center gap-1">
              <AlertTriangle size={11} /> {meta.ocr} image-only pages
            </span></>
          )}
          {meta.warnings.length > 0 && (
            <><span>·</span><span className="text-amber-600">{meta.warnings[0]}</span></>
          )}
        </div>
      )}

      {/* Available colours strip — from the PDF's "AVAILABLE COLOUR" page */}
      {availableColours && availableColours.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 bg-emerald-50 border border-emerald-100 rounded-lg px-3 py-2">
          <span className="text-xs font-semibold text-emerald-700 shrink-0">Available colours:</span>
          {availableColours.map(name => (
            <span key={name} className="text-xs px-2.5 py-1 rounded-full font-medium bg-white text-emerald-700 border border-emerald-200">
              {name}
            </span>
          ))}
          <span className="text-[10px] text-emerald-500 ml-auto italic">from PDF</span>
        </div>
      )}

      {/* Variant strip */}
      {variants && variants.length === 1 && (
        <div className="flex flex-wrap items-center gap-2 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
          <span className="text-xs font-semibold text-blue-700 shrink-0">Model variant:</span>
          <span className="text-xs px-2.5 py-1 rounded-full font-medium bg-blue-700 text-white select-none">{variants[0]}</span>
        </div>
      )}
      {variants && variants.length >= 2 && (
        <div className="flex flex-wrap items-center gap-2 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
          <span className="text-xs font-semibold text-blue-700 shrink-0">Model variant:</span>
          {variants.map((v, i) => (
            <button key={v} onClick={() => { setSelVariant(i); setSection(""); }}
              className={`text-xs px-2.5 py-1 rounded-full font-medium transition-colors ${selVariant === i ? "bg-blue-700 text-white" : "bg-white text-blue-700 border border-blue-200 hover:bg-blue-100"}`}>
              {v}
            </button>
          ))}
        </div>
      )}

      {/* Colour variant strip — filtered to: web-confirmed + has external parts + in this variant's roster */}
      {colourCodes && colourCodes.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
          <span className="text-xs font-semibold text-amber-700 shrink-0">
            Colour variant{manufactureYear ? ` (${manufactureYear})` : ""}:
          </span>
          {agentLoading && !agentResult ? (
            <span className="flex items-center gap-1.5 text-xs text-amber-600 italic">
              <Loader2 size={11} className="animate-spin" /> Identifying colour variants…
            </span>
          ) : (() => {
            // validated_colours: web-confirmed AND has ≥1 external part
            const validatedSet: Set<string> | null = agentResult?.validated_colours?.length
              ? new Set(agentResult.validated_colours)
              : null;
            // roster for the currently selected model variant
            const rosterSet: Set<string> | null =
              agentResult && selVariantCode && agentResult.rosters?.[selVariantCode]?.length
                ? new Set(agentResult.rosters[selVariantCode])
                : null;
            const visibleCodes = colourCodes.filter(c => {
              const inValidated = !validatedSet || validatedSet.has(c.abbreviation);
              const inRoster    = !rosterSet    || rosterSet.has(c.abbreviation);
              return inValidated && inRoster;
            });
            return (
              <>
                <button
                  onClick={() => { setSelColour(""); setSection(""); }}
                  className={`text-xs px-2.5 py-1 rounded-full font-medium transition-colors ${!selColour ? "bg-amber-600 text-white" : "bg-white text-amber-700 border border-amber-200 hover:bg-amber-100"}`}>
                  All
                </button>
                {visibleCodes.map(c => (
                  <button
                    key={c.abbreviation}
                    onClick={() => { setSelColour(selColour === c.abbreviation ? "" : c.abbreviation); setSection(""); setColourView("build"); }}
                    title={`${c.abbreviation} — paint code ${c.code}`}
                    className={`text-xs px-2.5 py-1 rounded-full font-medium transition-colors flex items-center gap-1 ${
                      selColour === c.abbreviation ? "bg-amber-600 text-white" : "bg-white text-amber-700 border border-amber-200 hover:bg-amber-100"
                    }`}>
                    {c.name}
                    {c.is_model_colour && (
                      <span className={`text-[9px] font-bold ${selColour === c.abbreviation ? "text-amber-200" : "text-amber-500"}`}>★</span>
                    )}
                  </button>
                ))}
                {agentResult && visibleCodes.length === 0 && (
                  <span className="text-xs text-amber-500 italic">No colour variants for this model</span>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* Sub-toggle (only when a colour is selected) */}
      {isColourSelected && (
        <div className="flex items-center gap-2">
          <div className="flex gap-0.5 bg-slate-100 rounded-lg p-0.5">
            <button
              onClick={() => { setColourView("build"); setSection(""); }}
              className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${colourView === "build" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              Complete Build
            </button>
            <button
              onClick={() => { setColourView("extracted"); setSection(""); }}
              className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${colourView === "extracted" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              Extracted Table
            </button>
          </div>
          {colourView === "build" && agentLoading && (
            <span className="flex items-center gap-1.5 text-xs text-amber-600">
              <Loader2 size={11} className="animate-spin" /> Assembling — cached after first run…
            </span>
          )}
          {colourView === "build" && agentError && (
            <span className="flex items-center gap-1.5 text-xs text-red-500">
              <AlertTriangle size={11} /> {agentError}
              <button onClick={() => { agentFetching.current = false; setAgentError(null); setAgentLoading(false); setAgentResult(null); }}
                className="underline ml-1">Retry</button>
            </span>
          )}
        </div>
      )}

      {/* Build summary banner */}
      {isBuildReady && (
        <div className="flex items-center gap-2 text-xs text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-lg px-3 py-2">
          <CheckCircle2 size={12} />
          Complete catalogue for&nbsp;
          <strong>{selColourName}{manufactureYear ? ` (${manufactureYear})` : ""}</strong>
          &nbsp;—&nbsp;variant&nbsp;<strong>{selVariantCode}</strong>
          &nbsp;·&nbsp;
          <span className="text-emerald-700 font-medium">{internalCount} internal</span>
          &nbsp;+&nbsp;
          <span className="text-amber-600 font-medium">{externalCount} external</span>
          &nbsp;=&nbsp;
          <strong>{agentBuild!.part_count} total parts</strong>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-slate-500">Section:</span>
          <select
            value={section} onChange={e => setSection(e.target.value)}
            className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
          >
            <option value="">
              {isBuildReady ? `All Sections (${agentBuild!.part_count})` : `All Sections (${data.total})`}
            </option>
            {sectionOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className="pl-8 pr-7 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-blue/30 w-60"
            placeholder="Search part no. or description…"
            value={search} onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button onClick={() => setSearch("")}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500">
              <X size={12} />
            </button>
          )}
        </div>
        <span className="text-xs text-slate-400 ml-auto">
          {displayRows.length.toLocaleString()} rows
        </span>
        {pdfUrl && (
          <a href={pdfUrl} target="_blank" rel="noreferrer"
            className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors">
            <FileText size={12} /> Open PDF
          </a>
        )}
      </div>

      {/* Parts table */}
      <div className="overflow-auto rounded-xl border border-slate-200 shadow-sm" style={{ maxHeight: "65vh" }}>
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 z-10">
            <tr style={{ background: "#1B3A6B" }}>
              {[...data.headers, ...(isBuildReady ? ["Part type"] : [])].map((h, i) => (
                <th key={i} className="py-2.5 px-3 text-left text-xs font-bold text-white whitespace-nowrap border-r border-blue-800 last:border-r-0">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {agentLoading && colourView === "build" ? (
              <tr>
                <td colSpan={data.headers.length + 1} className="py-16 text-center">
                  <div className="flex flex-col items-center gap-3 text-slate-400">
                    <Loader2 size={22} className="animate-spin" />
                    <p className="text-sm">Assembling complete parts list…</p>
                    <p className="text-xs text-slate-300">Runs once then caches — subsequent colour switches are instant</p>
                  </div>
                </td>
              </tr>
            ) : (
              <>
                {displayRows.map((row, ri) => {
                  const isFirstInSection = ri === 0 || row[0] !== displayRows[ri - 1][0];
                  const kind = row[6]; // "shared" | "colour_specific" — only in build mode
                  const rowBg = kind === "shared"
                    ? (ri % 2 === 0 ? "bg-emerald-50/50" : "bg-emerald-50/80")
                    : kind === "colour_specific"
                      ? (ri % 2 === 0 ? "bg-amber-50/50" : "bg-amber-50/80")
                      : (ri % 2 === 0 ? "bg-white" : "bg-slate-50/60");
                  return (
                    <tr key={ri} className={rowBg}>
                      {row.slice(0, 6).map((cell, ci) => (
                        <td key={ci}
                          className={`py-2 px-3 border-b border-slate-100 whitespace-nowrap
                            ${ci === 0 && isFirstInSection ? "font-semibold text-slate-800" : ""}
                            ${ci === 0 && !isFirstInSection ? "text-slate-300" : ""}
                            ${ci === 2 ? "font-mono text-xs text-slate-700" : ""}
                            ${ci === 4 ? "text-center font-semibold text-brand-blue" : ""}
                            ${ci === 5 ? "text-slate-400 text-xs" : ""}
                          `}>
                          {ci === 0 && !isFirstInSection ? "" : cell}
                        </td>
                      ))}
                      {isBuildReady && (
                        <td className="py-2 px-3 border-b border-slate-100">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${kind === "shared" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                            {kind === "shared" ? "Internal" : "External"}
                          </span>
                        </td>
                      )}
                    </tr>
                  );
                })}
                {displayRows.length === 0 && (
                  <tr>
                    <td colSpan={data.headers.length + (isBuildReady ? 1 : 0)} className="py-12 text-center text-slate-400 text-sm">
                      No rows match the current filter
                    </td>
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Shared states ──────────────────────────────────────────────────────────────

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-3 text-slate-400">
      <Loader2 size={28} className="animate-spin" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

function EmptyState({ message }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-2 text-slate-400">
      <FileText size={36} className="text-slate-200" />
      <p className="text-sm font-medium">No table data found</p>
      <p className="text-xs text-slate-300">{message ?? "This PDF may be image-only or contain no structured tables"}</p>
    </div>
  );
}

// ── Breadcrumb ─────────────────────────────────────────────────────────────────

function Breadcrumb({ onBack, backLabel = "All Catalogues", label, icon }: {
  onBack: () => void; backLabel?: string; label: string; icon: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3">
      <button onClick={onBack}
        className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors">
        <ArrowLeft size={15} /> {backLabel}
      </button>
      <span className="text-slate-300">/</span>
      <div className="flex items-center gap-2">
        {icon}
        <span className="font-semibold text-slate-800">{label}</span>
      </div>
    </div>
  );
}

// ── PDF catalogue viewer ───────────────────────────────────────────────────────

function PdfCatalogueViewer({ relPath, filename, pdfUrl, onBack }: {
  relPath: string; filename: string; pdfUrl: string; onBack: () => void;
}) {
  const [result,  setResult]  = useState<PdfTableResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchPdfTables(relPath)
      .then(d => { setResult(d); setLoading(false); })
      .catch(() => { setResult(null); setLoading(false); });
  }, [relPath]);

  const label = filename.replace(/\.pdf$/i, "");
  const meta = result ? {
    pages: result.pages_scanned,
    sections: result.sections_found,
    ocr: result.ocr_flagged,
    warnings: result.warnings,
  } : undefined;

  return (
    <div className="space-y-4">
      <Breadcrumb onBack={onBack} label={label} icon={<FileText size={14} className="text-red-400" />} />
      {loading ? <LoadingState label="Extracting parts from PDF…" /> :
       !result || result.headers.length === 0 ? <EmptyState /> :
       <CatalogueTable
         data={result} relPath={relPath} pdfUrl={pdfUrl} meta={meta}
         variants={result.variants} colourCodes={result.colour_codes}
         availableColours={result.available_colours}
         manufactureYear={result.manufacture_year}
       />}
    </div>
  );
}

// ── Extraction modal (used by upload panel) ────────────────────────────────────

function ExtractionModal({ relPath, filename, pdfUrl, onClose }: {
  relPath: string; filename: string; pdfUrl: string; onClose: () => void;
}) {
  const [result,  setResult]  = useState<PdfTableResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchPdfTables(relPath)
      .then(d => { setResult(d); setLoading(false); })
      .catch(() => { setLoading(false); });
  }, [relPath]);

  const meta = result ? {
    pages: result.pages_scanned,
    sections: result.sections_found,
    ocr: result.ocr_flagged,
    warnings: result.warnings,
  } : undefined;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col">
        {/* Modal header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100 shrink-0">
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-red-400" />
            <span className="font-semibold text-slate-800 text-sm">
              {filename.replace(/\.pdf$/i, "")}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <a href={pdfUrl} target="_blank" rel="noreferrer"
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors">
              <FileText size={12} /> Open PDF
            </a>
            <button onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>
        {/* Modal body */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading ? <LoadingState label="Extracting parts from PDF…" /> :
           !result || result.headers.length === 0 ? <EmptyState /> :
           <CatalogueTable data={result} relPath={relPath} meta={meta} variants={result.variants} colourCodes={result.colour_codes} availableColours={result.available_colours} manufactureYear={result.manufacture_year} />}
        </div>
      </div>
    </div>
  );
}

// ── Upload PDF panel ───────────────────────────────────────────────────────────

type UploadEntry = {
  file: File;
  status: "idle" | "uploading" | "done" | "error";
  relPath?: string;
  error?: string;
};

function UploadPanel({ onCatalogRefresh }: { onCatalogRefresh: () => void }) {
  const [folders,    setFolders]    = useState<string[]>([]);
  const [entries,    setEntries]    = useState<UploadEntry[]>([]);
  const [folder,     setFolder]     = useState("");
  const [newName,    setNewName]    = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [uploading,  setUploading]  = useState(false);
  const [viewFile,   setViewFile]   = useState<{ relPath: string; filename: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchCatalogFolders().then(fs => {
      setFolders(fs);
      if (fs.length > 0) setFolder(fs[0]);
    });
  }, []);

  const effectiveFolder = folder === "__new__" ? newName.trim() : folder;
  const canUpload = entries.length > 0 && effectiveFolder.length > 0 && !uploading;

  const addFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    const pdfs = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith(".pdf"));
    setEntries(prev => [...prev, ...pdfs.map(f => ({ file: f, status: "idle" as const }))]);
  };

  const removeEntry = (i: number) =>
    setEntries(prev => prev.filter((_, idx) => idx !== i));

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const handleUpload = async () => {
    setUploading(true);
    for (let i = 0; i < entries.length; i++) {
      if (entries[i].status === "done") continue;
      setEntries(prev => prev.map((e, idx) => idx === i ? { ...e, status: "uploading" } : e));
      try {
        const res = await uploadCatalogPdf(entries[i].file, effectiveFolder);
        setEntries(prev => prev.map((e, idx) =>
          idx === i ? { ...e, status: "done", relPath: res.rel_path } : e));
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Upload failed";
        setEntries(prev => prev.map((e, idx) =>
          idx === i ? { ...e, status: "error", error: msg } : e));
      }
    }
    setUploading(false);
    onCatalogRefresh();
    // refresh folder list in case a new folder was created
    fetchCatalogFolders().then(setFolders);
  };

  return (
    <>
      {viewFile && (
        <ExtractionModal
          relPath={viewFile.relPath}
          filename={viewFile.filename}
          pdfUrl={catalogFileUrl(viewFile.relPath)}
          onClose={() => setViewFile(null)}
        />
      )}

      <div className="space-y-5">
        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors select-none
            ${isDragging
              ? "border-brand-blue bg-blue-50"
              : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"}`}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".pdf"
            multiple
            className="hidden"
            onChange={e => { addFiles(e.target.files); e.target.value = ""; }}
          />
          <Upload size={28} className={`mx-auto mb-2 ${isDragging ? "text-brand-blue" : "text-slate-300"}`} />
          <p className="text-sm font-medium text-slate-600">Drop PDF files here or click to browse</p>
          <p className="text-xs text-slate-400 mt-1">Only .pdf files are accepted · multiple files supported</p>
        </div>

        {/* Selected files list */}
        {entries.length > 0 && (
          <div className="space-y-1.5">
            {entries.map((e, i) => (
              <div key={i}
                className="flex items-center gap-3 px-3 py-2 rounded-lg bg-slate-50 border border-slate-100">
                <FileText size={14} className="text-red-400 shrink-0" />
                <span className="text-sm text-slate-700 flex-1 truncate">{e.file.name}</span>
                <span className="text-xs text-slate-400 shrink-0">
                  {(e.file.size / 1024).toFixed(0)} KB
                </span>

                {e.status === "idle" && (
                  <button onClick={() => removeEntry(i)}
                    className="text-slate-300 hover:text-slate-500 transition-colors">
                    <X size={13} />
                  </button>
                )}
                {e.status === "uploading" && (
                  <Loader2 size={14} className="animate-spin text-brand-blue shrink-0" />
                )}
                {e.status === "done" && (
                  <>
                    <CheckCircle2 size={14} className="text-green-500 shrink-0" />
                    <button
                      onClick={() => setViewFile({ relPath: e.relPath!, filename: e.file.name })}
                      className="text-xs px-2.5 py-1 rounded-lg bg-brand-blue text-white hover:bg-blue-700 transition-colors shrink-0">
                      Extract
                    </button>
                  </>
                )}
                {e.status === "error" && (
                  <span className="text-xs text-red-500 truncate max-w-[200px]" title={e.error}>
                    {e.error}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Folder selector + upload button */}
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex-1 min-w-48">
            <label className="block text-xs font-medium text-slate-500 mb-1.5">Save to folder</label>
            <select
              value={folder}
              onChange={e => setFolder(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30 bg-white"
            >
              <option value="" disabled>Select folder…</option>
              {folders.map(f => <option key={f} value={f}>{f}</option>)}
              <option value="__new__">+ Create new folder…</option>
            </select>
          </div>

          {folder === "__new__" && (
            <div className="flex-1 min-w-48">
              <label className="block text-xs font-medium text-slate-500 mb-1.5">New folder name</label>
              <input
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="e.g. NMAX"
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
              />
            </div>
          )}

          <button
            onClick={handleUpload}
            disabled={!canUpload}
            className="flex items-center gap-2 px-5 py-2 text-sm rounded-lg bg-brand-blue text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-medium"
          >
            {uploading
              ? <><Loader2 size={14} className="animate-spin" /> Uploading…</>
              : <><Upload size={14} /> Upload</>}
          </button>
        </div>
      </div>
    </>
  );
}

// ── Model detail: file list → pick a file → PDF viewer ────────────────────────

function ModelDetail({ model, onBack }: { model: CatalogModel; onBack: () => void }) {
  const [selected, setSelected] = useState<{ relPath: string; filename: string; url: string } | null>(null);

  if (selected) {
    return (
      <PdfCatalogueViewer
        relPath={selected.relPath}
        filename={selected.filename}
        pdfUrl={selected.url}
        onBack={() => setSelected(null)}
      />
    );
  }

  const color = modelColor(model.model);
  return (
    <div className="space-y-4">
      <Breadcrumb
        onBack={onBack} backLabel="PDF Catalogues"
        label={model.model}
        icon={<span className="inline-block w-3 h-3 rounded-full" style={{ background: color }} />}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase tracking-wide">
              <th className="py-2.5 pr-4">Catalogue File</th>
              <th className="py-2.5 pr-4 text-right">Size</th>
              <th className="py-2.5 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {model.files.map(f => (
              <tr key={f.rel_path}
                className="border-b border-slate-50 hover:bg-slate-50/70 cursor-pointer transition-colors"
                onClick={() => setSelected({ relPath: f.rel_path, filename: f.filename, url: catalogFileUrl(f.rel_path) })}>
                <td className="py-2.5 pr-4">
                  <div className="flex items-center gap-2">
                    <FileText size={13} className="text-red-400 shrink-0" />
                    <span className="text-sm text-slate-700">{f.filename.replace(/\.pdf$/i, "")}</span>
                  </div>
                </td>
                <td className="py-2.5 pr-4 text-right text-xs text-slate-400">
                  {f.size_kb >= 1024 ? `${(f.size_kb / 1024).toFixed(1)} MB` : `${f.size_kb} KB`}
                </td>
                <td className="py-2.5 text-right">
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      setSelected({ relPath: f.rel_path, filename: f.filename, url: catalogFileUrl(f.rel_path) });
                    }}
                    className="text-xs px-2.5 py-1 rounded-lg bg-slate-100 text-slate-600 hover:bg-brand-blue hover:text-white transition-colors">
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── PDF model list table ───────────────────────────────────────────────────────

function ModelTable({ models, search, onSearch, onSelect }: {
  models: CatalogModel[]; search: string;
  onSearch: (v: string) => void; onSelect: (m: CatalogModel) => void;
}) {
  const q = search.toLowerCase();
  const filtered = q ? models.filter(m => m.model.toLowerCase().includes(q)) : models;
  return (
    <div className="space-y-3">
      <div className="relative max-w-sm">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-blue/30"
          placeholder="Search model…" value={search} onChange={e => onSearch(e.target.value)} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 uppercase tracking-wide">
              <th className="py-2.5 pr-4">Model</th>
              <th className="py-2.5 pr-4 text-right">PDFs</th>
              <th className="py-2.5 pr-4 text-right">Total Size</th>
              <th className="py-2.5 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(m => {
              const totalKb = m.files.reduce((s, f) => s + f.size_kb, 0);
              const color = modelColor(m.model);
              return (
                <tr key={m.model}
                  className="border-b border-slate-50 hover:bg-slate-50/70 cursor-pointer transition-colors"
                  onClick={() => onSelect(m)}>
                  <td className="py-3 pr-4">
                    <div className="flex items-center gap-3">
                      <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
                      <span className="font-medium text-slate-800">{m.model}</span>
                    </div>
                  </td>
                  <td className="py-3 pr-4 text-right text-slate-600">{m.pdf_count}</td>
                  <td className="py-3 pr-4 text-right text-slate-400 text-xs">
                    {totalKb >= 1024 ? `${(totalKb / 1024).toFixed(1)} MB` : `${totalKb.toFixed(0)} KB`}
                  </td>
                  <td className="py-3 text-right">
                    <button onClick={e => { e.stopPropagation(); onSelect(m); }}
                      className="text-xs px-3 py-1 rounded-lg bg-brand-blue text-white hover:bg-blue-700 transition-colors">
                      View
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length === 0 && <p className="text-center text-slate-400 text-sm py-10">No models match "{search}"</p>}
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

type MainTab = "pdf" | "upload";

export function Catalog() {
  const [pdfData,       setPdfData]       = useState<CatalogData | null>(null);
  const [mainTab,       setMainTab]       = useState<MainTab>("pdf");
  const [selectedModel, setSelectedModel] = useState<CatalogModel | null>(null);
  const [search,        setSearch]        = useState("");

  const loadCatalog = useCallback(() => {
    fetchCatalog().then(setPdfData);
  }, []);

  useEffect(() => { loadCatalog(); }, [loadCatalog]);

  if (selectedModel) {
    return (
      <div className="flex-1 p-6 overflow-y-auto">
        <div className="bg-white rounded-xl shadow-sm p-5">
          <ModelDetail model={selectedModel} onBack={() => { setSelectedModel(null); setMainTab("pdf"); }} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 overflow-y-auto">
      <div className="space-y-6">
        <div>
          <h2 className="text-xl font-bold text-slate-800">Parts Catalogues</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {pdfData?.total_pdfs ?? 0} PDFs across {pdfData?.models.length ?? 0} models
          </p>
        </div>

        <div className="bg-white rounded-xl shadow-sm p-5 space-y-5">
          <div className="flex gap-2 border-b border-slate-100 pb-3">
            <button onClick={() => setMainTab("pdf")}
              className={`flex items-center gap-2 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${mainTab === "pdf" ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}>
              <FileText size={14} /> PDF Catalogues
            </button>
            <button onClick={() => setMainTab("upload")}
              className={`flex items-center gap-2 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${mainTab === "upload" ? "bg-brand-blue text-white" : "text-slate-500 hover:bg-slate-50"}`}>
              <Upload size={14} /> Upload PDF
            </button>
          </div>

          {mainTab === "pdf" && pdfData && (
            <ModelTable
              models={pdfData.models}
              search={search}
              onSearch={setSearch}
              onSelect={m => { setSelectedModel(m); setSearch(""); }}
            />
          )}

          {mainTab === "upload" && (
            <UploadPanel onCatalogRefresh={loadCatalog} />
          )}
        </div>
      </div>
    </div>
  );
}
