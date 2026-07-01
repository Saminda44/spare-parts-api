import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, TrendingUp, Package, ShoppingCart,
  Zap, CheckCircle2, XCircle, Bike, BookOpen, BarChart2, FileText,
  Activity, Search,
} from "lucide-react";
import type { PipelineStatus } from "../api/client";

// ── Overview ──────────────────────────────────────────────────────────────
const NAV_OVERVIEW = [
  { to: "/", label: "Overview", Icon: LayoutDashboard },
];

// ── Motorcycles ───────────────────────────────────────────────────────────
const NAV_MOTO = [
  { to: "/bikes",    label: "Bike Sales & Forecast", Icon: TrendingUp },
  { to: "/bike-eda", label: "Dealer Performance",    Icon: Bike       },
  { to: "/mcsi-eda", label: "MCSI Sales EDA",        Icon: Search     },
  { to: "/uio",      label: "UIO Snapshot",           Icon: Activity   },
];

// ── Spare Parts ───────────────────────────────────────────────────────────
const NAV_PARTS_ANALYSIS = [
  { to: "/classification", label: "Inventory Analysis",         Icon: Package      },
  { to: "/forecast",       label: "Demand Forecast",            Icon: TrendingUp   },
  { to: "/orders",         label: "Order Plan",                  Icon: ShoppingCart },
  { to: "/rl",             label: "RL Policy",                   Icon: Zap          },
];

const NAV_PARTS_DATA = [
  { to: "/eda",   label: "Spare Parts Analysis", Icon: BarChart2 },
  { to: "/parts", label: "Part Master",          Icon: BookOpen  },
];

const NAV_PARTS_CATALOG = [
  { to: "/catalog", label: "Catalogues", Icon: FileText },
];

// ── Pipeline stages ───────────────────────────────────────────────────────
const EARLY_STAGES: { key: keyof PipelineStatus; label: string }[] = [
  { key: "stage1_mcsi",            label: "Stage 1 — MCSI" },
  { key: "stage2_sales_forecast",  label: "Stage 2 — Sales Fcst" },
  { key: "stage3_uio_forecast",    label: "Stage 3 — UIO Fcst" },
  { key: "stage4_orders_eda",      label: "Stage 4 — Orders EDA" },
  { key: "stage5_sales_eda",       label: "Stage 5 — Sales EDA" },
  { key: "stage6_part_master",     label: "Stage 6 — Part Master" },
  { key: "stage7_stock_movements", label: "Stage 7 — Movements" },
  { key: "stage8_spare_parts_eda", label: "Stage 8 — Spare EDA" },
];

const LATE_STAGES: { key: keyof PipelineStatus; label: string }[] = [
  { key: "stage9_classification",   label: "Stage 9 — Classification" },
  { key: "stage10_demand_forecast", label: "Stage 10 — Forecast" },
  { key: "stage11_stock_tracker",   label: "Stage 11 — Stock Tracker" },
  { key: "stage12_policy",          label: "Stage 12 — ROL/ROQ" },
  { key: "stage13_shipment_report", label: "Stage 13 — Shipment" },
  { key: "stage14_rl_policy",       label: "Stage 14 — RL Policy" },
];

interface Props { pipeline?: PipelineStatus | null; freshness?: Record<string, string | null>; }

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffH  = Math.floor(diffMs / 3_600_000);
  if (diffH < 1)  return "<1h ago";
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 30) return `${diffD}d ago`;
  return `${Math.floor(diffD / 30)}mo ago`;
}

function NavItem({ to, label, Icon }: { to: string; label: string; Icon: React.ElementType }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
          isActive ? "bg-brand-blue text-white" : "text-slate-300 hover:bg-white/10 hover:text-white"
        }`
      }
    >
      <Icon size={15}/>
      {label}
    </NavLink>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-3 pt-3 pb-1 text-[10px] font-bold uppercase tracking-widest text-slate-500">
      {children}
    </p>
  );
}

function Divider() {
  return <div className="my-2 border-t border-white/10"/>;
}

function StageRow({ ok, label, ago }: { ok: boolean; label: string; ago?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      {ok
        ? <CheckCircle2 size={11} className="text-brand-green shrink-0"/>
        : <XCircle      size={11} className="text-slate-600 shrink-0"/>}
      <span className={`flex-1 ${ok ? "text-slate-300" : "text-slate-600"}`}>{label}</span>
      {ago && <span className="text-[9px] text-slate-600 shrink-0">{ago}</span>}
    </div>
  );
}

export function Sidebar({ pipeline, freshness = {} }: Props) {
  return (
    <aside className="w-60 h-screen bg-sidebar text-white flex flex-col shrink-0 overflow-hidden">
      {/* Logo */}
      <div className="px-6 py-4 border-b border-white/10 shrink-0">
        <p className="text-xs text-slate-400 font-medium uppercase tracking-widest">Yamaha Sri Lanka</p>
        <h1 className="text-base font-bold leading-tight mt-0.5">Inventory Optimisation</h1>
      </div>

      <nav className="flex-1 px-3 py-2 overflow-y-auto no-scrollbar">

        {/* ── 1. Overview ── */}
        <SectionLabel>Overview</SectionLabel>
        {NAV_OVERVIEW.map(n => <NavItem key={n.to} {...n}/>)}

        <Divider/>

        {/* ── 2. Motorcycles ── */}
        <SectionLabel>Motorcycles</SectionLabel>
        {NAV_MOTO.map(n => <NavItem key={n.to} {...n}/>)}

        <Divider/>

        {/* ── 3. Spare Parts ── */}
        <SectionLabel>Spare Parts</SectionLabel>

        {NAV_PARTS_ANALYSIS.map(n => <NavItem key={n.to} {...n}/>)}
        {NAV_PARTS_DATA.map(n => <NavItem key={n.to} {...n}/>)}
        {NAV_PARTS_CATALOG.map(n => <NavItem key={n.to} {...n}/>)}

        {/* Pipeline status — bottom of scroll area */}
        <Divider/>
        <p className="px-3 pt-1 pb-1 text-[10px] font-bold uppercase tracking-widest text-slate-500">Pipeline</p>
        <div className="px-1 pb-4 space-y-1">
          {EARLY_STAGES.map(({ key, label }) => (
            <StageRow key={key} ok={pipeline?.[key] ?? false} label={label} ago={relativeTime(freshness[key])}/>
          ))}
          <div className="my-1.5 border-t border-white/10"/>
          {LATE_STAGES.map(({ key, label }) => (
            <StageRow key={key} ok={pipeline?.[key] ?? false} label={label} ago={relativeTime(freshness[key])}/>
          ))}
        </div>

      </nav>
    </aside>
  );
}
