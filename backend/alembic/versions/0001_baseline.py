"""baseline: 从 sql/struct.sql 的 CANONICAL SCHEMA 段构建完整 schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-27
"""
from __future__ import annotations

import re
from pathlib import Path

from alembic import op
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

# revision identifiers
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# CANONICAL SCHEMA 使用 CREATE TABLE IF NOT EXISTS；仅保留表已存在的兼容兜底。
# 任何重复列/索引/外键或唯一键错误都必须失败，不能静默留下缺失约束的 schema。
_IGNORE_MYSQL_ERRNOS = {
    1050,  # table already exists
}


def _repo_root() -> Path:
    # .../backend/alembic/versions/本文件 → 仓库根
    return Path(__file__).resolve().parents[3]


def _schema_sql_path() -> Path:
    # 结构真源：sql/struct.sql（生产种子见 sql/data_sys.sql + sql/data_custom.sql）
    return _repo_root() / "sql" / "struct.sql"


def _extract_schema_section(raw: str) -> str:
    """只读取结构真源的 DDL，绝不执行删库、授权或生产种子段。"""
    start_marker = "-- BEGIN CANONICAL SCHEMA"
    end_marker = "-- END CANONICAL SCHEMA"
    start = raw.find(start_marker)
    end = raw.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise RuntimeError("sql/struct.sql 缺少 CANONICAL SCHEMA 分段标记")
    return raw[start + len(start_marker) : end]


def _split_sql_statements(sql: str) -> list[str]:
    """去掉整行注释后按分号切分（DDL 场景，不处理存储过程体）。"""
    lines: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [p.strip() for p in body.split(";") if p.strip()]


def _should_skip_statement(stmt: str) -> bool:
    # 防御性跳过：即使误入标记区也不执行删库/授权类语句
    return bool(
        re.match(
            r"(?is)^(DROP\b|TRUNCATE\b|DELETE\b|CREATE\s+DATABASE|USE\b|CREATE\s+USER|GRANT\b|REVOKE\b|FLUSH\s+PRIVILEGES)\b",
            stmt,
        )
    )


def _mysql_errno(exc: BaseException) -> int | None:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    args = getattr(orig, "args", None)
    if args and isinstance(args[0], int):
        return int(args[0])
    return None


def upgrade() -> None:
    path = _schema_sql_path()
    if not path.is_file():
        raise FileNotFoundError(f"未找到 schema 文件: {path}")

    raw = _extract_schema_section(path.read_text(encoding="utf-8"))
    conn = op.get_bind()
    is_mysql = conn.dialect.name == "mysql"
    if is_mysql:
        # struct.sql 按模块分组，部分子表在父表之前定义（如 ab_assignments → customers）。
        # MySQL 官方支持在 FOREIGN_KEY_CHECKS=0 时按任意顺序导入含外键的表；
        # 这里只在 schema DDL 阶段临时关闭，finally 必定恢复，不影响应用运行时约束。
        conn.exec_driver_sql("SET SESSION FOREIGN_KEY_CHECKS=0")
    try:
        for stmt in _split_sql_statements(raw):
            if _should_skip_statement(stmt):
                continue
            try:
                # 用驱动原生 SQL，避免 COMMENT/JSON 示例中的 ":50" 被 text() 当成绑定参数
                conn.exec_driver_sql(stmt)
            except (ProgrammingError, DBAPIError) as exc:
                errno = _mysql_errno(exc)
                if errno in _IGNORE_MYSQL_ERRNOS:
                    continue
                raise
    finally:
        if is_mysql:
            # 不允许把会话的外键校验关闭状态带入后续 Alembic revisions / 应用连接。
            conn.exec_driver_sql("SET SESSION FOREIGN_KEY_CHECKS=1")

    if is_mysql:
        # FOREIGN_KEY_CHECKS=1 不会回扫历史行，因此显式确认所有 FK 元数据
        # 都能解析到已创建的父表/父列，避免导入顺序错误留下悬空约束。
        missing_reference = conn.exec_driver_sql(
            """
            SELECT k.TABLE_NAME, k.CONSTRAINT_NAME,
                   k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE AS k
            LEFT JOIN information_schema.TABLES AS t
              ON t.TABLE_SCHEMA = k.TABLE_SCHEMA
             AND t.TABLE_NAME = k.REFERENCED_TABLE_NAME
            LEFT JOIN information_schema.COLUMNS AS c
              ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
             AND c.TABLE_NAME = k.REFERENCED_TABLE_NAME
             AND c.COLUMN_NAME = k.REFERENCED_COLUMN_NAME
            WHERE k.TABLE_SCHEMA = DATABASE()
              AND k.REFERENCED_TABLE_NAME IS NOT NULL
              AND (t.TABLE_NAME IS NULL OR c.COLUMN_NAME IS NULL)
            LIMIT 1
            """
        ).first()
        if missing_reference:
            raise RuntimeError(
                "Canonical schema contains an unresolved foreign-key reference: "
                f"{tuple(missing_reference)}"
            )


def downgrade() -> None:
    # 基线不回滚整库 DROP；需要重建时请空库后重新 upgrade。
    pass
