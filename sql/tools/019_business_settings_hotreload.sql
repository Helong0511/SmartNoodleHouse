-- 业务参数热更新：将可在线调整项幂等写入 system_settings
-- 已有键不会覆盖；正式改值请用管理后台「系统设置 → 业务参数」

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_auto_confirm', 'false', '新外卖单是否自动向平台 confirm；关闭则本地 PENDING'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'delivery_auto_confirm');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'welcome_coupon_name', '新客满30减5', '注册赠券模板名（须与 coupon_templates.name 一致）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'welcome_coupon_name');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_avatar', 'true', '支付宝头像能力（小程序 config 下发）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_avatar');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_nickname', 'true', '支付宝昵称能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_nickname');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_phone_bind', 'false', '支付宝手机号绑定（个人主体通常关闭）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_phone_bind');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_avatar', 'true', '微信头像能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_avatar');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_nickname', 'true', '微信昵称能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_nickname');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_phone_bind', 'false', '微信手机号绑定（个人主体通常关闭）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_phone_bind');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_ticket_order_no', 'barcode', '厨房票订单号：barcode / text / off'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_ticket_order_no');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_print_copies', '1', '小票打印份数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_print_copies');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_voice', '2', '云打印语音：0取消单 1静音 2来单 3退单'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_voice');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'max_upload_size_mb', '5', '单文件上传上限（MB）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'max_upload_size_mb');
