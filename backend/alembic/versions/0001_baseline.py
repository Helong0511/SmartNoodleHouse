"""empty

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 基线：现有库以 sql/*.sql 为准；此 revision 仅作 Alembic 起点标记。
    # 新变更请用 `alembic revision --autogenerate` 生成。
    pass


def downgrade() -> None:
    pass
