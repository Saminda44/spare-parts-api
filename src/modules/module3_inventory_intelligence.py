"""Module 3: Inventory Intelligence.

Sub-components:
  1. Current Stock          — on-hand inventory per SKU (SAP snapshot from current_stock.xlsx)
  2. Pipeline Inventory     — import orders placed but not yet received (derived from
                              MT 641 / MT 101 movement-type netting in In_and_Out.xlsx)
  3. Outstanding Backorders — dealer orders confirmed short (lost_qty < 0 in orders_clean)
  4. Inventory Position     — net position = stock + pipeline - backorders

Design: wrap-and-promote.  Existing stage logic is unchanged.  This module
reads the cleaned interim parquets directly and adds Module 3-level aggregations
and the inventory-position computation that downstream modules (ROL/ROQ) need.

Outputs (returned in InventoryIntelligenceResult):
  current_stock      : [part_no, stock_qty, unit, description]
  pipeline_inventory : [part_no, ordered_qty, expected_receipt_date]
  backorders         : [part_no, backorder_qty]
  inventory_position : [part_no, stock_qty, pipeline_qty, backorder_qty, net_position]

Upstream parquet dependencies (must exist before calling run()):
  data/interim/current_stock.parquet   — cleaned current_stock.xlsx (all storage locs)
  data/interim/in_and_out.parquet      — cleaned In_and_Out.xlsx (all movements)
  data/interim/orders_clean.parquet    — stage04 output, dealer-scoped PO + return lines

SAP movement-type conventions used here:
  MT 641  Transfer to stock in transit    (goods issued from source — negative qty)
  MT 642  Reversal of MT 641              (cancel / return to source)
  MT 101  Goods Receipt from PO/STO       (goods received at destination — positive qty)
  MT 102  Reversal of MT 101              (GR reversal)

Pipeline qty per material  =  (|MT 641|  −  |MT 642|)  −  (MT 101  −  |MT 102|)
                              ───────────────────────      ────────────────────────
                              net transferred                  net received

Clipped to 0 (negative = received more than transferred, data gap artefact).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from loguru import logger

from src.config.constants import LEAD_TIME_DAYS
from src.config.paths import DATA_INTERIM
from src.ingestion.cleaner import load_clean

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: SAP movement types used to derive pipeline inventory
_MT_TRANSFER_OUT: int = 641   # Transfer to stock in transit (qty negative at source)
_MT_TRANSFER_REV: int = 642   # Reversal of MT 641
_MT_GOODS_RECEIPT: int = 101  # Goods Receipt from PO/STO
_MT_GR_REVERSAL: int = 102    # Goods Receipt reversal

#: The only active plant code in the current data
_PLANT_CODE: str = "W1B4"

#: orders_clean parquet (stage04 output — already dealer-scoped)
_ORDERS_CLEAN_PARQUET = DATA_INTERIM / "orders_clean.parquet"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class InventoryIntelligenceResult:
    """Typed container for all Module 3 outputs.

    Attributes:
        current_stock:      On-hand inventory per SKU from the SAP stock snapshot.
        pipeline_inventory: Goods in transit (ordered from supplier, not yet received).
        backorders:         Confirmed short-shipments on dealer purchase orders.
        inventory_position: Net inventory = stock + pipeline - backorders.
        metadata:           Run diagnostics (row counts, totals, timings, warnings).
    """

    current_stock: pd.DataFrame
    pipeline_inventory: pd.DataFrame
    backorders: pd.DataFrame
    inventory_position: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class InventoryIntelligence:
    """Module 3: Inventory Intelligence.

    Consolidates four inventory signals into a net inventory position per SKU
    that downstream modules (ROL / ROQ / Buffer policy) use for safety-stock
    and replenishment planning.

    Pipeline inventory is derived by netting movement types in In_and_Out.xlsx:
      net_pipeline = (|MT641| − |MT642|) − (MT101 − |MT102|), clipped to zero.
    This mirrors the SAP "Transit and Transfer" stock view from the MB52 report.
    """

    def __init__(self) -> None:
        logger.info("InventoryIntelligence: initialised")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> InventoryIntelligenceResult:
        """Run all four sub-components and return a typed result.

        Returns:
            InventoryIntelligenceResult with all four DataFrames plus run metadata.

        Raises:
            FileNotFoundError: If required upstream parquets are missing.
        """
        t0 = time.perf_counter()
        metadata: dict[str, Any] = {
            "lead_time_days": LEAD_TIME_DAYS,
            "warnings": [],
        }

        logger.info("=" * 60)
        logger.info("MODULE 3: INVENTORY INTELLIGENCE")
        logger.info("=" * 60)

        # ── Sub-component 1: current stock ────────────────────────────
        logger.info("Sub-component 1/4: current stock (SAP snapshot)")
        t1 = time.perf_counter()
        current_stock = self._load_current_stock()
        metadata["cs_skus_total"] = int(current_stock["part_no"].nunique())
        metadata["cs_skus_with_stock"] = int((current_stock["stock_qty"] > 0).sum())
        metadata["cs_total_stock_qty"] = float(current_stock["stock_qty"].sum())
        logger.info(
            f"  -> {current_stock.shape} | "
            f"{metadata['cs_skus_with_stock']:,} SKUs with stock > 0 | "
            f"total qty = {metadata['cs_total_stock_qty']:,.0f} "
            f"in {time.perf_counter() - t1:.2f}s"
        )

        # ── Sub-component 2: pipeline inventory ───────────────────────
        logger.info("Sub-component 2/4: pipeline inventory (MT641/101 netting)")
        t2 = time.perf_counter()
        pipeline_inventory = self._load_pipeline_inventory(metadata)
        metadata["pi_skus"] = int(pipeline_inventory["part_no"].nunique())
        metadata["pi_total_ordered_qty"] = float(pipeline_inventory["ordered_qty"].sum())
        logger.info(
            f"  -> {pipeline_inventory.shape} | "
            f"total ordered qty = {metadata['pi_total_ordered_qty']:,.0f} "
            f"in {time.perf_counter() - t2:.2f}s"
        )

        # ── Sub-component 3: backorders ───────────────────────────────
        logger.info("Sub-component 3/4: outstanding backorders (short-shipped PO lines)")
        t3 = time.perf_counter()
        backorders = self._load_backorders(metadata)
        metadata["bo_skus"] = int(backorders["part_no"].nunique())
        metadata["bo_total_backorder_qty"] = float(backorders["backorder_qty"].sum())
        logger.info(
            f"  -> {backorders.shape} | "
            f"total backorder qty = {metadata['bo_total_backorder_qty']:,.0f} "
            f"in {time.perf_counter() - t3:.2f}s"
        )

        # ── Sub-component 4: inventory position ──────────────────────
        logger.info("Sub-component 4/4: inventory position (net = stock + pipeline - backorders)")
        t4 = time.perf_counter()
        inventory_position = self._calc_inventory_position(
            current_stock, pipeline_inventory, backorders
        )
        metadata["ip_skus_total"] = int(inventory_position["part_no"].nunique())
        metadata["ip_skus_positive"] = int((inventory_position["net_position"] > 0).sum())
        metadata["ip_skus_negative"] = int((inventory_position["net_position"] < 0).sum())
        metadata["ip_total_net_position"] = float(inventory_position["net_position"].sum())
        logger.info(
            f"  -> {inventory_position.shape} | "
            f"{metadata['ip_skus_positive']:,} positive | "
            f"{metadata['ip_skus_negative']:,} negative "
            f"in {time.perf_counter() - t4:.2f}s"
        )

        # ── Summary ───────────────────────────────────────────────────
        elapsed = time.perf_counter() - t0
        metadata["run_time_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info("MODULE 3 COMPLETE")
        logger.info(f"  Current stock      : {current_stock.shape}  (sum_qty={metadata['cs_total_stock_qty']:,.0f})")
        logger.info(f"  Pipeline inventory : {pipeline_inventory.shape}  (sum_qty={metadata['pi_total_ordered_qty']:,.0f})")
        logger.info(f"  Backorders         : {backorders.shape}  (sum_qty={metadata['bo_total_backorder_qty']:,.0f})")
        logger.info(f"  Inventory position : {inventory_position.shape}  (sum_net={metadata['ip_total_net_position']:,.0f})")
        logger.info(f"  Total time         : {elapsed:.2f}s")
        if metadata["warnings"]:
            for w in metadata["warnings"]:
                logger.warning(f"  WARN: {w}")
        logger.info("=" * 60)

        return InventoryIntelligenceResult(
            current_stock=current_stock,
            pipeline_inventory=pipeline_inventory,
            backorders=backorders,
            inventory_position=inventory_position,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Sub-component 1: current stock
    # ------------------------------------------------------------------

    def _load_current_stock(self) -> pd.DataFrame:
        """Load and aggregate on-hand inventory from the SAP stock snapshot.

        Business meaning: current_stock.xlsx is a SAP MB52 (warehouse stocks)
        report exported at a point in time.  Each row is a (Material, StorageLocation)
        combination.  Summing `Unrestricted` across all storage locations for a
        given Material gives the total unrestricted-use on-hand stock for that SKU.

        The `Unrestricted` column contains the quantity available for immediate
        issue to customers.  It excludes quality-inspection stock, restricted
        stock, blocked stock, and in-transit quantities.

        Rows where Plant != 'W1B4' (the Seeduwa PDC) are dropped — these are
        duplicate header rows that appear in the SAP export artefact.

        Args:
            (none — uses load_clean("current_stock"))

        Returns:
            DataFrame with columns:
                part_no     (str)   — SAP material code (may be integer-format or
                                      alphanumeric Yamaha/OEM format)
                stock_qty   (float) — total unrestricted stock across all locs
                unit        (str)   — base unit of measure (e.g. "EA", "PCS")
                description (str)   — material description text

        Raises:
            FileNotFoundError: If current_stock.parquet is not found.
        """
        cs = load_clean("current_stock")
        logger.debug(f"  current_stock raw: {cs.shape}")

        # ── Drop SAP export artefact rows (Plant = 'Plnt' = repeated header) ──
        cs = cs[cs["Plnt"] == _PLANT_CODE].copy()
        logger.debug(f"  After plant filter (W1B4): {cs.shape}")

        # ── Coerce Unrestricted to numeric ────────────────────────────
        cs["Unrestricted"] = pd.to_numeric(cs["Unrestricted"], errors="coerce").fillna(0.0)

        # ── Normalise material code ───────────────────────────────────
        cs["Material"] = cs["Material"].astype(str).str.strip()

        # ── Aggregate by Material across all storage locations ────────
        # BUn (base unit) and Material Description are material-level attributes;
        # take the first non-null value for each (they should be the same across rows).
        agg = (
            cs.groupby("Material", sort=False)
            .agg(
                stock_qty=("Unrestricted", "sum"),
                unit=("BUn", "first"),
                description=("Material Description", "first"),
            )
            .reset_index()
            .rename(columns={"Material": "part_no"})
        )

        # ── Remove materials with no meaningful description ────────────
        # (Mostly SAP internal material numbers with no part record)
        agg["unit"] = agg["unit"].fillna("EA")
        agg["description"] = agg["description"].fillna("").astype(str)

        result = agg.sort_values("part_no").reset_index(drop=True)

        logger.info(
            f"  Current stock: {len(result):,} unique materials | "
            f"with stock > 0: {(result['stock_qty'] > 0).sum():,} | "
            f"total qty: {result['stock_qty'].sum():,.0f}"
        )
        return result[["part_no", "stock_qty", "unit", "description"]]

    # ------------------------------------------------------------------
    # Sub-component 2: pipeline inventory
    # ------------------------------------------------------------------

    def _load_pipeline_inventory(self, metadata: dict[str, Any]) -> pd.DataFrame:
        """Derive in-transit (pipeline) inventory from movement-type netting.

        Business meaning: goods ordered from the Indian supplier and in transit
        to Sri Lanka have NOT yet been posted as a Goods Receipt (MT 101) in SAP.
        They appear as MT 641 "Transfer to stock in transit" movements at the
        supplying plant.  The net pipeline quantity per material is:

          net_pipeline = (|MT 641 qty| − |MT 642 qty|)   ← net transferred
                       − (  MT 101 qty − |MT 102 qty|)   ← net received
          clipped to 0 (negative = received more than transferred, data gap)

        Expected receipt date is approximated as the latest MT 641 posting
        date for each material plus LEAD_TIME_DAYS (90 days from CLAUDE.md).

        Args:
            metadata: Mutable dict; warnings are appended here.

        Returns:
            DataFrame with columns:
                part_no              (str)
                ordered_qty          (float) — net quantity still in transit
                expected_receipt_date (pd.Timestamp) — estimated arrival date

        Raises:
            FileNotFoundError: If in_and_out.parquet is not found.
        """
        io = load_clean("in_and_out")
        logger.debug(f"  in_and_out raw: {io.shape}")

        # ── Keep only rows with a valid material code ─────────────────
        io = io[io["Material"].notna()].copy()
        io["Material"] = io["Material"].astype(str).str.strip()
        io["qty"] = pd.to_numeric(io["Qty in unit of entry"], errors="coerce").fillna(0.0)
        io["posting_date"] = pd.to_datetime(io["Posting Date"], errors="coerce")

        # ── Movement-type aggregations ────────────────────────────────
        def _abs_sum(mt: int) -> pd.Series:
            """Return sum of absolute qty for a given movement type, indexed by Material."""
            sub = io[io["Movement Type"] == mt]
            return sub.groupby("Material")["qty"].apply(lambda x: x.abs().sum())

        def _pos_sum(mt: int) -> pd.Series:
            """Return sum of positive qty for a given movement type, indexed by Material."""
            sub = io[io["Movement Type"] == mt]
            return sub.groupby("Material")["qty"].apply(lambda x: x.clip(lower=0.0).sum())

        transferred   = _abs_sum(_MT_TRANSFER_OUT)   # |MT 641|: qty leaving source
        transfer_rev  = _abs_sum(_MT_TRANSFER_REV)   # |MT 642|: cancelled transfers
        received      = _pos_sum(_MT_GOODS_RECEIPT)  # MT 101: qty received at dest
        received_rev  = _abs_sum(_MT_GR_REVERSAL)    # |MT 102|: GR reversals

        logger.debug(
            f"  MT counts: 641={len(io[io['Movement Type']==641]):,} | "
            f"642={len(io[io['Movement Type']==642]):,} | "
            f"101={len(io[io['Movement Type']==101]):,} | "
            f"102={len(io[io['Movement Type']==102]):,}"
        )

        # ── Net pipeline per material ──────────────────────────────────
        combined = pd.DataFrame(
            {
                "transferred":  transferred,
                "transfer_rev": transfer_rev,
                "received":     received,
                "received_rev": received_rev,
            }
        ).fillna(0.0)

        combined["ordered_qty"] = (
            (combined["transferred"] - combined["transfer_rev"])
            - (combined["received"] - combined["received_rev"])
        ).clip(lower=0.0)

        pipeline = (
            combined[combined["ordered_qty"] > 0]
            .reset_index()
            .rename(columns={"Material": "part_no"})
        )

        if pipeline.empty:
            metadata["warnings"].append(
                "No pipeline inventory found from MT641/101 netting — "
                "check if In_and_Out.xlsx contains transfer movements"
            )
            logger.warning("  Pipeline inventory is empty after MT641/101 netting")
            return pd.DataFrame(
                columns=["part_no", "ordered_qty", "expected_receipt_date"]
            )

        # ── Expected receipt date ─────────────────────────────────────
        # Latest MT 641 posting date per material + lead time
        last_transfer = (
            io[io["Movement Type"] == _MT_TRANSFER_OUT]
            .groupby("Material")["posting_date"]
            .max()
            .reset_index()
            .rename(columns={"Material": "part_no", "posting_date": "last_transfer_date"})
        )
        pipeline = pipeline.merge(last_transfer, on="part_no", how="left")
        pipeline["expected_receipt_date"] = (
            pipeline["last_transfer_date"] + pd.Timedelta(days=LEAD_TIME_DAYS)
        )

        # ── Audit: stale pipeline items (last transfer date > 6 months ago) ────
        data_max_date = io["posting_date"].max()
        stale_cutoff = data_max_date - pd.Timedelta(days=180)
        stale_count = int(
            (pipeline["last_transfer_date"] < stale_cutoff).sum()
        )
        if stale_count > 0:
            metadata["warnings"].append(
                f"{stale_count:,} pipeline materials have last transfer date > 6 months "
                f"before data end ({data_max_date.date() if pd.notna(data_max_date) else 'unknown'}). "
                "These may be un-matched historical transfers rather than active pipeline."
            )
            logger.warning(
                f"  {stale_count:,} pipeline materials have stale transfer dates "
                f"(< {stale_cutoff.date() if pd.notna(stale_cutoff) else 'unknown'})"
            )

        logger.info(
            f"  Pipeline inventory: {len(pipeline):,} materials | "
            f"total in-transit qty = {pipeline['ordered_qty'].sum():,.0f}"
        )
        return pipeline[["part_no", "ordered_qty", "expected_receipt_date"]].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 3: backorders
    # ------------------------------------------------------------------

    def _load_backorders(self, metadata: dict[str, Any]) -> pd.DataFrame:
        """Aggregate outstanding dealer backorders from orders_clean.

        Business meaning: a backorder arises when a dealer's confirmed quantity
        (the quantity the distributor could supply) is less than the dealer's
        requested order quantity.  Per CLAUDE.md:
          lost_qty = Confirmed Quantity − Order Quantity  (negative = short-shipped)
        Lines where lost_qty < 0 are partially or fully unfulfilled and represent
        demand that was not met.  Summing |lost_qty| per material gives the
        total outstanding backorder quantity.

        Only `doc_type == 'PO'` lines are included (SD Document Category C,
        Sales Document starting with '4').  Return lines are excluded.

        Args:
            metadata: Mutable dict; warnings are appended here.

        Returns:
            DataFrame with columns:
                part_no       (str)
                backorder_qty (float) — total unfulfilled quantity across all lines

        Raises:
            FileNotFoundError: If orders_clean.parquet is missing.
        """
        if not _ORDERS_CLEAN_PARQUET.exists():
            raise FileNotFoundError(
                f"orders_clean.parquet not found at {_ORDERS_CLEAN_PARQUET}. "
                "Run stage04 first: python -m scripts.run_stage 4"
            )

        oc = pd.read_parquet(_ORDERS_CLEAN_PARQUET)
        logger.debug(f"  orders_clean loaded: {oc.shape}")

        # Purchase orders only (exclude returns)
        po = oc[oc["doc_type"] == "PO"].copy()
        logger.debug(f"  PO rows: {len(po):,}")

        if po.empty:
            metadata["warnings"].append("No PO rows in orders_clean — backorders will be empty")
            logger.warning("  No PO rows found in orders_clean")
            return pd.DataFrame(columns=["part_no", "backorder_qty"])

        # Ensure lost_qty is numeric
        po["lost_qty"] = pd.to_numeric(po["lost_qty"], errors="coerce").fillna(0.0)

        # Backorder lines: confirmed qty < order qty → lost_qty < 0
        bo_lines = po[po["lost_qty"] < 0].copy()

        if bo_lines.empty:
            logger.info("  No short-shipped PO lines found — backorders DataFrame is empty")
            return pd.DataFrame(columns=["part_no", "backorder_qty"])

        logger.debug(f"  Short-shipped PO lines: {len(bo_lines):,}")

        # backorder_qty = |lost_qty| = Order Quantity − Confirmed Quantity
        bo_lines["backorder_qty"] = bo_lines["lost_qty"].abs()

        # Aggregate by Material
        backorders = (
            bo_lines.groupby("Material", sort=False)["backorder_qty"]
            .sum()
            .reset_index()
            .rename(columns={"Material": "part_no"})
        )
        backorders["part_no"] = backorders["part_no"].astype(str).str.strip()
        backorders = backorders[backorders["backorder_qty"] > 0].copy()

        logger.info(
            f"  Backorders: {len(backorders):,} materials | "
            f"total backorder qty = {backorders['backorder_qty'].sum():,.0f}"
        )
        return backorders[["part_no", "backorder_qty"]].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Sub-component 4: inventory position
    # ------------------------------------------------------------------

    def _calc_inventory_position(
        self,
        current_stock: pd.DataFrame,
        pipeline_inventory: pd.DataFrame,
        backorders: pd.DataFrame,
    ) -> pd.DataFrame:
        """Merge stock, pipeline, and backorders into a net inventory position.

        Business meaning: the inventory position is the true picture of supply
        available to meet future demand.  It combines:
          - what is physically on hand (unrestricted stock in SAP)
          - what is ordered and in transit (pipeline)
          - what dealers have already ordered but we have not yet supplied (backorders)

        Formula:
          net_position = stock_qty + pipeline_qty − backorder_qty

        A negative net_position indicates the distributor is already "in the hole"
        (backorders exceed available supply), even before new demand is considered.

        Merge strategy: outer join so all materials from any source are included.
        Missing values are filled with 0.

        Args:
            current_stock:      [part_no, stock_qty, ...] from _load_current_stock()
            pipeline_inventory: [part_no, ordered_qty, ...] from _load_pipeline_inventory()
            backorders:         [part_no, backorder_qty] from _load_backorders()

        Returns:
            DataFrame with columns:
                part_no       (str)
                stock_qty     (float) — on-hand unrestricted stock
                pipeline_qty  (float) — in-transit / not-yet-received qty
                backorder_qty (float) — outstanding unfulfilled dealer demand
                net_position  (float) — stock + pipeline − backorders
        """
        # Extract only the columns needed for the merge (other columns stay in
        # their sub-component DataFrames for detailed drill-down)
        cs_sub = current_stock[["part_no", "stock_qty"]].copy()

        # Rename ordered_qty → pipeline_qty for the position calculation
        pl_sub = (
            pipeline_inventory[["part_no", "ordered_qty"]].copy()
            if not pipeline_inventory.empty
            else pd.DataFrame(columns=["part_no", "ordered_qty"])
        ).rename(columns={"ordered_qty": "pipeline_qty"})

        bo_sub = (
            backorders[["part_no", "backorder_qty"]].copy()
            if not backorders.empty
            else pd.DataFrame(columns=["part_no", "backorder_qty"])
        )

        # Outer joins so no material is dropped
        pos = cs_sub.merge(pl_sub, on="part_no", how="outer")
        pos = pos.merge(bo_sub, on="part_no", how="outer")

        # Fill missing quantities with 0
        for col in ("stock_qty", "pipeline_qty", "backorder_qty"):
            pos[col] = pos[col].fillna(0.0)

        # Net position
        pos["net_position"] = pos["stock_qty"] + pos["pipeline_qty"] - pos["backorder_qty"]

        result = pos.sort_values("part_no").reset_index(drop=True)

        logger.info(
            f"  Inventory position: {len(result):,} total materials | "
            f"positive: {(result['net_position'] > 0).sum():,} | "
            f"zero: {(result['net_position'] == 0).sum():,} | "
            f"negative: {(result['net_position'] < 0).sum():,}"
        )
        return result[["part_no", "stock_qty", "pipeline_qty", "backorder_qty", "net_position"]]
