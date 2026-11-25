"""Create durable per-customer write gates and backfill existing accounts.

Revision ID: 0052_customer_write_gates
Revises: 0051_pos_refund_fencing
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInteger
from alembic import op

revision = "0052_customer_write_gates"
down_revision = "0051_pos_refund_fencing"
branch_labels = None
depends_on = None


def _normalize_trigger_sql(value: str) -> str:
    """Normalize harmless MySQL formatting differences before comparing trigger bodies."""
    return " ".join(str(value).replace("`", "").strip().rstrip(";").lower().split())


def _ensure_mysql_trigger(
    conn: sa.engine.Connection,
    *,
    name: str,
    table: str,
    timing: str,
    event: str,
    action: str,
    create_sql: str,
) -> None:
    """Create a migration-owned trigger, or verify the exact trigger already exists.

    MySQL has no CREATE TRIGGER IF NOT EXISTS. A failed migration can therefore
    leave earlier trigger DDL committed while Alembic has not recorded this
    revision. Checking the existing definition makes a retry safe without
    silently accepting a same-named trigger with different behavior.
    """
    existing = conn.execute(
        sa.text(
            "SELECT EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = :name"
        ),
        {"name": name},
    ).mappings().one_or_none()

    if existing is None:
        conn.exec_driver_sql(create_sql)
        return

    actual_metadata = (
        str(existing["EVENT_OBJECT_TABLE"]).lower(),
        str(existing["ACTION_TIMING"]).upper(),
        str(existing["EVENT_MANIPULATION"]).upper(),
    )
    expected_metadata = (table.lower(), timing.upper(), event.upper())
    if actual_metadata != expected_metadata or _normalize_trigger_sql(
        existing["ACTION_STATEMENT"]
    ) != _normalize_trigger_sql(action):
        raise RuntimeError(
            f"Existing MySQL trigger {name!r} does not match migration 0052; "
            "inspect and reconcile it before retrying the migration."
        )


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("customer_write_gates"):
        op.create_table(
            "customer_write_gates",
            sa.Column(
                "customer_id",
                MySQLBigInteger(unsigned=True).with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                nullable=False,
            ),
            comment="One transaction mutex row per customer; no business state",
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )

    # Record why an outstanding flash-sale entitlement became terminal.
    if inspector.has_table("marketing_flash_sale_claims") and "status_reason" not in {
        c["name"] for c in inspector.get_columns("marketing_flash_sale_claims")
    }:
        op.add_column(
            "marketing_flash_sale_claims",
            sa.Column("status_reason", sa.String(length=40), nullable=True),
        )

    # MySQL commits trigger DDL independently of the Alembic transaction.
    # Make each installation independently retryable after partial failure.
    if conn.dialect.name == "mysql":
        _ensure_mysql_trigger(
            conn,
            name="trg_customers_create_write_gate",
            table="customers",
            timing="AFTER",
            event="INSERT",
            action=(
                "INSERT IGNORE INTO customer_write_gates (customer_id) "
                "VALUES (NEW.id)"
            ),
            create_sql=(
                "CREATE TRIGGER trg_customers_create_write_gate "
                "AFTER INSERT ON customers FOR EACH ROW "
                "INSERT IGNORE INTO customer_write_gates (customer_id) VALUES (NEW.id)"
            ),
        )
        _ensure_mysql_trigger(
            conn,
            name="trg_customers_delete_write_gate",
            table="customers",
            timing="AFTER",
            event="DELETE",
            action="DELETE FROM customer_write_gates WHERE customer_id = OLD.id",
            create_sql=(
                "CREATE TRIGGER trg_customers_delete_write_gate "
                "AFTER DELETE ON customers FOR EACH ROW "
                "DELETE FROM customer_write_gates WHERE customer_id = OLD.id"
            ),
        )

    # Idempotent backfill: supports partially provisioned environments and can
    # be safely re-run after a failed deployment.
    if inspector.has_table("customers"):
        op.execute(
            sa.text(
                "INSERT INTO customer_write_gates (customer_id) "
                "SELECT c.id FROM customers AS c "
                "LEFT JOIN customer_write_gates AS g ON g.customer_id = c.id "
                "WHERE g.customer_id IS NULL"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "mysql":
        op.execute("DROP TRIGGER IF EXISTS trg_customers_delete_write_gate")
        op.execute("DROP TRIGGER IF EXISTS trg_customers_create_write_gate")
    inspector = sa.inspect(conn)
    if inspector.has_table("marketing_flash_sale_claims") and "status_reason" in {
        c["name"] for c in inspector.get_columns("marketing_flash_sale_claims")
    }:
        op.drop_column("marketing_flash_sale_claims", "status_reason")
    if inspector.has_table("customer_write_gates"):
        op.drop_table("customer_write_gates")
