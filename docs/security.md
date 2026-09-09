# 安全说明

仓库历史中曾出现过微信 AppSecret / 支付宝应用私钥 / AES Key，一律视为已泄露，上线前必须在各平台控制台轮换。

## 要求

- 真实密钥只放在本地 `.env` / `.env.development` / `.env.production`（已加入 `.gitignore`），勿写入示例文件、脚本、文档或测试。
- 示例与文档仅使用明显虚假占位符（如 `wx_REPLACE_WITH_YOUR_APPID`、`replace_with_wechat_app_secret_not_real`、`2021000000000000`）。
- 轮换范围至少包括：微信小程序 AppSecret、支付宝应用私钥与对应公钥、接口内容加密 AES Key，以及曾入库的 JWT / 人脸加密等对称密钥（如适用）。

## 密钥轮换提醒（生产运维）

建议建立定期轮换与离职即时轮换机制，至少覆盖：

| 类别 | 示例配置项（名称） | 轮换后动作 |
|------|-------------------|------------|
| 应用鉴权 | `JWT_SECRET` | 全员重新登录；可递增用户 `token_version` 使旧令牌失效 |
| 人脸特征 | `FACE_ENCRYPTION_KEY` | 更换后旧密文可能无法解密，需按迁移方案重采或重加密 |
| 微信 | AppSecret、商户 APIv3 Key、商户私钥 | 同步更新 `.env` 与平台证书；验证支付 / 退款回调 |
| 支付宝 | 应用私钥、支付宝公钥、AES Key | 更新后回归授权手机号与支付回调 |
| 外卖 | 美团 / 饿了么 app_secret、token | 重新授权门店绑定 |
| 屏端 / 反代 | `DISPLAY_SCREEN_TOKEN`、`TRUSTED_PROXY_IPS` | 更新叫号屏 URL；确认反代 IP 白名单无误 |

轮换操作不要把新密钥写入 Git、工单附件或聊天记录；审计谁有权读生产 `.env`。

## 运行时加固

- 生产关闭所有 `ALLOW_*_MOCK`。
- `PROCESS_ROLE`：仅一个 leader，避免重复调度。
- `TRUSTED_PROXY_IPS`：仅信任反代 peer，防止伪造客户端 IP。
- Webhook：验签 +（建议）出口 IP 白名单。
- 上传与回调接口限流；管理端敏感操作写 `operation_logs`。
