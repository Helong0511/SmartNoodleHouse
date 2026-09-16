# 生产环境发布检查清单

在把广告追踪 Engine 部署到正式环境之前，建议逐项确认。

## 一、密钥安全

- [ ] 生产密钥没有提交到 Git。
- [ ] 生产密钥没有写入 Rust/Python 源代码。
- [ ] 优先通过 KMS、Vault 或 Secret Manager 注入密钥。
- [ ] 当前 KID 已正确配置。
- [ ] 旧 KID 在旧 Token 有效期内继续保留。
- [ ] 旧 Token 全部过期并经过安全缓冲期后，再删除旧 KID。
- [ ] 如果不得不使用密钥文件，文件权限限制为服务账号可读。
- [ ] 不在日志、异常通知、聊天通知中输出密钥。

## 二、原生动态库

- [ ] 在可信 CI 或可信构建机器编译。
- [ ] 生产构建使用经过审核的 Cargo.lock。
- [ ] 已执行依赖安全检查。
- [ ] 已记录 DLL/SO/DYLIB 的 SHA-256。
- [ ] 目标 CPU 架构与编译目标一致。
- [ ] Linux、Windows、macOS 分别完成测试。
- [ ] ARM64 目标已经使用 ARM64 构建或经过可靠交叉编译测试。

## 三、网络 API

- [ ] 全部生产接口使用 HTTPS。
- [ ] API Gateway / Nginx 已配置请求大小限制。
- [ ] 已配置限流。
- [ ] 管理接口已认证和授权。
- [ ] 普通日志不记录完整 Token。
- [ ] 不在 Telegram、邮件或其他通知中发送完整 Token、Secret 或不必要的 PII。

## 四、Replay / Conversion 幂等

Rust Engine **不会自动阻止合法 Token 被重复使用**。

因此必须检查：

- [ ] Click ID 在数据库中具有业务唯一性。
- [ ] Conversion 已设计唯一业务键。
- [ ] 需要一次性消费的操作使用 Redis SETNX + TTL 或同等机制。
- [ ] 已测试并发重复请求。
- [ ] 数据库唯一约束冲突会被正确识别为重复事件，而不是错误 500。

## 五、归因数据

建议在 Click / Conversion 发生时保存必要的归因快照，例如：

- click_id
- offer_id
- campaign_id
- publisher_id
- advertiser_id
- source
- sub_id
- fbclid / fbp（按业务需要）
- gclid（按业务需要）
- UTM 参数
- trace_id / request_id
- 事件时间

不要把“以后永远能够解密 Token”作为唯一的数据保存方案。

## 六、失败策略

安全相关失败必须默认拒绝或进入明确的隔离流程：

- [ ] Unknown KID → 拒绝。
- [ ] AES-GCM Tag 错误 → 拒绝。
- [ ] Token 过期 → 拒绝。
- [ ] Payload 非法 → 拒绝。
- [ ] 必要 Replay 检查无法完成 → 按明确业务策略处理，不得静默当成安全。
- [ ] 数据库不可用 → 涉及资金/扣量的事件不得静默接受。

## 七、监控

- [ ] click → conversion → postback → deduction 全链路具有 trace_id。
- [ ] 监控 Token 认证失败数量。
- [ ] 监控过期 Token 数量。
- [ ] 监控 Unknown KID。
- [ ] 监控重复 Conversion。
- [ ] 监控 Generate / Verify 延迟。
- [ ] 监控 P95 / P99。
- [ ] 监控 CPU、内存、Redis、PostgreSQL。
- [ ] 日志中没有密钥和不必要的敏感信息。

## 八、多进程 / 多服务器

Engine 内部密钥是进程内存状态。

因此：

```text
进程 A
  └── Key Set A

进程 B
  └── Key Set B
```

不会自动共享。

所有 API Worker / Pod / VM 必须从统一的安全密钥来源加载一致的 Key Set。
