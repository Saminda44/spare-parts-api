"""Reusable Plotly chart builders for the dashboard."""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# Colour palette aligned with WowDash
_BLUE   = "#4361EE"
_GREEN  = "#2CC56F"
_AMBER  = "#FFC107"
_RED    = "#EF4444"
_PURPLE = "#7C3AED"
_TEAL   = "#06B6D4"
_GREY   = "#94A3B8"

_ABC_COLOURS  = {"A": _RED, "B": _AMBER, "C": _GREEN}
_TIER_COLOURS = {"critical": _RED, "managed": _AMBER, "watch": _BLUE, "rationalise": _GREY}
_STATUS_COLOURS = {
    "stockout": _RED, "critical": "#F97316",
    "low": _AMBER, "ok": _GREEN, "excess": _BLUE,
}


def _base_layout(title: str, height: int = 350) -> dict:
    return dict(
        title=dict(text=title, font=dict(size=14, color="#1E293B")),
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="Inter, sans-serif", size=12, color="#475569"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )


# ---------------------------------------------------------------------------
# KPI sparklines
# ---------------------------------------------------------------------------

def sparkline(values: list[float], color: str = _BLUE) -> go.Figure:
    fig = go.Figure(go.Scatter(
        y=values, mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.1)",
    ))
    fig.update_layout(
        height=60, margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Stock status donut
# ---------------------------------------------------------------------------

def stock_status_donut(status_counts: dict[str, int]) -> go.Figure:
    labels = list(status_counts.keys())
    values = list(status_counts.values())
    colours = [_STATUS_COLOURS.get(s, _GREY) for s in labels]

    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.6,
        marker=dict(colors=colours),
        textinfo="percent",
        hovertemplate="%{label}: %{value:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Stock Status Distribution", height=320))
    return fig


# ---------------------------------------------------------------------------
# ABC class bar chart
# ---------------------------------------------------------------------------

def abc_bar(df: pd.DataFrame) -> go.Figure:
    counts = df["abc"].value_counts().reindex(["A", "B", "C"]).fillna(0)
    fig = go.Figure(go.Bar(
        x=counts.index.tolist(),
        y=counts.values.tolist(),
        marker_color=[_ABC_COLOURS.get(a, _GREY) for a in counts.index],
        text=counts.values.tolist(),
        textposition="outside",
        hovertemplate="%{x}-class: %{y:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("ABC Classification", height=300))
    fig.update_yaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# Policy tier donut
# ---------------------------------------------------------------------------

def tier_donut(df: pd.DataFrame) -> go.Figure:
    order = ["critical", "managed", "watch", "rationalise"]
    counts = df["policy_tier"].value_counts().reindex(order).fillna(0)
    colours = [_TIER_COLOURS[t] for t in order]
    fig = go.Figure(go.Pie(
        labels=order, values=counts.values.tolist(),
        hole=0.55,
        marker=dict(colors=colours),
        textinfo="percent+label",
        hovertemplate="%{label}: %{value:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Policy Tier Distribution", height=320))
    return fig


# ---------------------------------------------------------------------------
# Demand trend (monthly demand for a SKU or aggregate)
# ---------------------------------------------------------------------------

def demand_trend(monthly_demand: pd.DataFrame, skus: list[str] | None = None) -> go.Figure:
    df = monthly_demand.copy()
    if skus:
        df = df[df["material_9"].isin(skus)]
    agg = (
        df.groupby("year_month_str")["issue_qty"]
        .sum()
        .reset_index()
        .sort_values("year_month_str")
    )
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=agg["year_month_str"], y=agg["issue_qty"],
        mode="lines+markers",
        line=dict(color=_BLUE, width=2),
        fill="tozeroy",
        fillcolor=f"rgba(67,97,238,0.08)",
        name="Issue Qty",
        hovertemplate="%{x}: %{y:,.0f} units<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Monthly Demand Trend", height=350))
    fig.update_xaxes(showgrid=False, tickangle=-30)
    fig.update_yaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# Coverage histogram
# ---------------------------------------------------------------------------

def coverage_histogram(stock_df: pd.DataFrame) -> go.Figure:
    active = stock_df[
        (stock_df["avg_monthly_demand"] > 0) &
        (stock_df["coverage_months"] < 24)
    ]["coverage_months"]
    fig = px.histogram(
        active, nbins=40,
        labels={"value": "Coverage (months)", "count": "SKUs"},
        color_discrete_sequence=[_BLUE],
    )
    fig.add_vline(x=3, line_dash="dash", line_color=_RED,
                  annotation_text="Lead time (3 mo)", annotation_position="top right")
    fig.add_vline(x=6, line_dash="dash", line_color=_AMBER,
                  annotation_text="Excess threshold (6 mo)")
    fig.update_layout(**_base_layout("Stock Coverage Distribution", height=320))
    fig.update_yaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# Order urgency horizontal bar
# ---------------------------------------------------------------------------

def urgency_bar(order_df: pd.DataFrame) -> go.Figure:
    order = ["immediate", "soon", "planned"]
    colours = [_RED, _AMBER, _BLUE]
    counts = order_df["order_urgency"].value_counts().reindex(order).fillna(0)
    fig = go.Figure(go.Bar(
        x=counts.values.tolist(),
        y=counts.index.tolist(),
        orientation="h",
        marker_color=colours,
        text=[f"{v:,.0f}" for v in counts.values],
        textposition="outside",
        hovertemplate="%{y}: %{x:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Order Urgency Breakdown", height=260))
    fig.update_xaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# Forecast method donut
# ---------------------------------------------------------------------------

def forecast_method_donut(forecast_df: pd.DataFrame) -> go.Figure:
    counts = forecast_df["method"].value_counts()
    palette = [_BLUE, _GREEN, _AMBER, _RED, _PURPLE, _TEAL, _GREY]
    fig = go.Figure(go.Pie(
        labels=counts.index.tolist(),
        values=counts.values.tolist(),
        hole=0.55,
        marker=dict(colors=palette[:len(counts)]),
        textinfo="percent+label",
        hovertemplate="%{label}: %{value:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Forecast Method Breakdown", height=320))
    return fig


# ---------------------------------------------------------------------------
# ML segment bar (K-Means clusters)
# ---------------------------------------------------------------------------

def ml_segment_bar(df: pd.DataFrame) -> go.Figure:
    if "ml_segment" not in df.columns:
        return go.Figure()
    counts = df["ml_segment"].value_counts().sort_values(ascending=True)
    palette = [_RED, _AMBER, _BLUE, _GREEN, _TEAL, _GREY]
    fig = go.Figure(go.Bar(
        x=counts.values.tolist(),
        y=counts.index.tolist(),
        orientation="h",
        marker_color=palette[:len(counts)],
        text=[f"{v:,}" for v in counts.values],
        textposition="outside",
        hovertemplate="%{y}: %{x:,} SKUs<extra></extra>",
    ))
    fig.update_layout(**_base_layout("ML Demand Segments (K-Means)", height=300))
    fig.update_xaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# RL multiplier distribution
# ---------------------------------------------------------------------------

def rl_multiplier_hist(rl_df: pd.DataFrame) -> go.Figure:
    col = "rl_multiplier" if "rl_multiplier" in rl_df.columns else "rl_multiplier_mean"
    active = rl_df[rl_df[col].notna() & (rl_df["avg_monthly_demand"] > 0)][col]
    fig = px.histogram(
        active, nbins=30,
        labels={"value": "RL Multiplier", "count": "SKUs"},
        color_discrete_sequence=[_PURPLE],
    )
    fig.add_vline(x=1.0, line_dash="solid", line_color=_GREY,
                  annotation_text="Rule-based baseline")
    fig.add_vline(x=0.5, line_dash="dash", line_color=_RED,
                  annotation_text="Min bound (−50%)")
    fig.add_vline(x=1.5, line_dash="dash", line_color=_RED,
                  annotation_text="Max bound (+50%)")
    fig.update_layout(**_base_layout("RL Order Multiplier Distribution", height=320))
    fig.update_yaxes(showgrid=True, gridcolor="#F1F5F9")
    return fig


# ---------------------------------------------------------------------------
# Cost saving scatter (RL vs rule-based)
# ---------------------------------------------------------------------------

def rl_cost_scatter(eval_df: pd.DataFrame) -> go.Figure:
    if eval_df.empty:
        return go.Figure()
    colour = eval_df["cost_saving_pct"].apply(lambda x: _GREEN if x > 0 else _RED)
    fig = go.Figure(go.Scatter(
        x=eval_df["rule_cost_lkr"],
        y=eval_df["rl_cost_lkr"],
        mode="markers",
        marker=dict(color=colour, size=6, opacity=0.7),
        text=eval_df["material_9"],
        hovertemplate="SKU: %{text}<br>Rule cost: %{x:,.0f} LKR<br>RL cost: %{y:,.0f} LKR<extra></extra>",
    ))
    max_v = max(eval_df["rule_cost_lkr"].max(), eval_df["rl_cost_lkr"].max()) * 1.05
    fig.add_trace(go.Scatter(
        x=[0, max_v], y=[0, max_v],
        mode="lines", line=dict(color=_GREY, dash="dash"), name="Break-even",
        showlegend=True,
    ))
    fig.update_layout(**_base_layout("RL vs Rule-Based Cost (eval SKUs)", height=380))
    fig.update_xaxes(title="Rule-Based Cost (LKR)", showgrid=True, gridcolor="#F1F5F9")
    fig.update_yaxes(title="RL Cost (LKR)", showgrid=True, gridcolor="#F1F5F9")
    return fig
