"""Static integrity checks for the canonical MySQL schema source."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STRUCT = ROOT / "sql" / "struct.sql"


def test_canonical_schema_has_unique_columns_and_resolvable_foreign_keys():
    raw = STRUCT.read_text(encoding="utf-8")
    start = raw.index("-- BEGIN CANONICAL SCHEMA")
    end = raw.index("-- END CANONICAL SCHEMA")
    body = raw[start:end]
    tables: dict[str, set[str]] = {}
    statements = [part.strip() for part in body.split(";") if part.strip()]

    for statement in statements:
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS \x60([^\x60]+)\x60\s*\(([\s\S]*)\)\s*ENGINE",
            statement,
            re.IGNORECASE,
        )
        if not match:
            continue
        table_name, definition = match.groups()
        columns = re.findall(r"^  \x60([^\x60]+)\x60\s+", definition, re.MULTILINE)
        duplicates = [name for name, count in Counter(columns).items() if count > 1]
        assert not duplicates, f"{table_name} defines duplicate columns: {duplicates}"
        assert re.search(r"^  PRIMARY KEY\s*\(", definition, re.MULTILINE), (
            f"{table_name} is missing a primary key definition"
        )
        tables[table_name] = set(columns)

    assert tables, "canonical schema contains no CREATE TABLE statements"

    unresolved = []
    for statement in statements:
        for parent_table, parent_column in re.findall(
            r"REFERENCES \x60([^\x60]+)\x60\s*\(\x60([^\x60]+)\x60\)", statement, re.IGNORECASE
        ):
            if parent_table not in tables:
                unresolved.append(f"missing parent table {parent_table}")
            elif parent_column not in tables[parent_table]:
                unresolved.append(f"missing parent column {parent_table}.{parent_column}")
    assert not unresolved, "canonical schema has unresolved foreign keys: " + "; ".join(unresolved)


def test_canonical_schema_foreign_key_names_are_unique():
    raw = STRUCT.read_text(encoding="utf-8")
    body = raw[raw.index("-- BEGIN CANONICAL SCHEMA") : raw.index("-- END CANONICAL SCHEMA")]
    names = re.findall(r"CONSTRAINT \x60([^\x60]+)\x60 FOREIGN KEY", body, re.IGNORECASE)
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    assert not duplicates, f"duplicate MySQL foreign-key constraint names: {duplicates}"
