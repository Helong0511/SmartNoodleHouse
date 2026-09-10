-- 第二批可热更业务参数（改完无需重启后端）
-- 幂等；已有键不覆盖。正式改值请用管理后台「系统设置 → 业务参数」

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'jwt_expire_minutes', '1440', '员工 Token 有效期（分钟）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'jwt_expire_minutes');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'customer_jwt_expire_minutes', '1440', '顾客 Token 有效期（分钟）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'customer_jwt_expire_minutes');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_ip_allowlist', '', 'Webhook IP 白名单（CIDR 逗号分隔）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'webhook_ip_allowlist');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_require_allowlist', 'false', '生产强制要求 Webhook 白名单'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'webhook_require_allowlist');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'trusted_proxy_ips', '127.0.0.1,::1', '可信反代 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'trusted_proxy_ips');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'h5_public_base_url', '', 'H5 对外域名'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'h5_public_base_url');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'dev_lan_host', '', '开发机局域网 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'dev_lan_host');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'mini_program_env_version', 'auto', '小程序码版本 auto/develop/trial/release'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'mini_program_env_version');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_enabled', 'false', '厨房打印总开关'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_enabled');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_mode', 'local', '打印机模式 local/xpyun'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_mode');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_host', '', '局域网打印机 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_host');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_port', '9100', '打印机端口'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_port');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'wechat_subscribe_template_order_ready', '', '微信出餐订阅消息模板 ID'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'wechat_subscribe_template_order_ready');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'allowed_image_extensions', 'jpg,jpeg,png,webp,gif', '允许的图片扩展名'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'allowed_image_extensions');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'app_timezone', 'Asia/Shanghai', '业务时区'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'app_timezone');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_env_fallback_store_id', '0', '外卖 .env 兜底门店 ID（多店生产须 0）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'delivery_env_fallback_store_id');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'preference_scene_enabled', 'true', '口味场景加权（季节/同星期同餐段）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'preference_scene_enabled');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'weather_pref_enabled', 'true', '天气口味加权（需门店经纬度）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'weather_pref_enabled');
