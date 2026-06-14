"""Unit tests for Stage 6 Master Data functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.master_data.stage06_master_data import (
    build_part_master,
    build_supersession_map,
    merge_catalog_coverage,
    resolve_supersessions,
)


def _ssop(rows: list[dict]) -> pd.DataFrame:
    """Build minimal SSOP-shaped DataFrame for testing."""
    defaults = {
        "requested_pn":       "AAA-12345-01",
        "intermediate_pn":    "AAA-12345-01",
        "latest_pn":          "AAA-12345-01",
        "description":        "TEST PART",
        "order_qty":          100.0,
        "eod_rate":           1.5,
        "stock":              50.0,
        "on_order":           200.0,
        "revised_order_qty":  0.0,
        "forecast_monthly_qty": 30.0,
        "compatible_models":  "FZ, R15",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ---------------------------------------------------------------------------
# resolve_supersessions
# ---------------------------------------------------------------------------

class TestResolveSupersessions:
    def test_no_supersession_identity(self) -> None:
        df = _ssop([{"requested_pn": "A01", "latest_pn": "A01"}])
        mapping, cycles = resolve_supersessions(df)
        assert mapping["A01"] == "A01"
        assert cycles == []

    def test_single_hop_supersession(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD-001", "latest_pn": "NEW-001"},
            {"requested_pn": "NEW-001", "latest_pn": "NEW-001"},
        ])
        mapping, cycles = resolve_supersessions(df)
        assert mapping["OLD-001"] == "NEW-001"
        assert mapping["NEW-001"] == "NEW-001"

    def test_two_hop_chain(self) -> None:
        """A → B → C should resolve A to C."""
        df = _ssop([
            {"requested_pn": "A", "latest_pn": "B"},
            {"requested_pn": "B", "latest_pn": "C"},
            {"requested_pn": "C", "latest_pn": "C"},
        ])
        mapping, cycles = resolve_supersessions(df)
        assert mapping["A"] == "C"
        assert mapping["B"] == "C"
        assert cycles == []

    def test_cycle_detected(self) -> None:
        """A → B → A is a cycle and must be flagged."""
        df = _ssop([
            {"requested_pn": "A", "latest_pn": "B"},
            {"requested_pn": "B", "latest_pn": "A"},
        ])
        mapping, cycles = resolve_supersessions(df)
        assert len(cycles) >= 1
        # At least one entry in cycles references A or B
        involved = {c["start"] for c in cycles}
        assert involved.intersection({"A", "B"})

    def test_independent_chains(self) -> None:
        df = _ssop([
            {"requested_pn": "X1", "latest_pn": "X2"},
            {"requested_pn": "X2", "latest_pn": "X2"},
            {"requested_pn": "Y1", "latest_pn": "Y3"},
            {"requested_pn": "Y3", "latest_pn": "Y3"},
        ])
        mapping, _ = resolve_supersessions(df)
        assert mapping["X1"] == "X2"
        assert mapping["Y1"] == "Y3"


# ---------------------------------------------------------------------------
# build_part_master
# ---------------------------------------------------------------------------

class TestBuildPartMaster:
    def test_non_superseded_part_kept(self) -> None:
        df = _ssop([{"requested_pn": "P001", "latest_pn": "P001"}])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        assert "P001" in master["part_number"].values

    def test_superseded_part_maps_to_latest(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD", "latest_pn": "NEW", "description": "OLD PART"},
            {"requested_pn": "NEW", "latest_pn": "NEW", "description": "NEW PART"},
        ])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        assert "OLD" not in master["part_number"].values
        assert "NEW" in master["part_number"].values

    def test_superseded_from_populated(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD", "latest_pn": "NEW"},
            {"requested_pn": "NEW", "latest_pn": "NEW"},
        ])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        new_row = master[master["part_number"] == "NEW"]
        assert "OLD" in new_row["superseded_from"].values[0]

    def test_has_supersession_flag(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD", "latest_pn": "NEW"},
            {"requested_pn": "NEW", "latest_pn": "NEW"},
        ])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        new_row = master[master["part_number"] == "NEW"]
        assert bool(new_row["has_supersession"].values[0]) is True

    def test_stock_aggregated(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD", "latest_pn": "NEW", "stock": 10.0},
            {"requested_pn": "NEW", "latest_pn": "NEW", "stock": 20.0},
        ])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        new_row = master[master["part_number"] == "NEW"]
        assert new_row["stock"].values[0] == pytest.approx(30.0)

    def test_all_columns_present(self) -> None:
        df = _ssop([{"requested_pn": "P", "latest_pn": "P"}])
        mapping, _ = resolve_supersessions(df)
        master = build_part_master(df, mapping)
        required = {
            "part_number", "description", "compatible_models",
            "stock", "eod_rate", "on_order", "forecast_monthly_qty",
            "has_supersession", "superseded_from",
        }
        assert required.issubset(set(master.columns))


# ---------------------------------------------------------------------------
# build_supersession_map
# ---------------------------------------------------------------------------

class TestBuildSupersessionMap:
    def test_only_superseded_in_map(self) -> None:
        df = _ssop([
            {"requested_pn": "OLD", "latest_pn": "NEW"},
            {"requested_pn": "NEW", "latest_pn": "NEW"},
        ])
        mapping, _ = resolve_supersessions(df)
        sup_map = build_supersession_map(df, mapping)
        assert "OLD" in sup_map["requested_pn"].values
        assert "NEW" not in sup_map["requested_pn"].values

    def test_hops_counted(self) -> None:
        df = _ssop([
            {"requested_pn": "A", "latest_pn": "B"},
            {"requested_pn": "B", "latest_pn": "C"},
            {"requested_pn": "C", "latest_pn": "C"},
        ])
        mapping, _ = resolve_supersessions(df)
        sup_map = build_supersession_map(df, mapping)
        a_row = sup_map[sup_map["requested_pn"] == "A"]
        assert a_row["hops"].values[0] == 2


# ---------------------------------------------------------------------------
# merge_catalog_coverage
# ---------------------------------------------------------------------------

class TestMergeCatalogCoverage:
    def _make_master(self, part_numbers: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"part_number": part_numbers})

    def test_empty_catalog_gives_blank_column(self) -> None:
        master = self._make_master(["5KA-E1400-11"])
        catalog_df = pd.DataFrame(columns=["part_number", "model", "source_file"])
        result = merge_catalog_coverage(master, catalog_df)
        assert result["catalog_models"].iloc[0] == ""

    def test_matched_part_gets_model(self) -> None:
        master = self._make_master(["5KA-E1400-11"])
        catalog_df = pd.DataFrame([{
            "part_number": "5KA-E1400-11-00",
            "model": "FZ",
            "source_file": "FZ.pdf",
        }])
        result = merge_catalog_coverage(master, catalog_df)
        assert "FZ" in result.loc[result["part_number"] == "5KA-E1400-11", "catalog_models"].values[0]

    def test_multiple_models_concatenated(self) -> None:
        master = self._make_master(["5KA-E1400-11"])
        catalog_df = pd.DataFrame([
            {"part_number": "5KA-E1400-11-00", "model": "FZ",   "source_file": "a.pdf"},
            {"part_number": "5KA-E1400-11-00", "model": "R 15", "source_file": "b.pdf"},
        ])
        result = merge_catalog_coverage(master, catalog_df)
        val = result.loc[result["part_number"] == "5KA-E1400-11", "catalog_models"].values[0]
        assert "FZ" in val
        assert "R 15" in val

    def test_unmatched_part_has_empty_models(self) -> None:
        master = self._make_master(["ZZZ-99999-00"])
        catalog_df = pd.DataFrame([{
            "part_number": "5KA-E1400-11-00", "model": "FZ", "source_file": "a.pdf"
        }])
        result = merge_catalog_coverage(master, catalog_df)
        assert result["catalog_models"].iloc[0] == ""

    def test_9digit_catalog_matches_ssop_directly(self) -> None:
        """9-digit catalog parts (no trailing -WW) should match SSOP without stripping."""
        master = self._make_master(["2FS-E1111-10"])
        catalog_df = pd.DataFrame([{
            "part_number": "2FS-E1111-10",
            "model": "ALFA",
            "source_file": "alfa.pdf",
        }])
        result = merge_catalog_coverage(master, catalog_df)
        assert "ALFA" in result.loc[
            result["part_number"] == "2FS-E1111-10", "catalog_models"
        ].values[0]
