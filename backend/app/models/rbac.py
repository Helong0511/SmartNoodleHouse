from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(50))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("permissions.id"), primary_key=True, index=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), index=True)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    status: Mapped[int] = mapped_column(Integer, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    role: Mapped["Role"] = relationship("Role")


class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        Index("ix_operation_logs_created_at_user_id", "created_at", "user_id"),
        Index("ix_operation_logs_category_created_at", "category", "created_at"),
        Index("ix_operation_logs_created_at_store_id", "created_at", "store_id"),
        Index("ix_operation_logs_created_at_module", "created_at", "module"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    # 审计分类：STAFF/STORE_ADMIN/CASHIER/KITCHEN/SYSTEM/CUSTOMER/DANGER/INTRUSION
    category: Mapped[str | None] = mapped_column(String(20), default="STAFF", comment="审计分类")
    # 门店归属：方便按门店过滤；NULL=全局/未绑定（如登录失败、跨店系统操作）
    store_id: Mapped[int | None] = mapped_column(BigInteger, default=None, comment="操作关联门店")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RoleDelegation(Base, TimestampMixin):
    """临时授权：店长休假期间把权限集委派给被委托人，到期自动失效。"""

    __tablename__ = "role_delegations"
    __table_args__ = (
        Index("idx_rd_delegatee_status_ends", "delegatee_user_id", "status", "ends_at"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    grantor_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    delegatee_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    permission_codes: Mapped[list] = mapped_column(JSON)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    reason: Mapped[str | None] = mapped_column(String(255), default=None)
