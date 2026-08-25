from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "SmartNoodleHouse"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True
    app_timezone: str = "Asia/Shanghai"

    database_url: str = "mysql+aiomysql://root:password@127.0.0.1:3306/smart_noodle_house?charset=utf8mb4"
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    upload_dir: str = "uploads"
    max_upload_size_mb: int = 5
    allowed_image_extensions: str = "jpg,jpeg,png,webp,gif"

    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_mch_id: str = ""
    wechat_api_v3_key: str = ""
    wechat_cert_serial_no: str = ""
    wechat_private_key_path: str = "certs/wechat_apiclient_key.pem"
    wechat_notify_url: str = ""

    alipay_app_id: str = ""
    alipay_private_key: str = ""
    alipay_public_key: str = ""
    alipay_gateway: str = "https://openapi.alipay.com/gateway.do"
    alipay_notify_url: str = ""
    alipay_return_url: str = ""

    # 美团外卖开放平台（waimaiopen）
    meituan_app_id: str = ""
    meituan_app_secret: str = ""
    meituan_shop_id: str = ""
    meituan_notify_url: str = ""
    meituan_api_base: str = "https://waimaiopen.meituan.com"

    # 饿了么商家开放平台（open-api.shop.ele.me）
    eleme_app_id: str = ""
    eleme_app_secret: str = ""
    eleme_shop_id: str = ""
    eleme_notify_url: str = ""
    eleme_access_token: str = ""
    eleme_api_base: str = "https://open-api.shop.ele.me/api/v1"

    tts_provider: str = "local"
    tts_voice: str = "zh-CN"

    camera_url: str = ""
    camera_username: str = ""
    camera_password: str = ""

    face_encryption_key: str = "change-me-face-encryption-key-32bytes!!"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    order_payment_timeout_minutes: int = 15
    visitor_data_retention_days: int = 7
    operation_log_retention_days: int = 90
    welcome_cooldown_seconds: int = 60
    face_match_threshold: float = 0.75
    face_confirm_frames: int = 3

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_image_extensions.split(",")}


@lru_cache
def get_settings() -> Settings:
    return Settings()
