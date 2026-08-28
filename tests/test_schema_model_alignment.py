"""比对 sql/001_schema.sql 与 SQLAlchemy models 的表/列一致性。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_sql_schema(path: Path) -> dict[str, dict[str, str]]:
    sql = path.read_text(encoding="utf-8")
    tables: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\)\s*ENGINE", sql, re.S):
        name = m.group(1)
        body = m.group(2)
        cols: dict[str, str] = {}
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith(("PRIMARY", "UNIQUE", "INDEX", "KEY", "CONSTRAINT")):
                continue
            cm = re.match(r"`?(\w+)`?\s+(\w+(?:\([^)]+\))?)", line)
            if cm:
                cols[cm.group(1)] = cm.group(2).upper()
        tables[name] = cols
    return tables


def parse_models(path: Path) -> dict[str, dict[str, str]]:
    src = path.read_text(encoding="utf-8")
    tables: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r'class (\w+)\(.*?\):\s*(?:\n\s*"""[\s\S]*?""")?\s*\n\s*__tablename__ = "(\w+)"',
        src,
    ):
        tname = m.group(2)
        block = src[m.end() :]
        end = block.find("\nclass ")
        block = block[:end] if end != -1 else block
        cols: dict[str, str] = {}
        for cm in re.finditer(r"(\w+):\s*Mapped.*?=\s*mapped_column\(([^)]+)\)", block):
            col = cm.group(1)
            args = cm.group(2)
            type_m = re.search(
                r"(BigInteger|String\(\d+\)|Integer|Boolean|Text|JSON|Numeric\([^)]+\)|LargeBinary|DateTime)",
                args,
            )
            cols[col] = type_m.group(1) if type_m else "unknown"
        tables[tname] = cols
    return tables


def test_schema_model_table_alignment():
    sql_tables = parse_sql_schema(ROOT / "sql" / "001_schema.sql")
    model_tables = parse_models(ROOT / "backend" / "app" / "models" / "__init__.py")

    assert not (set(model_tables) - set(sql_tables)), (
        "models 有但 schema 无: " + str(sorted(set(model_tables) - set(sql_tables)))
    )
    assert not (set(sql_tables) - set(model_tables)), (
        "schema 有但 models 无: " + str(sorted(set(sql_tables) - set(model_tables)))
    )

    model_only_cols = []
    sql_only_cols = []
    timestamp_cols = frozenset({"created_at", "updated_at"})
    for tname in sorted(model_tables):
        mcols = set(model_tables[tname])
        scols = set(sql_tables[tname])
        for c in sorted(mcols - scols):
            model_only_cols.append((tname, c))
        for c in sorted(scols - mcols):
            if c in timestamp_cols:
                continue
            sql_only_cols.append((tname, c))

    assert not model_only_cols, f"model 独有列: {model_only_cols}"
    assert not sql_only_cols, f"schema 独有列: {sql_only_cols}"


if __name__ == "__main__":
    sql_tables = parse_sql_schema(ROOT / "sql" / "001_schema.sql")
    model_tables = parse_models(ROOT / "backend" / "app" / "models" / "__init__.py")
    print("tables model-only:", sorted(set(model_tables) - set(sql_tables)))
    print("tables sql-only:", sorted(set(sql_tables) - set(model_tables)))
    for tname in sorted(model_tables):
        mcols = set(model_tables[tname])
        scols = set(sql_tables.get(tname, {}))
        diff_m = sorted(mcols - scols)
        diff_s = sorted(scols - mcols)
        if diff_m or diff_s:
            print(f"{tname}: model+{diff_m} sql+{diff_s}")
