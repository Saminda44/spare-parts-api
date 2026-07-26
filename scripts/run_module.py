"""CLI runner for the modular rebuild pipeline.

Each module is a self-contained class that wraps one or more pipeline stages.
Run a module end-to-end and optionally save outputs to data/processed/.

Usage:
    python -m scripts.run_module 1          # Run Module 1: Vehicle Intelligence
    python -m scripts.run_module 1 --save   # Run and save outputs to data/processed/
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reconfigure stdout for Windows terminals (avoids UnicodeEncodeError on emoji/arrows)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse

from loguru import logger


MODULE_REGISTRY: dict[int, str] = {
    1: "Vehicle Intelligence (sales forecast + UIO forecast + age distribution)",
    2: "Demand Intelligence (orders + sales + UIO-based demand fusion)",
    3: "Inventory Intelligence (current stock + pipeline + backorders + position)",
    4: "Inventory Planning (lead-time demand + safety stock + reorder level)",
    5: "Inventory Policy (ROL / ROQ / Buffer)",
    6: "Reporting & Dashboard",
}


def _run_module_1(save: bool) -> None:
    from src.modules.module1_vehicle_intelligence import VehicleIntelligence

    vi = VehicleIntelligence()
    result = vi.run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 1 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Sales Forecast]  shape={result.sales_forecast.shape}")
    print(result.sales_forecast.head(5).to_string(index=False))

    print(f"\n[UIO Forecast]  shape={result.uio_forecast.shape}")
    print(result.uio_forecast.head(5).to_string(index=False))

    print(f"\n[Age Distribution]  shape={result.age_distribution.shape}")
    print(result.age_distribution.head(5).to_string(index=False))

    print(f"\n[Metadata]")
    for k, v in result.metadata.items():
        print(f"  {k}: {v}")

    # ── Validation checks ──────────────────────────────────────────────
    print("\n[Validation]")
    checks_passed = True

    sf_months = result.sales_forecast["month"].nunique() if not result.sales_forecast.empty else 0
    if sf_months >= 12:
        print(f"  PASS  sales_forecast has {sf_months} forecast months (>= 12 required)")
    else:
        print(f"  FAIL  sales_forecast has only {sf_months} months (need >= 12)")
        checks_passed = False

    uio_months = result.uio_forecast["month"].nunique() if not result.uio_forecast.empty else 0
    if uio_months >= 12:
        print(f"  PASS  uio_forecast has {uio_months} forecast months (>= 12 required)")
    else:
        print(f"  FAIL  uio_forecast has only {uio_months} months (need >= 12)")
        checks_passed = False

    age_rows = len(result.age_distribution)
    age_models = result.age_distribution["model"].nunique() if not result.age_distribution.empty else 0
    if age_rows > 0 and age_models > 0:
        print(f"  PASS  age_distribution has {age_rows} rows across {age_models} models")
    else:
        print(f"  FAIL  age_distribution is empty")
        checks_passed = False

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Save outputs ───────────────────────────────────────────────────
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)

        sf_path = out / "m1_sales_forecast.parquet"
        uio_path = out / "m1_uio_forecast.parquet"
        age_path = out / "m1_age_distribution.parquet"

        result.sales_forecast.to_parquet(sf_path, index=False)
        result.uio_forecast.to_parquet(uio_path, index=False)
        result.age_distribution.to_parquet(age_path, index=False)

        print(f"\n[Saved]")
        print(f"  {sf_path}")
        print(f"  {uio_path}")
        print(f"  {age_path}")
        logger.info("Module 1 outputs saved to data/processed/")


def _run_module_2(save: bool) -> None:
    from src.modules.module1_vehicle_intelligence import VehicleIntelligence
    from src.modules.module2_demand_intelligence import DemandIntelligence

    # Load Module 1 result to pass UIO context to Module 2
    m1 = VehicleIntelligence().run()
    result = DemandIntelligence(vehicle_result=m1).run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 2 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Orders Forecast]  shape={result.orders_forecast.shape}")
    print(result.orders_forecast.head(5).to_string(index=False))

    print(f"\n[Sales Forecast]  shape={result.sales_forecast.shape}")
    print(result.sales_forecast.head(5).to_string(index=False))

    print(f"\n[UIO Demand]  shape={result.uio_demand.shape}")
    print(result.uio_demand.head(5).to_string(index=False))

    print(f"\n[Fused Demand]  shape={result.fused_demand.shape}")
    print(result.fused_demand.head(5).to_string(index=False))

    print(f"\n[Metadata]")
    for k, v in result.metadata.items():
        if k != "future_months":
            print(f"  {k}: {v}")
    if result.metadata.get("future_months"):
        months = result.metadata["future_months"]
        print(f"  future_months: {months[0]} ... {months[-1]} ({len(months)} months)")

    # ── Save outputs first (validation checks reference saved files) ──────────
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)

        result.orders_forecast.to_parquet(out / "m2_orders_forecast.parquet", index=False)
        result.sales_forecast.to_parquet(out / "m2_sales_forecast.parquet", index=False)
        result.uio_demand.to_parquet(out / "m2_uio_demand.parquet", index=False)
        result.fused_demand.to_parquet(out / "m2_fused_demand.parquet", index=False)

        print(f"\n[Saved]")
        print(f"  data/processed/m2_orders_forecast.parquet")
        print(f"  data/processed/m2_sales_forecast.parquet")
        print(f"  data/processed/m2_uio_demand.parquet")
        print(f"  data/processed/m2_fused_demand.parquet")
        logger.info("Module 2 outputs saved to data/processed/")

    # ── Validation checks (run after save so file checks are valid) ────────────
    print("\n[Validation]")
    checks_passed = True

    # Check 1: fused_demand has rows
    if not result.fused_demand.empty:
        n_skus = result.fused_demand["part_no"].nunique()
        print(f"  PASS  fused_demand is non-empty: {len(result.fused_demand):,} rows, {n_skus:,} SKUs")
    else:
        print("  FAIL  fused_demand is empty")
        checks_passed = False

    # Check 2: fused_demand has required columns
    required_cols = {"part_no", "month", "demand_qty"}
    missing = required_cols - set(result.fused_demand.columns)
    if not missing:
        print(f"  PASS  fused_demand has required columns: {sorted(required_cols)}")
    else:
        print(f"  FAIL  fused_demand missing columns: {sorted(missing)}")
        checks_passed = False

    # Check 3: four parquet files saved (only if --save)
    if save:
        out = Path("data/processed")
        for fname in [
            "m2_orders_forecast.parquet",
            "m2_sales_forecast.parquet",
            "m2_uio_demand.parquet",
            "m2_fused_demand.parquet",
        ]:
            fpath = out / fname
            if fpath.exists():
                print(f"  PASS  {fname} ({fpath.stat().st_size // 1024} KB)")
            else:
                print(f"  FAIL  {fname} not found at {fpath}")
                checks_passed = False

    # Check 4: unique SKU count in fused_demand
    total_skus = result.fused_demand["part_no"].nunique() if not result.fused_demand.empty else 0
    if total_skus > 0:
        print(f"  PASS  Total unique SKUs in fused_demand: {total_skus:,}")
    else:
        print("  FAIL  Zero unique SKUs in fused_demand")
        checks_passed = False

    # Check 5: no negative demand_qty
    if not result.fused_demand.empty:
        neg_count = int((result.fused_demand["demand_qty"] < 0).sum())
        if neg_count == 0:
            print("  PASS  No negative demand_qty values")
        else:
            print(f"  WARN  {neg_count:,} rows have demand_qty < 0")

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Summary stats ──────────────────────────────────────────────────
    if not result.fused_demand.empty:
        print("\n[Fused Demand — Top 5 rows by demand_qty]")
        top5 = result.fused_demand.nlargest(5, "demand_qty")
        print(top5.to_string(index=False))
        print(f"\n  Total unique SKUs : {result.fused_demand['part_no'].nunique():,}")
        print(f"  Mean demand_qty   : {result.fused_demand['demand_qty'].mean():.2f}")
        print(f"  Max  demand_qty   : {result.fused_demand['demand_qty'].max():.2f}")


def _run_module_3(save: bool) -> None:
    from src.modules.module3_inventory_intelligence import InventoryIntelligence

    result = InventoryIntelligence().run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 3 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Current Stock]  shape={result.current_stock.shape}")
    print(result.current_stock[result.current_stock["stock_qty"] > 0].head(5).to_string(index=False))

    print(f"\n[Pipeline Inventory]  shape={result.pipeline_inventory.shape}")
    print(result.pipeline_inventory.head(5).to_string(index=False))

    print(f"\n[Backorders]  shape={result.backorders.shape}")
    print(result.backorders.head(5).to_string(index=False))

    print(f"\n[Inventory Position]  shape={result.inventory_position.shape}")
    top_pos = result.inventory_position.nlargest(5, "net_position")
    print(top_pos.to_string(index=False))

    print(f"\n[Metadata]")
    for k, v in result.metadata.items():
        if k != "warnings":
            print(f"  {k}: {v}")
    if result.metadata.get("warnings"):
        print("  warnings:")
        for w in result.metadata["warnings"]:
            print(f"    - {w}")

    # ── Validation checks ──────────────────────────────────────────────
    print("\n[Validation]")
    checks_passed = True

    # Check 1: current_stock has part_no and stock_qty > 0 for some rows
    cs_has_stock = int((result.current_stock["stock_qty"] > 0).sum()) if not result.current_stock.empty else 0
    if not result.current_stock.empty and "part_no" in result.current_stock.columns:
        print(
            f"  PASS  current_stock: {result.current_stock['part_no'].nunique():,} SKUs | "
            f"{cs_has_stock:,} with stock > 0 | "
            f"sum_qty = {result.current_stock['stock_qty'].sum():,.0f}"
        )
    else:
        print("  FAIL  current_stock is empty or missing part_no column")
        checks_passed = False

    # Check 2: inventory_position has net_position column
    if not result.inventory_position.empty and "net_position" in result.inventory_position.columns:
        pos_count = int((result.inventory_position["net_position"] > 0).sum())
        neg_count = int((result.inventory_position["net_position"] < 0).sum())
        print(
            f"  PASS  inventory_position has net_position: "
            f"{pos_count:,} positive | {neg_count:,} negative"
        )
    else:
        print("  FAIL  inventory_position missing net_position column or is empty")
        checks_passed = False

    # Check 3: pipeline_inventory has expected_receipt_date
    if "expected_receipt_date" in result.pipeline_inventory.columns or result.pipeline_inventory.empty:
        print(
            f"  PASS  pipeline_inventory: {len(result.pipeline_inventory):,} rows | "
            f"sum_ordered_qty = {result.pipeline_inventory['ordered_qty'].sum():,.0f}"
            if not result.pipeline_inventory.empty
            else "  PASS  pipeline_inventory is empty (no in-transit stock found)"
        )
    else:
        print("  FAIL  pipeline_inventory missing expected_receipt_date column")
        checks_passed = False

    # Check 4: backorders has backorder_qty
    if "backorder_qty" in result.backorders.columns or result.backorders.empty:
        print(
            f"  PASS  backorders: {len(result.backorders):,} materials | "
            f"sum_backorder_qty = {result.backorders['backorder_qty'].sum():,.0f}"
            if not result.backorders.empty
            else "  PASS  backorders is empty (no short-shipped lines)"
        )
    else:
        print("  FAIL  backorders missing backorder_qty column")
        checks_passed = False

    # Check 5: net_position = stock + pipeline - backorders (spot-check)
    if not result.inventory_position.empty:
        ip = result.inventory_position
        computed = (ip["stock_qty"] + ip["pipeline_qty"] - ip["backorder_qty"]).round(6)
        actual = ip["net_position"].round(6)
        if (computed - actual).abs().max() < 1e-4:
            print("  PASS  net_position = stock_qty + pipeline_qty - backorder_qty (verified)")
        else:
            print("  FAIL  net_position arithmetic check failed")
            checks_passed = False

    # ── Save outputs ───────────────────────────────────────────────────
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)

        result.current_stock.to_parquet(out / "m3_current_stock.parquet", index=False)
        result.pipeline_inventory.to_parquet(out / "m3_pipeline_inventory.parquet", index=False)
        result.backorders.to_parquet(out / "m3_backorders.parquet", index=False)
        result.inventory_position.to_parquet(out / "m3_inventory_position.parquet", index=False)

        print(f"\n[Saved]")
        for fname in [
            "m3_current_stock.parquet",
            "m3_pipeline_inventory.parquet",
            "m3_backorders.parquet",
            "m3_inventory_position.parquet",
        ]:
            fpath = out / fname
            size_kb = fpath.stat().st_size // 1024 if fpath.exists() else 0
            status = "PASS" if fpath.exists() else "FAIL"
            print(f"  {status}  {fname} ({size_kb} KB)")
            if not fpath.exists():
                checks_passed = False

        logger.info("Module 3 outputs saved to data/processed/")

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Summary stats ──────────────────────────────────────────────────
    print("\n[Summary Statistics]")
    print(f"  Total unique SKUs in current_stock   : {result.current_stock['part_no'].nunique():,}")
    print(f"  Total unique SKUs in pipeline        : {len(result.pipeline_inventory):,}")
    print(f"  Total unique SKUs in backorders      : {len(result.backorders):,}")
    print(f"  Total unique SKUs in inventory_pos   : {result.inventory_position['part_no'].nunique():,}")
    print(f"  Sum stock_qty                        : {result.current_stock['stock_qty'].sum():,.0f}")
    if not result.pipeline_inventory.empty:
        print(f"  Sum pipeline_qty (ordered_qty)       : {result.pipeline_inventory['ordered_qty'].sum():,.0f}")
    if not result.backorders.empty:
        print(f"  Sum backorder_qty                    : {result.backorders['backorder_qty'].sum():,.0f}")
    print(f"  Sum net_position                     : {result.inventory_position['net_position'].sum():,.0f}")


def _run_module_4(save: bool) -> None:
    from src.modules.module4_inventory_planning import InventoryPlanning

    result = InventoryPlanning().run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 4 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Lead-Time Demand]  shape={result.lead_time_demand.shape}")
    print(result.lead_time_demand.head(5).to_string(index=False))

    print(f"\n[Safety Stock]  shape={result.safety_stock.shape}")
    print(result.safety_stock.head(5).to_string(index=False))

    print(f"\n[Reorder Level]  shape={result.reorder_level.shape}")
    print(result.reorder_level.head(5).to_string(index=False))

    print(f"\n[Planning Table]  shape={result.planning_table.shape}")
    print(result.planning_table.head(5).to_string(index=False))

    # ── Validation checks ──────────────────────────────────────────────
    print("\n[Validation]")
    checks_passed = True

    # Check 1: planning_table has signal_to_reorder boolean column
    if "signal_to_reorder" in result.planning_table.columns:
        sig_dtype = result.planning_table["signal_to_reorder"].dtype
        if sig_dtype == bool:
            print(f"  PASS  signal_to_reorder column present (dtype=bool)")
        else:
            print(f"  WARN  signal_to_reorder column present but dtype={sig_dtype} (expected bool)")
    else:
        print("  FAIL  signal_to_reorder column missing from planning_table")
        checks_passed = False

    # Check 2: ROL values are all non-negative
    if (result.reorder_level["rol"] >= 0).all():
        print(
            f"  PASS  All ROL values >= 0 "
            f"(max ROL = {result.reorder_level['rol'].max():.2f})"
        )
    else:
        n_neg = int((result.reorder_level["rol"] < 0).sum())
        print(f"  FAIL  {n_neg:,} ROL values are negative")
        checks_passed = False

    # Check 3: urgency_score is computed for all rows with mean_monthly_demand > 0
    pt = result.planning_table
    active_mask = pt["mean_monthly_demand"] > 0
    n_active = int(active_mask.sum())
    n_urgency_null = int(pt.loc[active_mask, "urgency_score"].isna().sum())
    if n_urgency_null == 0:
        print(f"  PASS  urgency_score computed for all {n_active:,} active SKUs")
    else:
        print(f"  WARN  {n_urgency_null:,} active SKUs have NaN urgency_score")

    # Check 4: ROL = lead_time_demand + safety_stock (arithmetic check)
    rol_computed = (
        result.reorder_level["lead_time_demand"] + result.reorder_level["safety_stock"]
    ).clip(lower=0.0).round(4)
    rol_actual = result.reorder_level["rol"].round(4)
    max_diff = (rol_computed - rol_actual).abs().max()
    if max_diff < 1e-3:
        print(f"  PASS  ROL = lead_time_demand + safety_stock (max diff = {max_diff:.2e})")
    else:
        print(f"  FAIL  ROL arithmetic check failed (max diff = {max_diff:.4f})")
        checks_passed = False

    # Check 5: four parquet files saved (only when --save)
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)
        result.lead_time_demand.to_parquet(out / "m4_lead_time_demand.parquet", index=False)
        result.safety_stock.to_parquet(out / "m4_safety_stock.parquet", index=False)
        result.reorder_level.to_parquet(out / "m4_reorder_level.parquet", index=False)
        result.planning_table.to_parquet(out / "m4_planning_table.parquet", index=False)

        print(f"\n[Saved]")
        for fname in [
            "m4_lead_time_demand.parquet",
            "m4_safety_stock.parquet",
            "m4_reorder_level.parquet",
            "m4_planning_table.parquet",
        ]:
            fpath = out / fname
            size_kb = fpath.stat().st_size // 1024 if fpath.exists() else 0
            status = "PASS" if fpath.exists() else "FAIL"
            print(f"  {status}  {fname} ({size_kb} KB)")
            if not fpath.exists():
                checks_passed = False

        logger.info("Module 4 outputs saved to data/processed/")

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Key metrics ────────────────────────────────────────────────────
    n_signal = int(result.planning_table["signal_to_reorder"].sum())
    print(f"\n[Key Metrics]")
    print(f"  Lead-time demand: {result.lead_time_demand.shape}")
    print(f"  Safety stock:     {result.safety_stock.shape}")
    print(f"  Reorder level:    {result.reorder_level.shape}")
    print(f"  Planning table:   {result.planning_table.shape}")
    print(f"  Parts signalling reorder: {n_signal:,}")
    print(f"  Metadata:")
    for k, v in result.metadata.items():
        if k not in ("warnings", "service_levels"):
            print(f"    {k}: {v}")
    if result.metadata.get("warnings"):
        print(f"  Warnings:")
        for w in result.metadata["warnings"]:
            print(f"    - {w}")

    # ── Top 5 most urgent (lowest urgency_score) ──────────────────────
    pt = result.planning_table
    signalling = pt[pt["signal_to_reorder"]].copy()
    if not signalling.empty:
        top5_urgent = signalling.nsmallest(5, "urgency_score")
        print(f"\n[Top 5 Most Urgent (lowest urgency_score — most below ROL)]")
        display_cols = [
            c for c in [
                "part_no", "demand_class", "mean_monthly_demand",
                "rol", "net_position", "urgency_score",
            ]
            if c in top5_urgent.columns
        ]
        print(top5_urgent[display_cols].to_string(index=False))
    else:
        print("\n[Top 5 Most Urgent]  No parts currently signalling reorder.")


def _run_module_5(save: bool) -> None:
    from src.modules.module5_procurement_optimization import ProcurementOptimization

    result = ProcurementOptimization().run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 5 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Net Need]  shape={result.net_need.shape}")
    print(result.net_need.head(5).to_string(index=False))

    print(f"\n[Constrained Order]  shape={result.constrained_order.shape}")
    print(result.constrained_order.head(5).to_string(index=False))

    print(f"\n[Import Recommendation]  shape={result.import_recommendation.shape}")
    print(result.import_recommendation.head(5).to_string(index=False))

    print(f"\n[Constraint Summary]")
    for k, v in result.constraint_summary.items():
        print(f"  {k}: {v}")

    # ── Validation checks ──────────────────────────────────────────────
    print("\n[Validation]")
    checks_passed = True
    rec = result.import_recommendation
    co = result.constrained_order

    # Check 1: priority column has values 1-4
    if "priority" in rec.columns:
        valid_priorities = set(rec["priority"].unique()) - {1, 2, 3, 4}
        if not valid_priorities:
            print(
                f"  PASS  priority column present with valid values "
                f"{sorted(rec['priority'].unique().tolist())}"
            )
        else:
            print(f"  FAIL  priority column has unexpected values: {valid_priorities}")
            checks_passed = False
    else:
        print("  FAIL  priority column missing from import_recommendation")
        checks_passed = False

    # Check 2: all order_qty >= MOQ (or 0 if constrained away)
    from src.modules.module5_procurement_optimization import ProcurementOptimization
    moq = ProcurementOptimization.MOQ
    non_zero = rec[rec["order_qty"] > 0]["order_qty"]
    if non_zero.empty or (non_zero >= moq).all():
        print(f"  PASS  All non-zero order_qty >= MOQ={moq}")
    else:
        n_below = int((non_zero < moq).sum())
        print(f"  FAIL  {n_below:,} rows have order_qty > 0 but < MOQ={moq}")
        checks_passed = False

    # Check 3: constrained_qty >= 0
    if (co["constrained_qty"] >= 0).all():
        print(f"  PASS  All constrained_qty >= 0")
    else:
        n_neg = int((co["constrained_qty"] < 0).sum())
        print(f"  FAIL  {n_neg:,} rows have constrained_qty < 0")
        checks_passed = False

    # Check 4: net_need shape non-empty
    if not result.net_need.empty:
        print(
            f"  PASS  net_need is non-empty: "
            f"{len(result.net_need):,} rows | "
            f"required columns: {list(result.net_need.columns)}"
        )
    else:
        print("  FAIL  net_need is empty")
        checks_passed = False

    # Check 5: binding_constraint values are valid
    valid_constraints = {"none", "budget", "container"}
    actual_constraints = set(co["binding_constraint"].unique())
    if actual_constraints <= valid_constraints:
        print(
            f"  PASS  binding_constraint values valid: {sorted(actual_constraints)}"
        )
    else:
        bad = actual_constraints - valid_constraints
        print(f"  FAIL  unexpected binding_constraint values: {bad}")
        checks_passed = False

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Priority tier breakdown ────────────────────────────────────────
    print("\n[Priority Tier Breakdown — all recommendation rows]")
    tier_labels = {1: "Critical", 2: "High", 3: "Medium", 4: "Low"}
    for p in sorted(rec["priority"].unique()):
        tier_rows = rec[rec["priority"] == p]
        to_order = tier_rows[tier_rows["order_qty"] > 0]
        print(
            f"  Priority {p} ({tier_labels.get(p, '?')}): "
            f"{len(tier_rows):,} SKUs | "
            f"{len(to_order):,} to order | "
            f"total_units={to_order['order_qty'].sum():,.0f}"
        )

    # ── Top 10 Critical parts ──────────────────────────────────────────
    critical = rec[(rec["priority"] == 1) & (rec["order_qty"] > 0)]
    if not critical.empty:
        print(f"\n[Top 10 Critical Parts (Priority=1, order_qty>0) — sorted by urgency_score]")
        top10 = critical.nsmallest(10, "urgency_score")
        display_cols = [
            c for c in ["part_no", "order_qty", "urgency_score", "demand_class",
                        "mean_monthly_demand", "binding_constraint"]
            if c in top10.columns
        ]
        print(top10[display_cols].to_string(index=False))
    else:
        print("\n[Top 10 Critical Parts]  No critical parts with order_qty > 0.")

    # ── Constraint summary (printed again for clarity) ─────────────────
    print("\n[Constraint Summary]")
    cs = result.constraint_summary
    print(f"  Budget constraint applied    : {cs.get('budget_constraint_applied')}")
    print(f"  Cost data available          : {cs.get('budget_cost_data_available')}")
    print(f"  Budget used                  : LKR {cs.get('budget_used_lkr', 0):,.0f}")
    print(f"  Container vol needed         : {cs.get('container_vol_needed_m3', 0):.2f} m3")
    print(f"  Container vol allocated      : {cs.get('container_vol_allocated_m3', 0):.2f} m3")
    print(f"  Container fill %             : {cs.get('container_fill_pct', 0):.1f}%")
    print(f"  SKUs raised to MOQ           : {cs.get('n_moq_applied', 0):,}")
    print(f"  SKUs cut by budget           : {cs.get('n_budget_skipped', 0):,}")
    print(f"  SKUs reduced by container    : {cs.get('n_container_constrained', 0):,}")
    print(f"  Total SKUs skipped           : {cs.get('n_skipped_total', 0):,}")
    print(f"  Total SKUs to order          : {cs.get('n_to_order', 0):,}")
    print(f"  Total units to order         : {cs.get('total_units_to_order', 0):,.0f}")

    # ── Save outputs ───────────────────────────────────────────────────
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)

        result.net_need.to_parquet(out / "m5_net_need.parquet", index=False)
        result.constrained_order.to_parquet(out / "m5_constrained_order.parquet", index=False)
        result.import_recommendation.to_parquet(
            out / "m5_import_recommendation.parquet", index=False
        )

        print(f"\n[Saved]")
        for fname in [
            "m5_net_need.parquet",
            "m5_constrained_order.parquet",
            "m5_import_recommendation.parquet",
        ]:
            fpath = out / fname
            size_kb = fpath.stat().st_size // 1024 if fpath.exists() else 0
            status = "PASS" if fpath.exists() else "FAIL"
            print(f"  {status}  {fname} ({size_kb} KB)")
            if not fpath.exists():
                checks_passed = False

        logger.info("Module 5 outputs saved to data/processed/")


def _run_module_6(save: bool) -> None:
    import json

    from src.modules.module6_decision_support import DecisionSupport

    result = DecisionSupport().run()

    # ── Print preview ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("MODULE 6 OUTPUT PREVIEW")
    print("=" * 60)

    print(f"\n[Recommendations]  shape={result.recommendations.shape}")
    print(result.recommendations.head(5).to_string(index=False))

    print(f"\n[Stockout Risk]  shape={result.stockout_risk.shape}")
    print(result.stockout_risk.head(5).to_string(index=False))

    print(f"\n[Overstock Risk]  shape={result.overstock_risk.shape}")
    print(result.overstock_risk.head(5).to_string(index=False))

    print(f"\n[Fill Rate]  shape={result.fill_rate.shape}")
    print(result.fill_rate.head(5).to_string(index=False))

    print(f"\n[Summary KPIs]")
    for k, v in result.summary.items():
        print(f"  {k}: {v}")

    # ── Validation checks ──────────────────────────────────────────────
    print("\n[Validation]")
    checks_passed = True

    # Check 1: recommendations only contain order_qty > 0
    if result.recommendations.empty or not (result.recommendations["order_qty"] > 0).all():
        n_zero = int((result.recommendations["order_qty"] <= 0).sum())
        if n_zero > 0:
            print(f"  FAIL  {n_zero:,} recommendation rows have order_qty <= 0")
            checks_passed = False
        else:
            print(f"  PASS  recommendations: {len(result.recommendations):,} rows, all order_qty > 0")
    else:
        print(f"  PASS  recommendations: {len(result.recommendations):,} rows, all order_qty > 0")

    # Check 2: risk_pct in [0, 100]
    rp = result.stockout_risk["risk_pct"]
    if (rp >= 0).all() and (rp <= 100).all():
        print(f"  PASS  stockout risk_pct in [0, 100] (mean={rp.mean():.1f}%)")
    else:
        print(f"  FAIL  risk_pct has values outside [0, 100]")
        checks_passed = False

    # Check 3: fill_rate_pct in [0, 100]
    fr = result.fill_rate["fill_rate_pct"]
    if (fr >= 0).all() and (fr <= 100).all():
        print(f"  PASS  fill_rate_pct in [0, 100] (weighted mean={result.summary['weighted_fill_rate_pct']:.1f}%)")
    else:
        print(f"  FAIL  fill_rate_pct has values outside [0, 100]")
        checks_passed = False

    # Check 4: summary has all required keys
    required_keys = {
        "total_skus_to_order", "critical_count", "high_count",
        "stockout_risk_count", "overstock_count",
        "weighted_fill_rate_pct", "container_utilization_pct",
    }
    missing = required_keys - set(result.summary.keys())
    if not missing:
        print(f"  PASS  summary has all {len(required_keys)} required KPI keys")
    else:
        print(f"  FAIL  summary missing keys: {missing}")
        checks_passed = False

    # Check 5: overstock rows have excess_qty >= 0
    if not result.overstock_risk.empty:
        if (result.overstock_risk["excess_qty"] >= 0).all():
            print(f"  PASS  overstock: {len(result.overstock_risk):,} SKUs, all excess_qty >= 0")
        else:
            n_neg = int((result.overstock_risk["excess_qty"] < 0).sum())
            print(f"  FAIL  {n_neg:,} overstock rows have excess_qty < 0")
            checks_passed = False
    else:
        print("  PASS  overstock: 0 SKUs flagged (no overstock detected)")

    if checks_passed:
        print("\n  All validation checks PASSED.")
    else:
        print("\n  One or more validation checks FAILED — review output above.")

    # ── Save outputs ───────────────────────────────────────────────────
    if save:
        out = Path("data/processed")
        out.mkdir(parents=True, exist_ok=True)

        result.recommendations.to_parquet(out / "m6_recommendations.parquet", index=False)
        result.stockout_risk.to_parquet(out / "m6_stockout_risk.parquet", index=False)
        result.overstock_risk.to_parquet(out / "m6_overstock_risk.parquet", index=False)
        result.fill_rate.to_parquet(out / "m6_fill_rate.parquet", index=False)

        summary_path = out / "m6_summary.json"
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(result.summary, fh, indent=2)

        print(f"\n[Saved]")
        for fname in [
            "m6_recommendations.parquet",
            "m6_stockout_risk.parquet",
            "m6_overstock_risk.parquet",
            "m6_fill_rate.parquet",
            "m6_summary.json",
        ]:
            fpath = out / fname
            size_kb = fpath.stat().st_size // 1024 if fpath.exists() else 0
            status = "PASS" if fpath.exists() else "FAIL"
            print(f"  {status}  {fname} ({size_kb} KB)")

        logger.info("Module 6 outputs saved to data/processed/")

    # ── Key metrics ────────────────────────────────────────────────────
    print("\n[Key Metrics]")
    s = result.summary
    print(f"  SKUs to order          : {s['total_skus_to_order']:,}")
    print(f"  Critical (Priority 1)  : {s['critical_count']:,}")
    print(f"  High (Priority 2)      : {s['high_count']:,}")
    print(f"  Stockout risk (>30%)   : {s['stockout_risk_count']:,}")
    print(f"  Overstock SKUs         : {s['overstock_count']:,}")
    print(f"  Weighted fill rate     : {s['weighted_fill_rate_pct']:.1f}%")
    print(f"  Container utilisation  : {s['container_utilization_pct']:.1f}%")
    print(f"  Run time               : {result.metadata.get('run_time_seconds', 0):.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a modular pipeline component (modules 1-6).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            f"  {n}: {desc}" for n, desc in MODULE_REGISTRY.items()
        ),
    )
    parser.add_argument(
        "module",
        type=int,
        choices=list(MODULE_REGISTRY.keys()),
        help="Module number to run (1-6).",
        metavar="MODULE",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save outputs to data/processed/ as parquet files.",
    )
    args = parser.parse_args()

    logger.info(f"Running Module {args.module}: {MODULE_REGISTRY[args.module]}")

    if args.module == 1:
        _run_module_1(save=args.save)
    elif args.module == 2:
        _run_module_2(save=args.save)
    elif args.module == 3:
        _run_module_3(save=args.save)
    elif args.module == 4:
        _run_module_4(save=args.save)
    elif args.module == 5:
        _run_module_5(save=args.save)
    elif args.module == 6:
        _run_module_6(save=args.save)


if __name__ == "__main__":
    main()
