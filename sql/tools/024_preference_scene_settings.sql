-- 口味场景/天气加权开关（默认开启；幂等，已有键不覆盖）
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'preference_scene_enabled', 'true', '口味场景加权（季节/同星期同餐段）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'preference_scene_enabled');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'weather_pref_enabled', 'true', '天气口味加权（需门店经纬度）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'weather_pref_enabled');
