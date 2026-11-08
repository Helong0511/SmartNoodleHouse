"""Static integrity checks for the canonical MySQL schema source."""
from __future__ import annotations

import ast
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


def test_database_and_every_schema_table_use_utf8mb4_unicode_ci():
    raw = STRUCT.read_text(encoding="utf-8")
    assert re.search(
        r"CREATE DATABASE IF NOT EXISTS .*DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        raw,
        re.IGNORECASE,
    )
    assert re.search(
        r"SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
        raw,
        re.IGNORECASE,
    )

    statements = [part.strip() for part in raw.split(";") if part.strip()]
    table_statements = [
        statement
        for statement in statements
        if re.search(r"CREATE TABLE IF NOT EXISTS", statement, re.IGNORECASE)
    ]
    assert table_statements, "canonical schema contains no CREATE TABLE statements"

    for statement in table_statements:
        table = re.search(r"CREATE TABLE IF NOT EXISTS \x60([^\x60]+)\x60", statement)
        table_name = table.group(1) if table else "<unknown>"
        assert re.search(
            r"(?:CHARACTER SET\s*=\s*utf8mb4|DEFAULT CHARSET\s*=\s*utf8mb4)",
            statement,
            re.IGNORECASE,
        ), f"{table_name} must explicitly use utf8mb4"
        assert re.search(
            r"COLLATE\s*=\s*utf8mb4_unicode_ci",
            statement,
            re.IGNORECASE,
        ), f"{table_name} must explicitly use utf8mb4_unicode_ci"


def test_alembic_created_tables_pin_mysql_utf8mb4_options():
    expected = {
        "backend/alembic/versions/0015_delivery_menu_ops.py": {
            "store_business_hours",
            "platform_category_maps",
            "delivery_sync_alerts",
        },
        "backend/alembic/versions/0045_leader_fencing_epoch.py": {
            "leader_fencing_state",
        },
    }
    for relative_path, expected_tables in expected.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        module = ast.parse(source, filename=relative_path)
        found = set()
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            table_name = node.args[0].value
            if table_name not in expected_tables:
                continue
            options = {
                keyword.arg: ast.literal_eval(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            }
            assert options.get("mysql_engine") == "InnoDB", table_name
            assert options.get("mysql_charset") == "utf8mb4", table_name
            assert options.get("mysql_collate") == "utf8mb4_unicode_ci", table_name
            found.add(table_name)
        assert found == expected_tables, (
            f"{relative_path}: missing explicit charset options for "
            f"{sorted(expected_tables - found)}"
        )


def test_sqlalchemy_mysql_table_options_compile_utf8mb4_collation():
    from sqlalchemy import Column, Integer, MetaData, Table
    from sqlalchemy.dialects.mysql import dialect
    from sqlalchemy.schema import CreateTable

    table = Table(
        "charset_compile_probe",
        MetaData(),
        Column("id", Integer, primary_key=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    ddl = str(CreateTable(table).compile(dialect=dialect()))

    assert "ENGINE=InnoDB" in ddl
    assert "CHARSET=utf8mb4" in ddl
    assert "COLLATE=utf8mb4_unicode_ci" in ddl



def test_converged_baseline_migrations_never_drop_preexisting_objects_on_downgrade():
    """A baseline-created table must survive downgrade of an idempotent migration."""
    migration_paths = [
        ROOT / "backend" / "alembic" / "versions" / "0015_delivery_menu_ops.py",
        ROOT / "backend" / "alembic" / "versions" / "0045_leader_fencing_epoch.py",
    ]
    # These objects are already present in the canonical bootstrap schema.
    canonical = STRUCT.read_text(encoding="utf-8")
    for table in (
        "store_business_hours",
        "platform_category_maps",
        "delivery_sync_alerts",
        "leader_fencing_state",
    ):
        assert f"CREATE TABLE IF NOT EXISTS `{table}`" in canonical
    binding_start = canonical.index(
        "CREATE TABLE IF NOT EXISTS `platform_store_bindings`"
    )
    binding_end = canonical.find(";", binding_start)
    binding_ddl = canonical[binding_start:binding_end]
    for column in ("auto_sync_open", "platform_open_status", "last_sync_at"):
        assert f"`{column}`" in binding_ddl

    for path in migration_paths:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        downgrade = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        destructive_calls = [
            node for node in ast.walk(downgrade)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr in {"drop_table", "drop_column"}
        ]
        assert not destructive_calls, (
            f"{path.name} downgrade must not drop objects whose ownership "
            "was not recorded during upgrade"
        )
