"""Unit tests for Stage 3 UIO Forecast functions."""

import pandas as pd
import pytest

from src.models.uio_forecast.stage03_uio_forecast import (
    get_model_mix,
    monthly_attrition_rate,
    project_uio_by_model,
    project_uio_total,
)


def _make_uio(models_units: dict[str, int]) -> pd.DataFrame:
    rows = [{"Model": m, "UIO": u, "UIO_pct": 0.0} for m, u in models_units.items()]
    df = pd.DataFrame(rows)
    total = df["UIO"].sum()
    df["UIO_pct"] = df["UIO"] / total * 100
    return df


class TestMonthlyAttritionRate:
    def test_zero_annual_gives_zero_monthly(self) -> None:
        assert monthly_attrition_rate(0.0) == pytest.approx(0.0)

    def test_100_annual_gives_100_monthly(self) -> None:
        assert monthly_attrition_rate(1.0) == pytest.approx(1.0)

    def test_compounding_correct(self) -> None:
        # 12 months of monthly_rate should equal annual_rate loss on initial stock
        annual = 0.12
        mrate = monthly_attrition_rate(annual)
        remaining = (1 - mrate) ** 12
        assert remaining == pytest.approx(1 - annual, abs=1e-6)


class TestGetModelMix:
    def test_shares_sum_to_one(self) -> None:
        uio = _make_uio({"A": 300, "B": 500, "C": 200})
        mix = get_model_mix(uio)
        assert sum(mix.values()) == pytest.approx(1.0)

    def test_proportional_to_uio(self) -> None:
        uio = _make_uio({"A": 1000, "B": 1000})
        mix = get_model_mix(uio)
        assert mix["A"] == pytest.approx(0.5)
        assert mix["B"] == pytest.approx(0.5)

    def test_zero_uio_equal_share(self) -> None:
        uio = _make_uio({"A": 0, "B": 0})
        mix = get_model_mix(uio)
        assert mix["A"] == pytest.approx(0.5)
        assert mix["B"] == pytest.approx(0.5)


class TestProjectUioTotal:
    def test_no_attrition_grows_by_sales(self) -> None:
        base   = 10_000
        sales  = [500] * 3
        result = project_uio_total(base, sales, annual_attrition_rate=0.0)
        assert result == [10_500, 11_000, 11_500]

    def test_attrition_reduces_growth(self) -> None:
        base   = 10_000
        sales  = [500] * 12
        no_attr  = project_uio_total(base, sales, annual_attrition_rate=0.0)
        with_attr = project_uio_total(base, sales, annual_attrition_rate=0.10)
        assert with_attr[-1] < no_attr[-1]

    def test_no_negative_uio(self) -> None:
        base   = 100
        sales  = [0] * 24
        result = project_uio_total(base, sales, annual_attrition_rate=1.0)
        assert all(v >= 0 for v in result)

    def test_correct_length(self) -> None:
        result = project_uio_total(5_000, [100] * 36, annual_attrition_rate=0.05)
        assert len(result) == 36


class TestProjectUioByModel:
    def test_total_matches_project_total(self) -> None:
        uio   = _make_uio({"A": 6_000, "B": 4_000})
        mix   = get_model_mix(uio)
        sales = [1_000] * 6
        by_model = project_uio_by_model(uio, sales, mix, annual_attrition_rate=0.05)
        total_proj = project_uio_total(10_000, sales, annual_attrition_rate=0.05)

        for t in range(6):
            month_total = by_model[by_model["period_idx"] == t]["UIO"].sum()
            assert month_total == pytest.approx(total_proj[t], abs=5)

    def test_output_shape(self) -> None:
        uio   = _make_uio({"A": 500, "B": 300, "C": 200})
        mix   = get_model_mix(uio)
        result = project_uio_by_model(uio, [100] * 4, mix, annual_attrition_rate=0.05)
        assert set(result.columns) >= {"period_idx", "Model", "UIO"}
        assert len(result) == 4 * 3   # 4 months × 3 models

    def test_higher_share_model_stays_larger(self) -> None:
        uio  = _make_uio({"Big": 8_000, "Small": 2_000})
        mix  = get_model_mix(uio)
        proj = project_uio_by_model(uio, [500] * 12, mix, annual_attrition_rate=0.05)
        last = proj[proj["period_idx"] == 11]
        big_uio   = last.loc[last["Model"] == "Big",   "UIO"].values[0]
        small_uio = last.loc[last["Model"] == "Small", "UIO"].values[0]
        assert big_uio > small_uio
