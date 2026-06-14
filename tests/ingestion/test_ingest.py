"""Tests for hash-dedup ingestion logic."""

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.ingestion.ingest import _row_hash, append_deduplicated


def make_df(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "VIN": [f"VIN{i:04d}" for i in range(n)],
        "Posting Date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "Material": ["MAT001"] * n,
        "SlsVolQty": [1.0] * n,
    })


def test_hash_is_deterministic() -> None:
    df = make_df()
    h1 = _row_hash(df, ["VIN", "Material"])
    h2 = _row_hash(df, ["VIN", "Material"])
    assert (h1 == h2).all()


def test_hash_differs_on_different_keys() -> None:
    df = make_df(2)
    hashes = _row_hash(df, ["VIN", "Material"])
    assert hashes.nunique() == 2


def test_append_no_duplicates(tmp_path: pytest.fixture) -> None:
    df = make_df()
    out = tmp_path / "test.parquet"
    append_deduplicated(df, out, ["VIN", "Material"], "test.xlsx")
    # second call with same data should insert 0 rows
    append_deduplicated(df, out, ["VIN", "Material"], "test.xlsx")
    result = pd.read_parquet(out)
    assert len(result) == len(df)


def test_append_new_rows(tmp_path: pytest.fixture) -> None:
    df1 = make_df(3)
    df2 = make_df(6)  # 3 new rows
    out = tmp_path / "test.parquet"
    append_deduplicated(df1, out, ["VIN", "Material"], "batch1.xlsx")
    append_deduplicated(df2, out, ["VIN", "Material"], "batch2.xlsx")
    result = pd.read_parquet(out)
    assert len(result) == 6
