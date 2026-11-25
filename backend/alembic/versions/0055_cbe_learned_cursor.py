"""Track behavior-event learning per event instead of a timestamp-only watermark.

Revision ID: 0055_cbe_learned_cursor
Revises: 0054_flash_sale_claim_reason
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_cbe_learned_cursor"
down_revision = "0054_flash_sale_claim_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("customer_behavior_events"):
        raise RuntimeError("customer_behavior_events is missing; apply recommendation migrations first")

    columns = {column["name"] for column in inspector.get_columns("customer_behavior_events")}
    if "learned_at" not in columns:
        op.add_column(
            "customer_behavior_events",
            sa.Column("learned_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Preserve the existing profile watermark: events at or before the latest
    # folded timestamp are treated as consumed, avoiding a one-time double fold.
    conn.execute(sa.text("""
        UPDATE customer_behavior_events AS e
        SET learned_at = e.created_at
        WHERE e.learned_at IS NULL
          AND EXISTS (
              SELECT 1
              FROM customer_taste_profiles AS p
              WHERE p.customer_id = e.customer_id
                AND p.last_event_at IS NOT NULL
                AND e.created_at <= (
                    SELECT MAX(p2.last_event_at)
                    FROM customer_taste_profiles AS p2
                    WHERE p2.customer_id = e.customer_id
                )
          )
    """))

    inspector = sa.inspect(conn)
    if not inspector.has_index("customer_behavior_events", "ix_cbe_customer_learned_created"):
        op.create_index(
            "ix_cbe_customer_learned_created",
            "customer_behavior_events",
            ["customer_id", "learned_at", "created_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_index("customer_behavior_events", "ix_cbe_customer_learned_created"):
        op.drop_index("ix_cbe_customer_learned_created", table_name="customer_behavior_events")
    if any(
        column["name"] == "learned_at"
        for column in inspector.get_columns("customer_behavior_events")
    ):
        op.drop_column("customer_behavior_events", "learned_at")
