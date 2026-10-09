from pydantic import BaseModel, Field, field_validator


class BusinessSettingsUpdate(BaseModel):
    settings: dict[str, str | int | float | bool] = Field(description="业务参数键值，见 GET /settings 的 business 列表")

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, v: dict) -> dict:
        if not v:
            raise ValueError("未提交任何配置项")
        if len(v) > 80:
            raise ValueError("单次最多提交 80 个配置项")
        return v


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    logo: str | None = Field(default=None, max_length=500)
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    status: int | None = Field(default=None, ge=0, le=1)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        if not s:
            raise ValueError("门店名称不能为空")
        return s

    @field_validator("logo")
    @classmethod
    def logo_path(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        if not s:
            return None
        if s.startswith("uploads/"):
            s = "/" + s
        if not s.startswith("/uploads/"):
            raise ValueError("Logo 须为站内 uploads 路径，请先上传")
        return s

    @field_validator("phone")
    @classmethod
    def phone_trim(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        if not s:
            return None
        # P3-6: 校验电话号码格式（手机/座机均可，仅允许数字、+、-、空格、括号）
        import re

        if not re.fullmatch(r"[\d+\-\s()]{7,20}", s):
            raise ValueError("电话号码格式不合法（仅允许数字、+、-、空格、括号，长度7-20）")
        return s

    @field_validator("address")
    @classmethod
    def address_trim(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        return s or None
