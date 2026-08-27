from pydantic import BaseModel, Field, field_validator


class BusinessSettingsUpdate(BaseModel):
    settings: dict[str, str | int | float] = Field(description="业务参数键值，见 GET /settings 的 business 列表")

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, v: dict) -> dict:
        if not v:
            raise ValueError("未提交任何配置项")
        if len(v) > 32:
            raise ValueError("单次最多提交 32 个配置项")
        return v


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
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

    @field_validator("phone")
    @classmethod
    def phone_trim(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        return s or None

    @field_validator("address")
    @classmethod
    def address_trim(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        return s or None
