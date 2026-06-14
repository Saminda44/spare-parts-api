"""Yamaha Spare Parts — Inventory Optimisation Dashboard.

Run:  streamlit run src/dashboard/app.py

Pages (sidebar):
  Overview          — Executive KPI summary + pipeline health
  Demand Forecast   — Forecast breakdown, trends, top SKUs
  Inventory Status  — Stock health, at-risk, coverage distribution
  Order Recommendation — Next shipment PO recommendation
  Classification    — ABC-XYZ-FSN, ML segments
  RL Policy         — PPO agent vs rule-based policy comparison
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ── page config must be first Streamlit call ──────────────────────────────────
st.set_page_config(
    page_title="Yamaha Inventory Optimisation",
    page_icon="🏍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard._data import (
    available_stages,
    load_classification,
    load_forecast,
    load_monthly_demand,
    load_policy,
    load_rl_policy,
    load_stock_tracker,
)
from src.dashboard._charts import (
    abc_bar,
    coverage_histogram,
    demand_trend,
    forecast_method_donut,
    ml_segment_bar,
    rl_cost_scatter,
    rl_multiplier_hist,
    sparkline,
    stock_status_donut,
    tier_donut,
    urgency_bar,
)

# ── Custom CSS (WowDash-inspired) ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Sidebar */
[data-testid="stSidebar"] {
    background: #1A1F37;
    border-right: 1px solid #2D3561;
}
[data-testid="stSidebar"] * { color: #CBD5E1 !important; }
[data-testid="stSidebar"] .stRadio label { font-size: 0.9rem; padding: 6px 0; cursor: pointer; }
[data-testid="stSidebar"] .stRadio label:hover { color: #fff !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: #fff !important; font-weight: 700; }

/* KPI cards */
.kpi-card {
    background: #fff;
    border-radius: 12px;
    padding: 20px 18px 14px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 4px solid #4361EE;
    margin-bottom: 4px;
}
.kpi-card.red   { border-left-color: #EF4444; }
.kpi-card.green { border-left-color: #2CC56F; }
.kpi-card.amber { border-left-color: #FFC107; }
.kpi-card.purple{ border-left-color: #7C3AED; }
.kpi-label  { font-size: 0.75rem; color: #94A3B8; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.kpi-value  { font-size: 1.8rem; font-weight: 700; color: #1E293B; line-height: 1; }
.kpi-delta  { font-size: 0.78rem; color: #64748B; margin-top: 4px; }
.kpi-delta.up   { color: #2CC56F; }
.kpi-delta.down { color: #EF4444; }

/* Section headings */
.section-title {
    font-size: 1.05rem; font-weight: 600; color: #1E293B;
    border-bottom: 2px solid #EEF2FF;
    padding-bottom: 6px; margin-bottom: 14px; margin-top: 20px;
}

/* Status badge */
.badge {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600; text-transform: capitalize;
}
.badge-red    { background:#FEE2E2; color:#DC2626; }
.badge-amber  { background:#FEF3C7; color:#D97706; }
.badge-green  { background:#D1FAE5; color:#059669; }
.badge-blue   { background:#DBEAFE; color:#2563EB; }
.badge-purple { background:#EDE9FE; color:#7C3AED; }
.badge-grey   { background:#F1F5F9; color:#64748B; }

/* Pipeline badge */
.pipeline-badge { padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; }
.pipeline-ok  { background: #D1FAE5; color: #059669; }
.pipeline-nok { background: #FEE2E2; color: #DC2626; }
</style>
""", unsafe_allow_html=True)


# ── Helper: KPI card HTML ─────────────────────────────────────────────────────

def kpi_card(label: str, value: str, delta: str = "", colour: str = "blue") -> str:
    delta_cls = ""
    if delta.startswith("+"):
        delta_cls = "up"
    elif delta.startswith("-"):
        delta_cls = "down"
    delta_html = f'<div class="kpi-delta {delta_cls}">{delta}</div>' if delta else ""
    return f"""
<div class="kpi-card {colour}">
  <div class="kpi-label">{label}</div>
  <div class="kpi-value">{value}</div>
  {delta_html}
</div>"""


def section(title: str) -> None:
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)


# ── Sidebar navigation ────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🏍️ Yamaha SL")
    st.markdown("**Inventory Optimisation**")
    st.divider()
    page = st.radio(
        "Navigation",
        [
            "📊 Overview",
            "📈 Demand Forecast",
            "🏭 Inventory Status",
            "📦 Order Recommendation",
            "🏷️ Classification",
            "🤖 RL Policy",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("**Pipeline Status**")
    for stage_name, ready in available_stages().items():
        badge_cls = "pipeline-ok" if ready else "pipeline-nok"
        icon = "✓" if ready else "✗"
        short = stage_name.split("—")[1].strip() if "—" in stage_name else stage_name
        st.markdown(
            f'<span class="pipeline-badge {badge_cls}">{icon} {short}</span><br>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

if page == "📊 Overview":
    st.markdown("## Dashboard — Overview")

    policy = load_policy()
    stock  = load_stock_tracker()
    cls    = load_classification()

    if policy.empty:
        st.warning("Run Stages 9–12 to populate dashboard data.")
        st.stop()

    # ── Top KPI cards ────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    total_skus   = len(policy)
    order_skus   = int((policy["net_requirement"] > 0).sum())
    immediate    = int((policy["order_urgency"] == "immediate").sum()) if "order_urgency" in policy.columns else 0
    order_val    = policy.loc[policy["net_requirement"] > 0, "net_requirement"].multiply(
                    policy.loc[policy["net_requirement"] > 0, "unit_value_lkr"]).sum() if "unit_value_lkr" in policy.columns else 0
    excess_skus  = int((stock["stock_status"] == "excess").sum()) if not stock.empty else 0

    with c1:
        st.markdown(kpi_card("Total SKUs", f"{total_skus:,}", colour="blue"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("SKUs to Order", f"{order_skus:,}", colour="amber"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Immediate Orders", f"{immediate:,}", colour="red"), unsafe_allow_html=True)
    with c4:
        val_bn = order_val / 1e9
        st.markdown(kpi_card("Order Value Est.", f"LKR {val_bn:.2f}B", colour="purple"), unsafe_allow_html=True)
    with c5:
        st.markdown(kpi_card("Excess Stock SKUs", f"{excess_skus:,}", colour="green"), unsafe_allow_html=True)

    st.divider()

    # ── Charts row ───────────────────────────────────────────────────────────
    col_l, col_m, col_r = st.columns([1.2, 1.2, 1])

    with col_l:
        section("Stock Status Distribution")
        if not stock.empty:
            status_counts = stock["stock_status"].value_counts().to_dict()
            st.plotly_chart(stock_status_donut(status_counts), use_container_width=True)

    with col_m:
        section("Policy Tier Breakdown")
        if not policy.empty and "policy_tier" in policy.columns:
            st.plotly_chart(tier_donut(policy), use_container_width=True)

    with col_r:
        section("Order Urgency")
        if not policy.empty and "order_urgency" in policy.columns:
            order_df = policy[policy["net_requirement"] > 0]
            st.plotly_chart(urgency_bar(order_df), use_container_width=True)

    st.divider()

    # ── Top at-risk SKUs table ───────────────────────────────────────────────
    section("Top At-Risk SKUs (Immediate Orders)")
    if not policy.empty and "order_urgency" in policy.columns:
        imm = (
            policy[policy["order_urgency"] == "immediate"]
            .sort_values("net_requirement", ascending=False)
            .head(10)
        )
        show_cols = [c for c in ["material_9", "description", "abc", "policy_tier",
                                  "stock_on_hand", "coverage_months", "forecast_lt",
                                  "net_requirement", "roq"] if c in imm.columns]
        st.dataframe(
            imm[show_cols].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: DEMAND FORECAST
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "📈 Demand Forecast":
    st.markdown("## Dashboard — Demand Forecast")

    forecast = load_forecast()
    monthly  = load_monthly_demand()

    if forecast.empty:
        st.warning("Run Stage 10 to generate demand forecasts.")
        st.stop()

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    non_zero    = int((forecast["forecast_lt"] > 0).sum())
    total_lt    = float(forecast["forecast_lt"].sum())
    a_lt        = float(forecast.loc[forecast.get("abc", pd.Series(dtype=str)) == "A", "forecast_lt"].sum()) if "abc" in forecast.columns else 0
    avg_monthly = float(forecast["forecast_m1"].mean()) if "forecast_m1" in forecast.columns else 0

    with c1:
        st.markdown(kpi_card("Active Forecast SKUs", f"{non_zero:,}", colour="blue"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("Total 3-Mo Forecast", f"{total_lt:,.0f} units", colour="green"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("A-class Lead-Time Fcst", f"{a_lt:,.0f} units", colour="red"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Avg Monthly Forecast", f"{avg_monthly:.1f} units", colour="purple"), unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns([1.6, 1])

    with col_l:
        section("Aggregate Monthly Demand Trend")
        if not monthly.empty:
            st.plotly_chart(demand_trend(monthly), use_container_width=True)

    with col_r:
        section("Forecast Method Breakdown")
        st.plotly_chart(forecast_method_donut(forecast), use_container_width=True)

    st.divider()
    section("Top 20 SKUs by 3-Month Forecast Volume")
    top_fc = (
        forecast[forecast["forecast_lt"] > 0]
        .sort_values("forecast_lt", ascending=False)
        .head(20)
    )
    show = [c for c in ["material_9", "description", "forecast_m1", "forecast_m2",
                         "forecast_m3", "forecast_lt", "method", "demand_std_lt"] if c in top_fc.columns]
    st.dataframe(top_fc[show].reset_index(drop=True), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: INVENTORY STATUS
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "🏭 Inventory Status":
    st.markdown("## Dashboard — Inventory Status")

    stock = load_stock_tracker()

    if stock.empty:
        st.warning("Run Stage 11 to generate stock tracker data.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    stockout_n  = int((stock["stock_status"] == "stockout").sum())
    critical_n  = int((stock["stock_status"] == "critical").sum())
    ok_n        = int((stock["stock_status"] == "ok").sum())
    total_val   = float(stock["stock_value_lkr"].sum())

    with c1:
        st.markdown(kpi_card("Stockout SKUs", f"{stockout_n:,}", colour="red"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("Critical (< 1 mo)", f"{critical_n:,}", colour="amber"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Healthy Stock SKUs", f"{ok_n:,}", colour="green"), unsafe_allow_html=True)
    with c4:
        val_bn = total_val / 1e6
        st.markdown(kpi_card("Total Stock Value", f"LKR {val_bn:.1f}M", colour="blue"), unsafe_allow_html=True)

    st.divider()
    col_l, col_m, col_r = st.columns(3)

    with col_l:
        section("Stock Status")
        status_counts = stock["stock_status"].value_counts().to_dict()
        st.plotly_chart(stock_status_donut(status_counts), use_container_width=True)

    with col_m:
        section("Coverage Distribution")
        st.plotly_chart(coverage_histogram(stock), use_container_width=True)

    with col_r:
        section("ABC Class Breakdown")
        if "abc" in stock.columns:
            st.plotly_chart(abc_bar(stock), use_container_width=True)

    st.divider()

    section("At-Risk SKUs (Coverage < Lead Time)")
    at_risk = stock[stock["stock_status"].isin({"stockout", "critical", "low"}) &
                    (stock["avg_monthly_demand"] > 0)]
    show = [c for c in ["material_9", "description", "abc", "policy_tier",
                         "stock_on_hand", "coverage_months", "stock_status",
                         "avg_monthly_demand", "forecast_lt"] if c in at_risk.columns]
    st.dataframe(
        at_risk[show].sort_values("coverage_months").head(30).reset_index(drop=True),
        use_container_width=True, hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: ORDER RECOMMENDATION
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "📦 Order Recommendation":
    st.markdown("## Dashboard — Order Recommendation")

    policy = load_policy()

    if policy.empty:
        st.warning("Run Stage 12 to generate the inventory policy.")
        st.stop()

    order = policy[policy["net_requirement"] > 0].copy()
    if "roq" in order.columns and "net_requirement" in order.columns:
        order["recommended_qty"] = order[["net_requirement", "roq"]].max(axis=1).round(0)
        order["order_value_lkr"] = order["recommended_qty"] * order.get("unit_value_lkr", 0)

    c1, c2, c3, c4 = st.columns(4)
    total_qty = float(order["recommended_qty"].sum()) if "recommended_qty" in order.columns else 0
    total_val = float(order["order_value_lkr"].sum()) if "order_value_lkr" in order.columns else 0
    a_val     = float(order.loc[order["abc"] == "A", "order_value_lkr"].sum()) if "abc" in order.columns and "order_value_lkr" in order.columns else 0
    sanity_n  = int(order["sanity_flag"].sum()) if "sanity_flag" in order.columns else 0

    with c1:
        st.markdown(kpi_card("SKUs to Order", f"{len(order):,}", colour="blue"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("Total Recommended Qty", f"{total_qty:,.0f} units", colour="green"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Est. Order Value (LKR)", f"{total_val/1e6:.1f}M", colour="amber"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Sanity-Flagged Items", f"{sanity_n:,}", colour="red"), unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns([1, 1.6])

    with col_l:
        section("Order Urgency")
        if "order_urgency" in order.columns:
            st.plotly_chart(urgency_bar(order), use_container_width=True)

    with col_r:
        section("Order Value by Policy Tier")
        if "policy_tier" in order.columns and "order_value_lkr" in order.columns:
            tier_val = (
                order.groupby("policy_tier")["order_value_lkr"]
                .sum()
                .reset_index()
                .sort_values("order_value_lkr", ascending=True)
            )
            import plotly.express as px
            fig = px.bar(tier_val, x="order_value_lkr", y="policy_tier",
                         orientation="h", color="policy_tier",
                         color_discrete_map={
                             "critical": "#EF4444", "managed": "#FFC107",
                             "watch": "#4361EE", "rationalise": "#94A3B8"
                         })
            fig.update_layout(height=250, margin=dict(l=5,r=5,t=30,b=5), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Filters
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        urgency_filter = st.multiselect(
            "Urgency", ["immediate", "soon", "planned"],
            default=["immediate", "soon"]
        )
    with col_f2:
        abc_filter = st.multiselect("ABC Class", ["A", "B", "C"], default=["A", "B"])
    with col_f3:
        sanity_only = st.checkbox("Sanity-flagged only", value=False)

    filtered = order.copy()
    if urgency_filter and "order_urgency" in filtered.columns:
        filtered = filtered[filtered["order_urgency"].isin(urgency_filter)]
    if abc_filter and "abc" in filtered.columns:
        filtered = filtered[filtered["abc"].isin(abc_filter)]
    if sanity_only and "sanity_flag" in filtered.columns:
        filtered = filtered[filtered["sanity_flag"]]

    section(f"Order List ({len(filtered):,} SKUs)")
    show_cols = [c for c in [
        "material_9", "description", "abc", "policy_tier", "order_urgency",
        "stock_on_hand", "coverage_months", "rol", "roq",
        "recommended_qty", "order_value_lkr",
        "forecast_lt", "safety_stock", "ss_method", "sanity_flag",
    ] if c in filtered.columns]
    st.dataframe(
        filtered[show_cols].sort_values("order_urgency" if "order_urgency" in filtered.columns else "material_9")
        .reset_index(drop=True),
        use_container_width=True, hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "🏷️ Classification":
    st.markdown("## Dashboard — Classification")

    cls = load_classification()

    if cls.empty:
        st.warning("Run Stage 9 to generate ABC-XYZ-FSN classification.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    a_skus = int((cls["abc"] == "A").sum())
    b_skus = int((cls["abc"] == "B").sum())
    active_skus = int((cls["avg_monthly_demand"] > 0).sum()) if "avg_monthly_demand" in cls.columns else 0
    crit   = int((cls["policy_tier"] == "critical").sum()) if "policy_tier" in cls.columns else 0

    with c1:
        st.markdown(kpi_card("A-Class SKUs (Top 80%)", f"{a_skus:,}", colour="red"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("B-Class SKUs (80–95%)", f"{b_skus:,}", colour="amber"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("Active SKUs (demand > 0)", f"{active_skus:,}", colour="green"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Critical Policy SKUs", f"{crit:,}", colour="purple"), unsafe_allow_html=True)

    st.divider()
    col_l, col_m, col_r = st.columns(3)

    with col_l:
        section("ABC Distribution")
        st.plotly_chart(abc_bar(cls), use_container_width=True)

    with col_m:
        section("Policy Tier")
        if "policy_tier" in cls.columns:
            st.plotly_chart(tier_donut(cls), use_container_width=True)

    with col_r:
        section("ML Demand Segments")
        if "ml_segment" in cls.columns:
            st.plotly_chart(ml_segment_bar(cls), use_container_width=True)
        else:
            st.info("ML segment column not found — re-run Stage 9.")

    st.divider()

    # ABC × XYZ heatmap
    section("ABC × XYZ Matrix (SKU count)")
    if "xyz" in cls.columns:
        pivot = (
            cls.groupby(["abc", "xyz"], observed=True)
            .size()
            .reset_index(name="count")
            .pivot(index="abc", columns="xyz", values="count")
            .fillna(0)
            .astype(int)
        )
        import plotly.figure_factory as ff
        import numpy as np
        z = pivot.values.tolist()
        fig = go.Figure(go.Heatmap(
            z=z,
            x=pivot.columns.tolist(),
            y=pivot.index.tolist(),
            colorscale="Blues",
            text=[[f"{v:,}" for v in row] for row in z],
            texttemplate="%{text}",
            hovertemplate="ABC %{y} × XYZ %{x}: %{z:,} SKUs<extra></extra>",
        ))
        fig.update_layout(height=280, margin=dict(l=10,r=10,t=30,b=10),
                          xaxis_title="XYZ Class", yaxis_title="ABC Class")
        st.plotly_chart(fig, use_container_width=True)

    section("Top A-Class SKUs by Issue Value")
    top_a = (
        cls[cls["abc"] == "A"]
        .sort_values("total_issue_value_lkr", ascending=False)
        .head(20)
    )
    show = [c for c in ["material_9","description","abc","xyz","fsn","abc_xyz_fsn",
                         "policy_tier","avg_monthly_demand","cv","total_issue_value_lkr",
                         "active_months"] if c in top_a.columns]
    st.dataframe(top_a[show].reset_index(drop=True), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: RL POLICY
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "🤖 RL Policy":
    st.markdown("## Dashboard — RL Policy (PPO Agent)")

    rl = load_rl_policy()

    if rl.empty:
        st.warning("Run Stage 14 (`python -m scripts.run_stage 14`) to train the RL agent.")
        st.info(
            "Stage 14 trains a PPO (Proximal Policy Optimisation) agent across all active SKUs "
            "simultaneously. It learns an order-quantity multiplier in the range [0.5, 1.5] "
            "relative to the rule-based ROQ, minimising simulated holding + stockout + ordering costs."
        )
        st.stop()

    active_rl = rl[rl["avg_monthly_demand"] > 0] if "avg_monthly_demand" in rl.columns else rl

    c1, c2, c3, c4 = st.columns(4)
    avg_mult  = float(active_rl["rl_multiplier"].mean()) if "rl_multiplier" in active_rl.columns else 1.0
    flagged   = int(active_rl["rl_flag"].sum()) if "rl_flag" in active_rl.columns else 0
    below_1   = int((active_rl["rl_multiplier"] < 1.0).sum()) if "rl_multiplier" in active_rl.columns else 0
    above_1   = int((active_rl["rl_multiplier"] >= 1.0).sum()) if "rl_multiplier" in active_rl.columns else 0

    with c1:
        st.markdown(kpi_card("Avg RL Multiplier", f"{avg_mult:.3f}×", colour="purple"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card("RL Orders < Rule-Based", f"{below_1:,}", colour="green"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card("RL Orders ≥ Rule-Based", f"{above_1:,}", colour="amber"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card("Flagged (> ±50%)", f"{flagged:,}", colour="red"), unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        section("RL Multiplier Distribution")
        st.plotly_chart(rl_multiplier_hist(active_rl), use_container_width=True)

    with col_r:
        section("RL vs Rule-Based Cost (Eval SKUs)")
        eval_cols = ["rule_cost_lkr", "rl_cost_lkr", "material_9", "avg_monthly_demand"]
        has_eval  = all(c in rl.columns for c in eval_cols)
        if has_eval:
            eval_df = rl[rl["rule_cost_lkr"].notna()].copy()
            st.plotly_chart(rl_cost_scatter(eval_df), use_container_width=True)
        else:
            st.info("Detailed eval data not available — re-run Stage 14 with fresh data.")

    st.divider()
    section("Policy Comparison: Rule-Based vs RL Recommended Qty")
    if "rl_recommended_qty" in rl.columns and "roq" in rl.columns:
        compare = rl[rl["net_requirement"] > 0].copy() if "net_requirement" in rl.columns else rl.head(500)
        show = [c for c in ["material_9","description","abc","policy_tier",
                              "roq","rl_recommended_qty","rl_multiplier",
                              "net_requirement","rl_flag"] if c in compare.columns]
        st.dataframe(
            compare[show].sort_values("rl_flag" if "rl_flag" in compare.columns else "material_9",
                                      ascending=False).head(100).reset_index(drop=True),
            use_container_width=True, hide_index=True,
        )
