"""Unit tests for Stage 2 unit sales forecast functions."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.models.unit_sales_forecast.stage02_unit_sales_forecast import (
    _mape,
    build_combined_table,
    distribute_annual_target,
    fit_holt_ets,
    fit_linear_trend,
    load_targets,
    save_targets,
    set_annual_target,
    set_monthly_target,
)


def _make_series(n: int = 8, start: str = "2025-05-01") -> pd.DataFrame:
    ds = pd.date_range(start, periods=n, freq="MS")
    y = np.linspace(800, 4000, n).astype(int)
    return pd.DataFrame({"ds": ds, "y": y, "month_str": ds.strftime("%Y-%m")})


class TestMape:
    def test_perfect_forecast(self) -> None:
        a = np.array([100.0, 200.0])
        assert _mape(a, a) == 0.0

    def test_50_percent_error(self) -> None:
        a = np.array([100.0])
        p = np.array([150.0])
        assert abs(_mape(a, p) - 50.0) < 0.01

    def test_ignores_zero_actual(self) -> None:
        a = np.array([0.0, 100.0])
        p = np.array([999.0, 100.0])
        assert _mape(a, p) == 0.0   # only non-zero actual considered


class TestFitHoltETS:
    def test_returns_correct_length(self) -> None:
        series = _make_series(8)
        fc = fit_holt_ets(series, horizon=12)
        assert len(fc) == 8 + 12

    def test_forecast_is_positive(self) -> None:
        series = _make_series(8)
        fc = fit_holt_ets(series, horizon=12)
        assert (fc["yhat"] >= 0).all()

    def test_output_columns(self) -> None:
        series = _make_series(6)
        fc = fit_holt_ets(series, horizon=3)
        assert {"ds", "yhat"}.issubset(fc.columns)


class TestFitLinearTrend:
    def test_upward_trend_forecasts_higher(self) -> None:
        series = _make_series(8)   # linearly increasing
        fc = fit_linear_trend(series, horizon=6)
        last_actual = series["y"].iloc[-1]
        last_fc = fc["yhat"].iloc[-1]
        assert last_fc > last_actual

    def test_no_negative_values(self) -> None:
        # Even with declining trend, clamp at 0
        ds = pd.date_range("2025-05-01", periods=4, freq="MS")
        series = pd.DataFrame({"ds": ds, "y": [1000, 800, 600, 400], "month_str": ds.strftime("%Y-%m")})
        fc = fit_linear_trend(series, horizon=20)
        assert (fc["yhat"] >= 0).all()


class TestDistributeAnnualTarget:
    def test_distributes_proportionally(self) -> None:
        months = ["2026-01", "2026-02", "2026-03"]
        fc_vals = [1000, 2000, 1000]
        dist = distribute_annual_target(40000, months, fc_vals)
        assert sum(dist.values()) == pytest.approx(40000, abs=5)
        assert dist["2026-02"] > dist["2026-01"]

    def test_single_month(self) -> None:
        dist = distribute_annual_target(5000, ["2026-01"], [1000])
        assert dist["2026-01"] == 5000


class TestTargetManagement:
    def test_set_and_load_annual(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target_file = tmp_path / "targets.json"
        monkeypatch.setattr(
            "src.models.unit_sales_forecast.stage02_unit_sales_forecast._TARGETS_JSON",
            target_file,
        )
        set_annual_target(2026, 40000)
        targets = load_targets()
        assert targets["2026"]["annual"] == 40000

    def test_set_monthly_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target_file = tmp_path / "targets.json"
        monkeypatch.setattr(
            "src.models.unit_sales_forecast.stage02_unit_sales_forecast._TARGETS_JSON",
            target_file,
        )
        set_annual_target(2026, 40000)
        set_monthly_target(2026, 3, 5000)
        targets = load_targets()
        assert targets["2026"]["monthly_override"]["2026-03"] == 5000


class TestBuildCombinedTable:
    def test_actuals_not_flagged_as_forecast(self) -> None:
        series = _make_series(8)
        ds_fc = pd.date_range("2026-01-01", periods=12, freq="MS")
        fc_df = pd.DataFrame({
            "ds": pd.concat([series["ds"], pd.Series(ds_fc)]).reset_index(drop=True),
            "forecast": [int(v) for v in np.linspace(1000, 5000, 20)],
            "lower_80": [500] * 20,
            "upper_80": [6000] * 20,
        })
        combined = build_combined_table(series, fc_df)
        assert combined[~combined["is_forecast"]]["actual"].notna().all()
        assert combined[combined["is_forecast"]]["actual"].isna().all()
