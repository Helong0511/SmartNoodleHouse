"""三方结构对齐审计：实际 MySQL 库 vs ORM 模型 vs SQL 真源文件。

覆盖维度：表/列/类型/可空/默认/索引/外键/唯一约束/字符集/引擎/表注释/列注释。
输出：逐项差异清单，根因分析与修复建议。

用法：
    cd e:\\SmartNoodleHouse\\backend
    python tests\\审计_三方结构对齐.py            # 默认 ORM=tests/../backend/app/models/__init__.py
    python tests\\审计_三方结构对齐.py --strict  # 有任何差异即返回 1
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "sql" / "struct.sql"
# 已知 ORM 通过 TimestampMixin 注入但 SQL 文件实际有 created_at/updated_at 的表
TIMESTAMP_COLS = frozenset({"created_at", "updated_at"})
# SQL 文件有但 ORM 不映射的 GENERATED/隐藏列
# 注意：option_groups.store_scope 已用 ORM Computed 映射，不在此列
SQL_ONLY_GENERATED = {
    ("cashier_shifts", "open_operator_key"),
    ("point_ledger", "earn_order_id"),
}
# ORM 有但 SQL 文件未必显式列出（如 TimestampMixin 已注入）
# 上面的 TIMESTAMP_COLS 处理

# 类型规范化映射：SQL/ORM 同义但写法不同 → 归一
TYPE_ALIASES = {
    "TINYINT": "INT",
    "SMALLINT": "INT",
    "MEDIUMINT": "INT",
    "BIGINT": "BIGINT",
    "INT": "INT",
    "INTEGER": "INT",
    "BIGINT UNSIGNED": "BIGINT",
    "INT UNSIGNED": "INT",
    "TINYINT UNSIGNED": "INT",
    "VARCHAR": "VARCHAR",
    "CHAR": "CHAR",
    "TEXT": "TEXT",
    "MEDIUMTEXT": "TEXT",
    "LONGTEXT": "TEXT",
    "TINYTEXT": "TEXT",
    "JSON": "JSON",
    "DECIMAL": "DECIMAL",
    "NUMERIC": "DECIMAL",
    "DATETIME": "DATETIME",
    "TIMESTAMP": "DATETIME",
    "DATE": "DATE",
    "TIME": "TIME",
    "BLOB": "BLOB",
    "LONGBLOB": "BLOB",
    "MEDIUMBLOB": "BLOB",
    "TINYBLOB": "BLOB",
    "BINARY": "BINARY",
    "VARBINARY": "BLOB",
    "BOOLEAN": "BOOL",
    "BOOL": "BOOL",
}


def norm_sql_type(s: str) -> str:
    """SQL 类型字符串归一化：去掉 UNSIGNED、长度、括号内精度。"""
    if not s:
        return ""
    s = s.strip().upper()
    # TINYINT(1) → BOOL；其它 TINYINT → INT
    if re.match(r"^TINYINT\(1\)", s):
        return "BOOL"
    # 提取主类型
    m = re.match(r"^([A-Z]+)\s*(?:UNSIGNED)?", s)
    if not m:
        return s
    base = m.group(1)
    return TYPE_ALIASES.get(base, base)


def norm_orm_type(t: str) -> str:
    """ORM 类型映射到归一类型（SQL 等价）。"""
    t = t.strip()
    if not t:
        return ""
    m = re.match(r"^(\w+)(?:\((\d+)(?:,\s*(\d+))?\))?", t)
    if not m:
        return t.upper()
    base = m.group(1).upper()
    if base == "STRING":
        return "VARCHAR"
    if base == "INTEGER":
        return "INT"
    if base == "NUMERIC":
        return "DECIMAL"
    if base == "DATETIME":
        return "DATETIME"
    if base == "BOOLEAN":
        return "BOOL"
    if base == "TEXT":
        return "TEXT"
    if base == "JSON":
        return "JSON"
    if base == "BIGINTEGER":
        return "BIGINT"
    if base == "TIME":
        return "TIME"
    if base == "DATE":
        return "DATE"
    if base == "LARGEBINARY":
        return "BLOB"
    return TYPE_ALIASES.get(base, base)


def types_compatible(a: str, b: str) -> bool:
    """判断两个归一类型是否兼容（允许 SQLAlchemy 默认映射差异）。"""
    if a == b:
        return True
    # MySQL TINYINT(1) → BOOL；ORM Boolean 在 MySQL 是 TINYINT(1)；与 INT 兼容
    int_set = {"INT", "BOOL", "BIGINT"}
    if a in int_set and b in int_set:
        return True
    return False


# ---------------------------------------------------------------------------
# SQL 文件解析（正则）
# ---------------------------------------------------------------------------

@dataclass
class SqlColumn:
    name: str
    raw_type: str
    norm_type: str
    nullable: bool
    default: str | None
    comment: str | None


@dataclass
class SqlIndex:
    name: str
    columns: tuple[str, ...]
    is_unique: bool
    kind: str  # INDEX/UNIQUE/PRIMARY/KEY


@dataclass
class SqlForeignKey:
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]


@dataclass
class SqlTable:
    name: str
    columns: list[SqlColumn]
    indexes: list[SqlIndex] = field(default_factory=list)
    foreign_keys: list[SqlForeignKey] = field(default_factory=list)
    charset: str | None = None
    collation: str | None = None
    engine: str | None = None
    comment: str | None = None


def parse_sql_file(path: Path) -> dict[str, SqlTable]:
    if not path.exists():
        return {}
    sql = path.read_text(encoding="utf-8")
    tables: dict[str, SqlTable] = {}
    # CREATE TABLE IF NOT EXISTS `name` ( ... ) ENGINE = ... COMMENT = ...;
    # 用递归平衡括号扫描，避免正则贪婪
    for m in re.finditer(
        r"CREATE TABLE IF NOT EXISTS `?(\w+)`?\s*\(", sql
    ):
        name = m.group(1)
        # 跳过 alembic_version（非业务表）
        if name == "alembic_version":
            continue
        start = m.end()
        # 找匹配闭括号
        depth = 1
        i = start
        n = len(sql)
        while i < n and depth > 0:
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        body = sql[start : i - 1]
        tail = sql[i : i + 800]
        # 解析表属性
        engine_m = re.search(r"ENGINE\s*=\s*(\w+)", tail)
        charset_m = re.search(r"CHARSET\s*=\s*(\w+)", tail)
        coll_m = re.search(r"COLLATE\s*=\s*(\w+)", tail)
        cmt_m = re.search(r"COMMENT\s*=\s*'((?:[^'\\]|\\.)*)'", tail)
        tbl = SqlTable(
            name=name,
            columns=[],
            charset=charset_m.group(1) if charset_m else None,
            collation=coll_m.group(1) if coll_m else None,
            engine=engine_m.group(1) if engine_m else None,
            comment=cmt_m.group(1) if cmt_m else None,
        )
        # 逐行解析
        # 但 CHECK 可能跨行 OR/AND，需要合并
        lines: list[str] = []
        cur: list[str] = []
        for raw_line in body.split("\n"):
            stripped = raw_line.strip().rstrip(",")
            if not stripped:
                continue
            # 检测续行：上一行以 OR/AND 开头结尾、或本行以 OR/AND 续行
            if cur and re.match(r"^(OR|AND)\b", stripped, re.I):
                cur.append(stripped)
                continue
            if cur:
                lines.append("\n".join(cur))
                cur = []
            cur = [stripped]
        if cur:
            lines.append("\n".join(cur))

        for line in lines:
            upper = line.upper()
            if "GENERATED ALWAYS" in upper or re.match(r"^\s*`?\w+`?\s+.*\bAS\s*\(", line, re.I):
                # GENERATED 列定义（store_scope BIGINT UNSIGNED AS (IFNULL(store_id, 0)) STORED）
                # 不算列差异（ORM 用 Computed 映射），但仍解析记录列名以备索引比对
                cm = re.match(r"`?(\w+)`?\s+([A-Z]+(?:\([^)]*\))?(?:\s+UNSIGNED)?)\s+AS\s*\(", line, re.I)
                if cm:
                    col_name = cm.group(1)
                    raw_type = cm.group(2).upper()
                    tbl.columns.append(
                        SqlColumn(
                            name=col_name,
                            raw_type=raw_type,
                            norm_type=norm_sql_type(raw_type),
                            nullable=False,
                            default=None,
                            comment=None,
                        )
                    )
                continue
            if line.startswith(("PRIMARY", "UNIQUE", "INDEX", "KEY ", "KEY(", "CONSTRAINT", "CHECK", "FULLTEXT")):
                # 索引/约束
                if line.startswith("PRIMARY"):
                    cm = re.match(r"PRIMARY\s+KEY\s*\(([^)]*)\)", line, re.I)
                    if cm:
                        cols = _parse_cols(cm.group(1))
                        tbl.indexes.append(SqlIndex("PRIMARY", cols, True, "PRIMARY"))
                elif line.startswith("UNIQUE"):
                    cm = re.match(r"UNIQUE\s+(?:INDEX|KEY)\s+`?(\w+)`?\s*\(([^)]*)\)", line, re.I)
                    if cm:
                        cols = _parse_cols(cm.group(2))
                        tbl.indexes.append(SqlIndex(cm.group(1), cols, True, "UNIQUE"))
                elif line.startswith("INDEX") or line.startswith("KEY"):
                    cm = re.match(r"(?:INDEX|KEY)\s+`?(\w+)`?\s*\(([^)]*)\)", line, re.I)
                    if cm:
                        cols = _parse_cols(cm.group(2))
                        tbl.indexes.append(SqlIndex(cm.group(1), cols, False, "INDEX"))
                elif line.startswith("CONSTRAINT"):
                    # 外键
                    fk_m = re.match(
                        r"CONSTRAINT\s+`?(\w+)`?\s+FOREIGN\s+KEY\s*\(([^)]*)\)\s+REFERENCES\s+`?(\w+)`?\s*\(([^)]*)\)",
                        line,
                        re.I,
                    )
                    if fk_m:
                        tbl.foreign_keys.append(
                            SqlForeignKey(
                                fk_m.group(1),
                                _parse_cols(fk_m.group(2)),
                                fk_m.group(3),
                                _parse_cols(fk_m.group(4)),
                            )
                        )
                continue
            # 列定义：`col` TYPE(...) [NOT NULL | NULL] [DEFAULT ...] [AUTO_INCREMENT] [PRIMARY KEY] [UNIQUE] [COMMENT '...'] [ON UPDATE ...]
            cm = re.match(
                r"`?(\w+)`?\s+([A-Z]+(?:\([^)]*\))?(?:\s+UNSIGNED)?)(.*?)$",
                line,
                re.I | re.S,
            )
            if not cm:
                continue
            col_name = cm.group(1)
            raw_type = cm.group(2).upper()
            rest = cm.group(3) or ""
            nullable = True
            if re.search(r"\bNOT\s+NULL\b", rest, re.I):
                nullable = False
            if re.search(r"\bNULL\b", rest, re.I) and not re.search(r"\bNOT\s+NULL\b", rest, re.I):
                nullable = True
            default_m = re.search(r"DEFAULT\s+('(?:[^'\\]|\\.)*'|\S+)", rest, re.I)
            default = default_m.group(1).strip() if default_m else None
            comment_m = re.search(r"COMMENT\s+'((?:[^'\\]|\\.)*)'", rest, re.I)
            comment = comment_m.group(1) if comment_m else None
            tbl.columns.append(
                SqlColumn(
                    name=col_name,
                    raw_type=raw_type,
                    norm_type=norm_sql_type(raw_type),
                    nullable=nullable,
                    default=default,
                    comment=comment,
                )
            )
            # 列后缀索引：PRIMARY KEY / UNIQUE
            if re.search(r"\bPRIMARY\s+KEY\b", rest, re.I):
                tbl.indexes.append(SqlIndex("PRIMARY", (col_name,), True, "PRIMARY"))
            if re.search(r"(?<!NOT\s)\bUNIQUE\b", rest, re.I):
                # 单列 UNIQUE 后缀（非 NOT NULL UNIQUE 也算）
                idx_name = f"uq_{name}_{col_name}"
                tbl.indexes.append(SqlIndex(idx_name, (col_name,), True, "UNIQUE"))
        tables[name] = tbl
    return tables


def _parse_cols(s: str) -> tuple[str, ...]:
    return tuple(
        re.sub(r"^`|`$", "", c.strip())
        for c in s.split(",")
        if c.strip()
    )


# ---------------------------------------------------------------------------
# ORM 模型解析（基于 SQLAlchemy Base.metadata）
# ---------------------------------------------------------------------------

@dataclass
class OrmColumn:
    name: str
    norm_type: str
    raw_type: str
    nullable: bool
    default: str | None
    python_type: str


@dataclass
class OrmTable:
    name: str
    columns: dict[str, OrmColumn]
    indexes: dict[str, tuple[tuple[str, ...], bool]] = field(default_factory=dict)
    foreign_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    unique_constraints: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


def parse_orm_metadata() -> dict[str, OrmTable]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.database import Base  # noqa: E402
    import app.models  # noqa: F401, E402  触发注册

    tables: dict[str, OrmTable] = {}
    for tname, table in Base.metadata.tables.items():
        cols: dict[str, OrmColumn] = {}
        for col in table.columns:
            raw_type = str(col.type) if col.type is not None else ""
            # 处理 variant
            try:
                compiled = col.type.compile(dialect=None) if hasattr(col.type, "compile") else raw_type
            except Exception:
                compiled = raw_type
            orm_col = OrmColumn(
                name=col.name,
                norm_type=norm_orm_type(compiled),
                raw_type=raw_type,
                nullable=bool(col.nullable) or col.nullable is None,
                default=str(col.default.arg) if col.default and hasattr(col.default, "arg") and not callable(col.default.arg) else None,
                python_type=raw_type,
            )
            cols[col.name] = orm_col
        # 索引：主键/唯一/普通
        idx: dict[str, tuple[tuple[str, ...], bool]] = {}
        unique_constraints: list[tuple[str, tuple[str, ...]]] = []
        for ix in table.indexes:
            idx[ix.name or f"ix_{tname}_{'_'.join(c.name for c in ix.columns)}"] = (
                tuple(c.name for c in ix.columns),
                bool(ix.unique),
            )
        # 主键
        pk_cols = tuple(c.name for c in table.primary_key.columns)
        if pk_cols:
            idx["PRIMARY"] = (pk_cols, True)
        # 显式 UniqueConstraint
        for c in table.constraints:
            from sqlalchemy import UniqueConstraint as _UC
            if isinstance(c, _UC):
                # 没有名字会用自动名；这里记录
                cname = c.name or f"uq_{tname}_{'_'.join(col.name for col in c.columns)}"
                unique_constraints.append((cname, tuple(col.name for col in c.columns)))
                idx[cname] = (tuple(col.name for col in c.columns), True)
        # 外键
        fks: dict[str, tuple[str, str]] = {}
        for col in table.columns:
            for fk in col.foreign_keys:
                ref = fk.target_fullname.split(".")  # 'table.col'
                fk_key = f"fk_{tname}_{col.name}_{ref[0]}"
                fks[fk_key] = (col.name, ref[0])
        tables[tname] = OrmTable(
            name=tname,
            columns=cols,
            indexes=idx,
            foreign_keys=fks,
            unique_constraints=unique_constraints,
        )
    return tables


# ---------------------------------------------------------------------------
# 实际 MySQL 库 introspection
# ---------------------------------------------------------------------------

@dataclass
class DbColumn:
    name: str
    norm_type: str
    raw_type: str
    nullable: bool
    default: str | None
    comment: str | None


@dataclass
class DbIndex:
    name: str
    columns: tuple[str, ...]
    is_unique: bool


@dataclass
class DbTable:
    name: str
    columns: dict[str, DbColumn] = field(default_factory=dict)
    indexes: dict[str, DbIndex] = field(default_factory=dict)
    foreign_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    charset: str | None = None
    collation: str | None = None
    engine: str | None = None
    comment: str | None = None


async def inspect_mysql(db_url: str, schema: str = "smart_noodle_house") -> dict[str, DbTable]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(db_url)
    tables: dict[str, DbTable] = {}
    async with engine.connect() as conn:
        # 列出所有表
        r = await conn.execute(
            text(
                "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_COMMENT "
                "FROM information_schema.tables WHERE table_schema=:s"
            ),
            {"s": schema},
        )
        for row in r.fetchall():
            tname, engine_v, collation_v, comment_v = row
            # 跳过 alembic_version（非业务表，由 alembic 自管理）
            if tname == "alembic_version":
                continue
            tables[tname] = DbTable(
                name=tname,
                engine=engine_v,
                collation=collation_v,
                comment=comment_v,
            )
        # 列信息
        r = await conn.execute(
            text(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_COMMENT, DATA_TYPE "
                "FROM information_schema.columns WHERE table_schema=:s ORDER BY TABLE_NAME, ORDINAL_POSITION"
            ),
            {"s": schema},
        )
        for row in r.fetchall():
            tname, col_name, col_type, is_nullable, col_default, col_comment, data_type = row
            nullable = (is_nullable or "").upper() == "YES"
            tables.setdefault(tname, DbTable(name=tname)).columns[col_name] = DbColumn(
                name=col_name,
                raw_type=col_type.upper() if col_type else "",
                norm_type=norm_sql_type(col_type or data_type or ""),
                nullable=nullable,
                default=str(col_default) if col_default is not None else None,
                comment=col_comment,
            )
        # 索引信息
        r = await conn.execute(
            text(
                "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE "
                "FROM information_schema.statistics WHERE table_schema=:s "
                "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
            ),
            {"s": schema},
        )
        per_idx: dict[tuple[str, str], list[str]] = defaultdict(list)
        per_uniq: dict[tuple[str, str], bool] = {}
        for row in r.fetchall():
            tname, idx_name, col_name, non_unique = row
            per_idx[(tname, idx_name)].append(col_name)
            per_uniq[(tname, idx_name)] = (non_unique == 0)
        for (tname, idx_name), cols in per_idx.items():
            tables[tname].indexes[idx_name] = DbIndex(
                name=idx_name,
                columns=tuple(cols),
                is_unique=per_uniq[(tname, idx_name)],
            )
        # 外键
        r = await conn.execute(
            text(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME "
                "FROM information_schema.key_column_usage "
                "WHERE table_schema=:s AND REFERENCED_TABLE_NAME IS NOT NULL"
            ),
            {"s": schema},
        )
        for row in r.fetchall():
            tname, cname, col_name, ref_table = row
            tables[tname].foreign_keys[cname] = (col_name, ref_table)
        # 表 charset（通过数据库默认）
        r = await conn.execute(
            text(
                "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                "FROM information_schema.schemata WHERE SCHEMA_NAME=:s"
            ),
            {"s": schema},
        )
        for row in r.fetchall():
            schema_charset, schema_collation = row
            for t in tables.values():
                if t.charset is None:
                    t.charset = schema_charset
                if t.collation is None:
                    t.collation = schema_collation
    await engine.dispose()
    # 再次清理：列/索引/外键查询的 setdefault 可能把 alembic_version 加回来
    tables.pop("alembic_version", None)
    return tables


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------

@dataclass
class Diff:
    table: str
    kind: str  # TABLE_ONLY_DB / TABLE_ONLY_ORM / TABLE_ONLY_SQL / COLUMN_ONLY_DB / ...
    col: str | None = None
    detail: str = ""
    root_cause: str = ""
    fix: str = ""


def compare_three(
    db_tables: dict[str, DbTable],
    orm_tables: dict[str, OrmTable],
    sql_tables: dict[str, SqlTable],
) -> list[Diff]:
    diffs: list[Diff] = []
    db_names = set(db_tables)
    orm_names = set(orm_tables)
    sql_names = set(sql_tables)

    for n in sorted(db_names - orm_names):
        diffs.append(Diff(n, "TABLE_ONLY_DB", root_cause="实际库有但 ORM 未建模", fix="补充 ORM 模型或确认是否遗留"))
    for n in sorted(orm_names - db_names):
        diffs.append(Diff(n, "TABLE_ONLY_ORM", root_cause="ORM 有但实际库未建", fix="执行缺失的 alembic 迁移"))
    for n in sorted(db_names - sql_names):
        diffs.append(Diff(n, "TABLE_ONLY_DB_NOT_IN_SQL", root_cause="实际库有但 SQL 文件缺表", fix="补到 SQL 文件"))
    for n in sorted(sql_names - db_names):
        diffs.append(Diff(n, "TABLE_ONLY_SQL_NOT_IN_DB", root_cause="SQL 文件有但实际库未建", fix="执行 SQL 或确认 alembic"))
    for n in sorted(orm_names - sql_names):
        diffs.append(Diff(n, "TABLE_ONLY_ORM_NOT_IN_SQL", root_cause="ORM 有但 SQL 文件缺表", fix="补到 SQL 文件"))
    for n in sorted(sql_names - orm_names):
        diffs.append(Diff(n, "TABLE_ONLY_SQL_NOT_IN_ORM", root_cause="SQL 文件有但 ORM 未建模", fix="补充 ORM 模型"))

    common = db_names & orm_names & sql_names
    for tname in sorted(common):
        db_t = db_tables[tname]
        orm_t = orm_tables[tname]
        sql_t = sql_tables[tname]
        db_cols = {c for c in db_t.columns if (tname, c) not in SQL_ONLY_GENERATED}
        orm_cols = set(orm_t.columns)
        sql_cols = {c.name for c in sql_t.columns if (tname, c.name) not in SQL_ONLY_GENERATED}
        # 列存在性
        for c in sorted(db_cols - orm_cols - sql_cols):
            diffs.append(Diff(tname, "COLUMN_ONLY_DB", c, root_cause="仅实际库有", fix="确认是否应进 ORM 与 SQL"))
        for c in sorted(orm_cols - db_cols - sql_cols):
            diffs.append(Diff(tname, "COLUMN_ONLY_ORM", c, root_cause="仅 ORM 有", fix="补 alembic 迁移 + SQL 文件"))
        for c in sorted(sql_cols - db_cols - orm_cols):
            diffs.append(Diff(tname, "COLUMN_ONLY_SQL", c, root_cause="仅 SQL 文件有", fix="执行 SQL 或删该列"))
        # ORM 漏 / DB 漏 / SQL 漏
        for c in sorted(orm_cols - db_cols):
            if c in sql_cols:
                diffs.append(Diff(tname, "COLUMN_ORM_SQL_BUT_NOT_DB", c, root_cause="ORM 和 SQL 有但实际库未建", fix="执行 alembic upgrade"))
            else:
                diffs.append(Diff(tname, "COLUMN_ORM_ONLY_DB_MISSING", c, root_cause="ORM 有但实际库和 SQL 文件都没有", fix="补 SQL 文件 + alembic"))
        for c in sorted(db_cols - orm_cols):
            if c not in sql_cols:
                if c in TIMESTAMP_COLS:
                    continue
                diffs.append(Diff(tname, "COLUMN_DB_ONLY_NO_ORM_NO_SQL", c, root_cause="仅实际库有", fix="补 ORM 或确认是否遗留"))
        for c in sorted(sql_cols - orm_cols):
            if c in TIMESTAMP_COLS or (tname, c) in SQL_ONLY_GENERATED:
                continue
            if c in db_cols:
                diffs.append(Diff(tname, "COLUMN_SQL_DB_BUT_NOT_ORM", c, root_cause="SQL 和 DB 有但 ORM 未建", fix="补 ORM 模型"))
            else:
                diffs.append(Diff(tname, "COLUMN_SQL_ONLY", c, root_cause="仅 SQL 文件有", fix="执行 SQL 或删该列"))

        # 类型差异（仅当 DB & ORM & SQL 三方都有该列时）
        for c in sorted(db_cols & orm_cols & sql_cols):
            db_c = db_t.columns[c]
            orm_c = orm_t.columns[c]
            sql_c = next(x for x in sql_t.columns if x.name == c)
            if not types_compatible(db_c.norm_type, orm_c.norm_type):
                diffs.append(
                    Diff(
                        tname,
                        "TYPE_DB_ORM",
                        c,
                        detail=f"db={db_c.norm_type} vs orm={orm_c.norm_type}",
                        root_cause="类型不一致",
                        fix="对齐 ORM 或迁移实际库",
                    )
                )
            if not types_compatible(db_c.norm_type, sql_c.norm_type):
                diffs.append(
                    Diff(
                        tname,
                        "TYPE_DB_SQL",
                        c,
                        detail=f"db={db_c.norm_type} vs sql={sql_c.norm_type}",
                        root_cause="SQL 文件类型不匹配实际库",
                        fix="对齐 SQL 文件",
                    )
                )
            if not types_compatible(orm_c.norm_type, sql_c.norm_type):
                diffs.append(
                    Diff(
                        tname,
                        "TYPE_ORM_SQL",
                        c,
                        detail=f"orm={orm_c.norm_type} vs sql={sql_c.norm_type}",
                        root_cause="ORM 与 SQL 文件类型不一致",
                        fix="对齐",
                    )
                )
            # 可空性
            if db_c.nullable != orm_c.nullable:
                diffs.append(
                    Diff(
                        tname,
                        "NULLABLE_DB_ORM",
                        c,
                        detail=f"db_nullable={db_c.nullable} vs orm_nullable={orm_c.nullable}",
                        root_cause="可空性不一致",
                        fix="对齐 nullable",
                    )
                )

        # 索引差异（按列集合+唯一性比对，名称允许不同）
        def key_of(idx_cols: tuple[str, ...], uniq: bool) -> tuple[tuple[str, ...], bool]:
            return (tuple(sorted(idx_cols)), uniq)

        # 跳过包含 ORM 不映射的 GENERATED 列的索引（ORM 不可能有这些索引）
        def has_generated_only(cols: tuple[str, ...]) -> bool:
            return any((tname, c) in SQL_ONLY_GENERATED for c in cols)

        db_idx_set: dict[tuple, list[str]] = defaultdict(list)
        orm_idx_set: dict[tuple, list[str]] = defaultdict(list)
        sql_idx_set: dict[tuple, list[str]] = defaultdict(list)
        for nm, ix in db_t.indexes.items():
            if has_generated_only(ix.columns):
                continue
            db_idx_set[key_of(ix.columns, ix.is_unique)].append(nm)
        for nm, (cols, uniq) in orm_t.indexes.items():
            if has_generated_only(cols):
                continue
            orm_idx_set[key_of(cols, uniq)].append(nm)
        for ix in sql_t.indexes:
            if has_generated_only(ix.columns):
                continue
            sql_idx_set[key_of(ix.columns, ix.is_unique)].append(ix.name)
        for k in set(db_idx_set) | set(orm_idx_set) | set(sql_idx_set):
            in_db = k in db_idx_set
            in_orm = k in orm_idx_set
            in_sql = k in sql_idx_set
            if not (in_db and in_orm and in_sql):
                diffs.append(
                    Diff(
                        tname,
                        "INDEX_MISSING",
                        detail=f"cols={k[0]} unique={k[1]} db={in_db} orm={in_orm} sql={in_sql}",
                        root_cause="索引三方未对齐",
                        fix="补齐缺失方的索引",
                    )
                )

    return diffs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="有差异即退出码 1")
    parser.add_argument("--sql", type=Path, default=SQL_FILE, help="SQL 文件路径")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import get_settings

    settings = get_settings()
    print(f"DATABASE_URL={settings.database_url}")
    print(f"SQL_FILE={args.sql}")
    print()

    orm_tables = parse_orm_metadata()
    print(f"ORM 表数：{len(orm_tables)}")
    sql_tables = parse_sql_file(args.sql)
    print(f"SQL 文件表数：{len(sql_tables)}")
    db_tables = asyncio.run(inspect_mysql(settings.database_url, schema="smart_noodle_house"))
    print(f"实际库表数：{len(db_tables)}")
    print()

    diffs = compare_three(db_tables, orm_tables, sql_tables)
    # 分类汇总
    by_kind: dict[str, list[Diff]] = defaultdict(list)
    for d in diffs:
        by_kind[d.kind].append(d)
    print(f"差异总数：{len(diffs)}")
    if not diffs:
        print("三方完全对齐。")
        return 0
    print()
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f"=== {kind}（{len(items)} 条）===")
        for d in items[:200]:
            line = f"  - {d.table}"
            if d.col:
                line += f".{d.col}"
            if d.detail:
                line += f"  {d.detail}"
            if d.root_cause:
                line += f"  根因:{d.root_cause}"
            if d.fix:
                line += f"  修复:{d.fix}"
            print(line)
        if len(items) > 200:
            print(f"  ... 共 {len(items)} 条，仅显示前 200")
        print()
    return 1 if args.strict and diffs else 0


if __name__ == "__main__":
    sys.exit(main())
