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
    # Join the complete SQL body before splitting: CREATE TABLE statements
    # span many lines, so per-line splitting mistakes column definitions for SQL.
    body = "\n".join(
        line for line in schema.splitlines() if not line.strip().startswith("--")
    )
    statements = [part.strip() for part in body.split(";") if part.strip()]

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
        if not re.match(r"(?is)^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\b", statement)
    ]
    assert not non_table_ddl, f"unexpected DDL in canonical schema: {non_table_ddl}"


def test_deployment_guide_never_recommends_destructive_schema_reset():
    repo_root = Path(__file__).resolve().parents[1]
    guide = (repo_root / "docs" / "部署说明.md").read_text(encoding="utf-8")

    assert "已有生产库" in guide or "已有库" in guide
    assert "生产升级只允许备份后执行 Alembic 迁移" in guide
    assert "不要直接执行 `mysql < sql/struct.sql`" in guide
    assert "会删除并重建同名库" not in guide

    # data_sys.sql is destructive and may only appear behind the explicit
    # fresh-database confirmation guard documented in the same code block.
    lines = guide.splitlines()
    imports = [i for i, line in enumerate(lines) if "sql/data_sys.sql" in line]
    assert imports, "fresh-install seed command must be documented"
    for index in imports:
        command = lines[index]
        assert "--init-command=" in command
        assert "SMART_NOODLE_FRESH_INSTALL_CONFIRM='YES'" in command
