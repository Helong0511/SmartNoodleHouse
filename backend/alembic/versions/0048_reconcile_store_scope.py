"""Add explicit store scope to payment reconciliation batches.

Revision ID: 0048_reconcile_store_scope
Revises: 0047_payment_refund_idem

Existing batches are retained as HQ/global scope (scope_store_id=0). The canonical
baseline schema may already contain this column/index on a fresh install, so this
migration is intentionally idempotent across fresh and previously deployed DBs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_reconcile_store_scope"
down_revision = "0047_payment_refund_idem"
branch_labels = None
depends_on = None

TABLE = "payment_reconcile_batches"
OLD_UNIQUE = "uk_prb_channel_bill"
NEW_UNIQUE = "uk_prb_channel_bill_store"
SCOPE_INDEX = "ix_payment_reconcile_batches_scope_store_id"


def _schema_objects(conn):
    inspector = sa.inspect(conn)
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    unique_constraints = {
        item.get("name") for item in inspector.get_unique_constraints(TABLE)
    }
    indexes = {
        item.get("name"): item
        for item in inspector.get_indexes(TABLE)
    }
    # Some dialects report unique indexes as constraints and others as indexes.
    unique_names = set(unique_constraints)
    unique_names.update(
        name for name, item in indexes.items() if item.get("unique")
    )
    return columns, unique_constraints, indexes, unique_names


def upgrade() -> None:
    conn = op.get_bind()
    # 0036 creates this required reconciliation table. Silently returning here
    # would let Alembic advance while leaving a required feature schema absent.
    if not sa.inspect(conn).has_table(TABLE):
        raise RuntimeError(
            "Cannot apply 0048_reconcile_store_scope: required table "
            "'payment_reconcile_batches' is missing. Restore/repair migration "
            "0036 before retrying Alembic upgrade."
        )
    columns, unique_constraints, indexes, unique_names = _schema_objects(conn)

    has_scope_column = "scope_store_id" in columns
    has_old_unique = OLD_UNIQUE in unique_names
    has_new_unique = NEW_UNIQUE in unique_names
    has_scope_index = SCOPE_INDEX in indexes

    if has_scope_column and not has_old_unique and has_new_unique and has_scope_index:
        return

    with op.batch_alter_table(TABLE) as batch:
        if not has_scope_column:
            batch.add_column(
                sa.Column(
                    "scope_store_id",
                    sa.BigInteger(),
                    nullable=False,
                    server_default=sa.text("0"),
                    comment="0=HQ/global reconciliation; positive value=store ID",
                )
            )

        if has_old_unique:
            if OLD_UNIQUE in unique_constraints:
                batch.drop_constraint(OLD_UNIQUE, type_="unique")
            else:
                # Canonical struct.sql creates this as a named unique index.
                batch.drop_index(OLD_UNIQUE)

        if not has_new_unique:
            batch.create_unique_constraint(
                NEW_UNIQUE,
                ["channel", "bill_date", "scope_store_id"],
            )

        if not has_scope_index:
            batch.create_index(SCOPE_INDEX, ["scope_store_id"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    # Earlier revisions may have been interrupted before this optional table
    # was created. MySQL commits DDL independently, so rollback must tolerate it.
    if not sa.inspect(conn).has_table(TABLE):
        return
    columns, unique_constraints, indexes, _ = _schema_objects(conn)
    if "scope_store_id" not in columns:
        return

    store_scoped_rows = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_reconcile_batches WHERE scope_store_id <> 0"
        )
    ).scalar()
    if int(store_scoped_rows or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade 0048_reconcile_store_scope: store-scoped batches exist; "
            "dropping their scope would expose store-level reconciliation data as HQ data."
        )

    with op.batch_alter_table(TABLE) as batch:
        if SCOPE_INDEX in indexes:
            batch.drop_index(SCOPE_INDEX)
        if NEW_UNIQUE in unique_constraints:
            batch.drop_constraint(NEW_UNIQUE, type_="unique")
        elif NEW_UNIQUE in indexes:
            batch.drop_index(NEW_UNIQUE)
        batch.drop_column("scope_store_id")
        batch.create_unique_constraint(OLD_UNIQUE, ["channel", "bill_date"])
