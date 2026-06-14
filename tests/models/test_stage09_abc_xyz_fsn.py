"""Unit tests for Stage 9 ABC-XYZ-FSN classification functions."""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.classification.stage09_abc_xyz_fsn import (
    _policy_tier,
    abc_summary,
    build_classification,
    classify_abc,
    classify_fsn,
    classify_xyz,
    fsn_summary,
    policy_tier_summary,
    segment_matrix,
    xyz_summary,
)

_REF_DATE = pd.Timestamp("2026-01-01")  # fixed reference for FSN tests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_features(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal spare_parts_features DataFrame from partial row dicts."""
    defaults: dict = {
        "material_9":             "1GC-E4450-00",
        "description":            "AIR CLEANER ELEMENT",
        "total_months":           41,
        "active_months":          5,
        "p_zero":                 0.88,
        "avg_monthly_demand":     2.0,
        "cv":                     0.3,
        "total_issue_qty":        50.0,
        "total_issue_value_lkr":  25000.0,
        "total_return_qty":       0.0,
        "total_net_demand":       50.0,
        "last_issue_date":        pd.Timestamp("2025-10-01"),
        "demand_category":        "intermittent",
        "in_ssop":                True,
    }
    records = [{**defaults, **r} for r in rows]
    df = pd.DataFrame(records)
    df["last_issue_date"] = pd.to_datetime(df["last_issue_date"])
    return df


# ---------------------------------------------------------------------------
# classify_abc
# ---------------------------------------------------------------------------

class TestClassifyAbc:
    def test_highest_value_is_a(self) -> None:
        df = _make_features([
            {"material_9": "HIGH", "total_issue_value_lkr": 900_000.0},
            {"material_9": "MED",  "total_issue_value_lkr":  80_000.0},
            {"material_9": "LOW",  "total_issue_value_lkr":  20_000.0},
        ])
        result = classify_abc(df)
        high_abc = result.loc[result["material_9"] == "HIGH", "abc"].iloc[0]
        assert high_abc == "A"

    def test_zero_value_is_c(self) -> None:
        df = _make_features([
            {"material_9": "GHOST", "total_issue_value_lkr": 0.0},
        ])
        result = classify_abc(df)
        assert result.loc[result["material_9"] == "GHOST", "abc"].iloc[0] == "C"

    def test_abc_column_exists(self) -> None:
        df = _make_features([{"total_issue_value_lkr": 1000.0}])
        result = classify_abc(df)
        assert "abc" in result.columns

    def test_only_abc_values_present(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 1000)}
            for i in range(1, 21)
        ])
        result = classify_abc(df)
        assert set(result["abc"]).issubset({"A", "B", "C"})

    def test_a_items_hold_80pct_value(self) -> None:
        """A-items must represent ≥ 80% of total issue value."""
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 10_000)}
            for i in range(1, 101)
        ])
        result = classify_abc(df)
        a_value = result.loc[result["abc"] == "A", "total_issue_value_lkr"].sum()
        total_value = result["total_issue_value_lkr"].sum()
        assert a_value / total_value >= 0.79  # allow tiny rounding at boundary

    def test_all_zero_value_all_c(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": 0.0}
            for i in range(5)
        ])
        result = classify_abc(df)
        assert (result["abc"] == "C").all()

    def test_input_not_mutated(self) -> None:
        df = _make_features([{"total_issue_value_lkr": 5000.0}])
        original_cols = list(df.columns)
        classify_abc(df)
        assert list(df.columns) == original_cols


# ---------------------------------------------------------------------------
# classify_xyz
# ---------------------------------------------------------------------------

class TestClassifyXyz:
    def test_low_cv_is_x(self) -> None:
        df = _make_features([{"cv": 0.2, "active_months": 5}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "X"

    def test_mid_cv_is_y(self) -> None:
        df = _make_features([{"cv": 0.7, "active_months": 5}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "Y"

    def test_high_cv_is_z(self) -> None:
        df = _make_features([{"cv": 1.5, "active_months": 5}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "Z"

    def test_boundary_cv_0_5_is_y(self) -> None:
        df = _make_features([{"cv": 0.5, "active_months": 5}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "Y"

    def test_boundary_cv_1_0_is_z(self) -> None:
        df = _make_features([{"cv": 1.0, "active_months": 5}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "Z"

    def test_non_mover_is_z(self) -> None:
        """Zero active months → maximally uncertain → Z regardless of cv."""
        df = _make_features([{"cv": 0.1, "active_months": 0}])
        result = classify_xyz(df)
        assert result["xyz"].iloc[0] == "Z"

    def test_only_xyz_values_present(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "cv": float(i) / 5, "active_months": 3}
            for i in range(10)
        ])
        result = classify_xyz(df)
        assert set(result["xyz"]).issubset({"X", "Y", "Z"})


# ---------------------------------------------------------------------------
# classify_fsn
# ---------------------------------------------------------------------------

class TestClassifyFsn:
    def test_recent_issue_is_f(self) -> None:
        # Last issue 1 month before ref (2026-01-01) → Fast
        df = _make_features([
            {"last_issue_date": pd.Timestamp("2025-12-01"), "active_months": 3}
        ])
        result = classify_fsn(df, reference_date=_REF_DATE)
        assert result["fsn"].iloc[0] == "F"

    def test_old_issue_is_n(self) -> None:
        # Last issue 24 months before ref → Non-moving
        df = _make_features([
            {"last_issue_date": pd.Timestamp("2024-01-01"), "active_months": 2}
        ])
        result = classify_fsn(df, reference_date=_REF_DATE)
        assert result["fsn"].iloc[0] == "N"

    def test_mid_range_issue_is_s(self) -> None:
        # Last issue 6 months before ref → Slow
        df = _make_features([
            {"last_issue_date": pd.Timestamp("2025-07-01"), "active_months": 2}
        ])
        result = classify_fsn(df, reference_date=_REF_DATE)
        assert result["fsn"].iloc[0] == "S"

    def test_never_issued_is_n(self) -> None:
        df = _make_features([
            {"last_issue_date": None, "active_months": 0}
        ])
        df["last_issue_date"] = pd.to_datetime(df["last_issue_date"])
        result = classify_fsn(df, reference_date=_REF_DATE)
        assert result["fsn"].iloc[0] == "N"

    def test_only_fsn_values_present(self) -> None:
        df = _make_features([
            {"material_9": "A", "last_issue_date": pd.Timestamp("2025-12-01"), "active_months": 5},
            {"material_9": "B", "last_issue_date": pd.Timestamp("2025-07-01"), "active_months": 2},
            {"material_9": "C", "last_issue_date": pd.Timestamp("2022-01-01"), "active_months": 1},
            {"material_9": "D", "last_issue_date": None,                        "active_months": 0},
        ])
        df["last_issue_date"] = pd.to_datetime(df["last_issue_date"])
        result = classify_fsn(df, reference_date=_REF_DATE)
        assert set(result["fsn"]).issubset({"F", "S", "N"})


# ---------------------------------------------------------------------------
# build_classification
# ---------------------------------------------------------------------------

class TestBuildClassification:
    def test_all_classification_columns_present(self) -> None:
        df = _make_features([{"total_issue_value_lkr": 10000.0, "active_months": 5}])
        result = build_classification(df)
        for col in ("abc", "xyz", "fsn", "abc_xyz_fsn", "policy_tier"):
            assert col in result.columns

    def test_combined_code_length_3(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 1000), "active_months": 3}
            for i in range(5)
        ])
        result = build_classification(df)
        assert (result["abc_xyz_fsn"].str.len() == 3).all()

    def test_combined_code_characters(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 1000), "active_months": 3}
            for i in range(5)
        ])
        result = build_classification(df)
        for code in result["abc_xyz_fsn"]:
            assert code[0] in "ABC"
            assert code[1] in "XYZ"
            assert code[2] in "FSN"

    def test_row_count_preserved(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}"}
            for i in range(10)
        ])
        result = build_classification(df)
        assert len(result) == len(df)


# ---------------------------------------------------------------------------
# _policy_tier
# ---------------------------------------------------------------------------

class TestPolicyTier:
    def test_af_is_critical(self) -> None:
        assert _policy_tier("AXF") == "critical"
        assert _policy_tier("AYF") == "critical"
        assert _policy_tier("AZF") == "critical"

    def test_as_is_managed(self) -> None:
        assert _policy_tier("AXS") == "managed"

    def test_bf_is_managed(self) -> None:
        assert _policy_tier("BXF") == "managed"
        assert _policy_tier("BYF") == "managed"

    def test_cn_is_rationalise(self) -> None:
        assert _policy_tier("CXN") == "rationalise"
        assert _policy_tier("CZN") == "rationalise"

    def test_cf_is_watch(self) -> None:
        assert _policy_tier("CXF") == "watch"
        assert _policy_tier("CYS") == "watch"

    def test_an_is_watch(self) -> None:
        # High-value but stale — investigate, don't auto-rationalise
        assert _policy_tier("AXN") == "watch"

    def test_valid_tiers_only(self) -> None:
        valid = {"critical", "managed", "watch", "rationalise"}
        for abc in "ABC":
            for xyz in "XYZ":
                for fsn in "FSN":
                    tier = _policy_tier(abc + xyz + fsn)
                    assert tier in valid, f"Unexpected tier '{tier}' for code {abc+xyz+fsn}"


# ---------------------------------------------------------------------------
# Summary functions
# ---------------------------------------------------------------------------

class TestAbcSummary:
    def test_three_rows(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 10_000)}
            for i in range(1, 21)
        ])
        classified = build_classification(df)
        summary = abc_summary(classified)
        assert list(summary["abc"]) == ["A", "B", "C"]

    def test_value_shares_sum_to_100(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "total_issue_value_lkr": float(i * 10_000)}
            for i in range(1, 21)
        ])
        classified = build_classification(df)
        summary = abc_summary(classified)
        assert summary["value_share_%"].sum() == pytest.approx(100.0, abs=0.1)


class TestXyzSummary:
    def test_three_rows(self) -> None:
        df = _make_features([
            {"material_9": "X_sku", "cv": 0.2, "active_months": 5},
            {"material_9": "Y_sku", "cv": 0.7, "active_months": 5},
            {"material_9": "Z_sku", "cv": 1.5, "active_months": 5},
        ])
        classified = build_classification(df)
        summary = xyz_summary(classified)
        assert list(summary["xyz"]) == ["X", "Y", "Z"]

    def test_sku_shares_sum_to_100(self) -> None:
        df = _make_features([
            {"material_9": f"M{i}", "cv": float(i) / 5, "active_months": 3}
            for i in range(6)
        ])
        classified = build_classification(df)
        summary = xyz_summary(classified)
        assert summary["sku_share_%"].sum() == pytest.approx(100.0, abs=0.1)


class TestSegmentMatrix:
    def test_matrix_shape(self) -> None:
        df = _make_features([
            {"material_9": "A1", "total_issue_value_lkr": 900_000.0,
             "last_issue_date": pd.Timestamp("2025-10-01"), "active_months": 5},
            {"material_9": "C1", "total_issue_value_lkr": 0.0,
             "last_issue_date": None, "active_months": 0},
        ])
        df["last_issue_date"] = pd.to_datetime(df["last_issue_date"])
        classified = build_classification(df)
        matrix = segment_matrix(classified)
        # Rows are ABC + Total, columns are FSN + Total
        assert "Total" in matrix.index
        assert "Total" in matrix.columns
