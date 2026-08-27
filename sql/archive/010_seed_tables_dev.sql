-- 开发环境默认桌台（无桌台时下单会 400：无效桌台二维码）
-- 正式扫码请用 qr_token；开发编译模式可用 query: token=1001 或 token=<qr_token>
USE smart_noodle_house;

INSERT INTO stores (id, store_no, name, address, phone, status)
SELECT 1, 'S001', '总店', '本地调试', NULL, 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM stores WHERE id = 1);

INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT 1, '1001', '1号桌', '1001',
  'pages/index/index?token=1001&channel=wechat',
  'pages/index/index?token=1001&channel=alipay',
  1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM `tables` WHERE store_id = 1 AND table_no = '1001');

INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT 1, '1002', '2号桌', '1002',
  'pages/index/index?token=1002&channel=wechat',
  'pages/index/index?token=1002&channel=alipay',
  1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM `tables` WHERE store_id = 1 AND table_no = '1002');

INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT 1, '1003', '3号桌', '1003',
  'pages/index/index?token=1003&channel=wechat',
  'pages/index/index?token=1003&channel=alipay',
  1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM `tables` WHERE store_id = 1 AND table_no = '1003');
