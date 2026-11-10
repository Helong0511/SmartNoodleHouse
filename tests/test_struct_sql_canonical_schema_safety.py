"""The canonical schema is a declarative baseline, never a destructive reset script."""
from pathlib import Path
import re


def test_canonical_schema_contains_no_destructive_statements():
    repo_root = Path(__file__).resolve().parents[1]
    raw = (repo_root / "sql" / "struct.sql").read_text(encoding="utf-8")
    start_marker = "-- BEGIN CANONICAL SCHEMA"
    end_marker = "-- END CANONICAL SCHEMA"
    start = raw.find(start_marker)
    end = raw.find(end_marker)
    assert start >= 0 and end > start, "canonical schema markers must exist"

    schema = raw[start + len(start_marker):end]
    statements = []
    for line in schema.splitlines():
        if line.strip().startswith("--"):
            continue
        statements.extend(part.strip() for part in line.split(";") if part.strip())

    destructive = [
        statement for statement in statements
        if re.match(r"(?is)^(DROP|TRUNCATE|DELETE|INSERT|UPDATE|REPLACE)\b", statement)
    ]
    assert not destructive, f"destructive statements in canonical schema: {destructive}"

    # The baseline is intentionally declarative: only idempotent CREATE TABLE
    # statements are allowed. This also rejects ALTER/DROP INDEX/RENAME
    # operations that the destructive-keyword list above might otherwise miss.
    non_table_ddl = [
        statement for statement in statements
        if not re.match(r"(?is)^CREATE\\s+TABLE\\s+IF\\s+NOT\\s+EXISTS\\b", statement)
    ]
    assert not non_table_ddl, f"unexpected DDL in canonical schema: {non_table_ddl}"
