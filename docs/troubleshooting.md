# 运维排障

常见问题速查（无真实密钥；配置项仅名称）。

## 启动失败

| 现象 | 排查 |
|------|------|
| `JWT_SECRET` / `FACE_ENCRYPTION_KEY` 过弱 | 生产启动校验拒绝；换 ≥32 字符高熵串 |
| 未配置 `DISPLAY_SCREEN_TOKEN` | 生产叫号屏强制；填 opaque 短令牌 |
| `PROCESS_ROLE` 非法 | 仅 `leader` / `worker`；集群只留一个 leader |
| 支付通道未配齐 | 微信或支付宝至少配通一套 |
| 打印开了但未配主机 / 云账号 | `PRINTER_MODE` + `PRINTER_HOST` 或芯烨云三项 |

## 支付 / 回调

- 回调 403 / 验签失败：核对平台证书、公钥、通知 URL 与 `WEBHOOK_IP_ALLOWLIST`。
- 重复入账：查 `payment_callback_receipts` 与 Redis 幂等键。
- 客户端 IP 异常：检查 `TRUSTED_PROXY_IPS` 是否仅为真实反代。

## WebSocket / 叫号屏

- 员工频道：token 走 `Sec-WebSocket-Protocol`，**禁止**把长 JWT 放 query。
- 叫号屏：`/display?store_id=&token=` 使用 `DISPLAY_SCREEN_TOKEN`；生产空令牌会拒连。
- 频繁断线：查 per-IP / per-user 连接上限与空闲超时（约 90s）。

## 人脸迎宾

- 授权失败「维度不匹配」：确认 InsightFace buffalo_l 512 维；禁止客户端向量。
- 不迎宾：查阈值、冷却、摄像头 ONLINE、`ENABLE_RECOGNITION_WORKER`、leader 进程。
- 提示 basic anti-spoof：当前无真活体，仅粗检。

## 打印 / 班次

- 小票乱码或异常切纸：用户备注已清 ESC/GS；查编码 GBK。
- 开班提示「已有班次」：同一收银员仅一个 OPEN（`uk_cashier_shift_one_open`）；先交班。

## 日志与上传

- 客户端日志被拒：条数 / 消息长度 / 控制字符限制。
- 头像拉取失败：远程 URL 禁 localhost / 私网 / metadata，限跳转与体积。

更多见 [deployment.md](deployment.md)、[security.md](security.md)。
