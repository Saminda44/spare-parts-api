"""SQLAlchemy table definitions for the spare-parts database."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData(schema="interim")

catalog_parts = Table(
    "catalog_parts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("model", String(100)),
    Column("section", String(200)),
    Column("ref_no", String(50)),
    Column("part_no", String(100)),
    Column("description", Text),
    Column("qty", String(50)),
    Column("nine_digit_part_no", String(100)),
    Column("superseded_part_no", String(100)),
    Column("remarks", Text),
    Column("source_file", String(500)),
    Column("ocr_used", Boolean),
    # Metadata footer — CLAUDE.md §12
    Column("source_hash", String(64)),
    Column("code_commit_sha", String(40)),
    Column("generated_at", DateTime(timezone=True)),
    Column("model_version", String(50)),
)
