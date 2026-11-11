"""个性化推荐改为明确同意后开启。

Revision ID: 0042_privacy_opt_in_default
Revises: 0041_flash_sale_store_product
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0042_privacy_opt_in_default"
down_revision = "0041_flash_sale_store_product"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # 旧记录没有可验证的政策版本/同意时间，不能推定为已同意。
    conn.execute(
        text(
            "UPDATE customers SET recommend_opt_in = 0 "
            "WHERE consent_version IS NULL OR recommend_consent_at IS NULL"
        )
    )
    # SQLite 不支持原地修改列默认值；ORM default 已设为 0。
    if conn.dialect.name != "sqlite":
        conn.execute(
            text(
                "ALTER TABLE customers MODIFY COLUMN recommend_opt_in "
                "INT NOT NULL DEFAULT 0 COMMENT '个性化推荐：顾客明确同意后开启'"
            )
        )


def downgrade() -> None:
    # Consent is affirmative. Rolling application code back cannot create
    # consent, and restoring DEFAULT 1 would silently opt in new customers.
    # Keep the safe DEFAULT 0 established by upgrade.
    return
