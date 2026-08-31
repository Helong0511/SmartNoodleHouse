-- 厨房 KDS 超时自动出餐（幂等）
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_auto_complete_minutes', '20', '支付后厨房 KDS 未处理超过该分钟数则自动完成出餐；0=关闭'
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_auto_complete_minutes');
