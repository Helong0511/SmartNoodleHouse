"""Keep cache invalidation Outbox events retryable across Redis outages.

Revision ID: 0049_menu_cache_outbox_retry
Revises: 0048_reconcile_store_scope

Menu and personalized recommendation invalidations are idempotent cache-coherence
work. They must not become permanent dead letters after the generic eight-attempt
policy is exhausted.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_menu_cache_outbox_retry"
down_revision = "0048_reconcile_store_scope"
branch_labels = None
depends_on = None

MAX_ATTEMPTS = 2_147_483_647


def upgrade() -> None:
    conn = op.get_bind()
    # Outbox is required for durable side effects and cache invalidation.
    # Fail rather than record this revision while the table is missing.
    if not sa.inspect(conn).has_table("outbox_events"):
        raise RuntimeError(
            "Cannot apply 0049_menu_cache_outbox_retry: required table "
            "'outbox_events' is missing. Restore the canonical baseline before retrying."
        )
    conn.execute(
        sa.text(
            """
            UPDATE outbox_events
            SET max_attempts = :max_attempts
            WHERE (
                    (aggregate_type = 'menu_cache' AND event_kind = 'invalidate')
                 OR (aggregate_type = 'reco_cache' AND substr(event_kind, 1, 11) = 'invalidate_')
              )
              AND (max_attempts IS NULL OR max_attempts < :max_attempts)
            """
        ),
        {"max_attempts": MAX_ATTEMPTS},
    )


def downgrade() -> None:
    # Deliberately retain the larger retry budget. Lowering it could turn an
    # already-retried but still pending invalidation into a permanent dead letter.
    pass
