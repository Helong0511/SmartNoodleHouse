"""Preserve original customer identity on point and wallet ledger rows.

Revision ID: 0054_customer_ledger_origin
Revises: 0053_pos_cash_refund_shift
"""
from __future__ import annotations

import copy

import sqlalchemy as sa
from alembic import op

revision = "0054_customer_ledger_origin"
down_revision = "0053_pos_cash_refund_shift"
branch_labels = None
depends_on = None


def _origin_column(table: str, customer_id_type: sa.types.TypeEngine) -> sa.Column:
    # Match the referenced PK exactly: installations may use signed BIGINT
    # from ORM metadata or BIGINT UNSIGNED from the canonical SQL schema.
    return sa.Column(
        "origin_customer_id",
        copy.copy(customer_id_type),
        sa.ForeignKey(
            "customers.id",
            name=f"fk_{table}_origin_customer",
            ondelete="SET NULL",
        ),
        nullable=True,
        comment="Original customer account that generated the ledger snapshot",
    )


def _add_origin_column(table: str) -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table(table):
        return
    columns = {item["name"] for item in inspector.get_columns(table)}
    customer_columns = {
        item["name"]: item for item in inspector.get_columns("customers")
    } if inspector.has_table("customers") else {}
    if "id" not in customer_columns:
        raise RuntimeError("customers.id is required for ledger origin foreign keys")
    customer_id_type = customer_columns["id"]["type"]
    if "origin_customer_id" not in columns:
        # SQLite cannot ALTER TABLE ADD COLUMN with a REFERENCES clause when
        # foreign_keys are enabled. Batch mode rebuilds the table and keeps
        # the new FK; MySQL uses the native ALTER path.
        if conn.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.add_column(_origin_column(table, customer_id_type))
        else:
            op.add_column(table, _origin_column(table))

    indexes = {item["name"] for item in sa.inspect(conn).get_indexes(table)}
    index_name = f"ix_{table}_origin_customer_id"
    if index_name not in indexes:
        op.create_index(index_name, table, ["origin_customer_id"])
    conn.execute(
        sa.text(
            f"UPDATE {table} SET origin_customer_id = customer_id "
            "WHERE origin_customer_id IS NULL"
        )
    )


def upgrade() -> None:
    _add_origin_column("point_ledger")
    _add_origin_column("balance_ledger")


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("balance_ledger", "point_ledger"):
        inspector = sa.inspect(conn)
        if not inspector.has_table(table):
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        index_name = f"ix_{table}_origin_customer_id"
        indexes = {item["name"] for item in inspector.get_indexes(table)}
        # Do not rely solely on a generated constraint name: MySQL may report
        # a dialect-normalized name. Identify the FK by its local column.
        origin_fks = [
            fk for fk in inspector.get_foreign_keys(table)
            if "origin_customer_id" in (fk.get("constrained_columns") or [])
        ]
        if conn.dialect.name == "sqlite":
            if origin_fks or index_name in indexes or "origin_customer_id" in columns:
                with op.batch_alter_table(table, recreate="always") as batch:
                    for fk in origin_fks:
                        if fk.get("name"):
                            batch.drop_constraint(fk["name"], type_="foreignkey")
                    if index_name in indexes:
                        batch.drop_index(index_name)
                    if "origin_customer_id" in columns:
                        batch.drop_column("origin_customer_id")
        else:
            # InnoDB requires the FK to be removed before its supporting index.
            for fk in origin_fks:
                if fk.get("name"):
                    op.drop_constraint(fk["name"], table, type_="foreignkey")
            if index_name in indexes:
                op.drop_index(index_name, table_name=table)
            if "origin_customer_id" in columns:
                op.drop_column(table, "origin_customer_id")
