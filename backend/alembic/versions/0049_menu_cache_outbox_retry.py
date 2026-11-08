"""Keep menu cache invalidation Outbox events retryable across Redis outages.

Revision ID: 0049_menu_cache_outbox_retry
Revises: 0048_reconcile_store_scope

Menu invalidation is idempotent cache-coherence work. It must not become a
permanent dead letter after the generic eight-attempt policy is exhausted.
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
    op.get_bind().execute(
        sa.text(
            """
            UPDATE outbox_events
            SET max_attempts = :max_attempts
            WHERE aggregate_type = 'menu_cache'
              AND event_kind = 'invalidate'
              AND (max_attempts IS NULL OR max_attempts < :max_attempts)
            """
        ),
        {"max_attempts": MAX_ATTEMPTS},
    )


def downgrade() -> None:
    # Deliberately retain the larger retry budget. Lowering it could turn an
    # already-retried but still pending invalidation into a permanent dead letter.
    pass
