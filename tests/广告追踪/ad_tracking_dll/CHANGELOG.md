# 更新日志

## v1.1.0 修复补丁

- 修复 Rust `now_unix()` 的嵌套 `Result` 类型错误，解决 Windows `cargo build --release` 的 E0308 编译失败。

## v1.1.0

- 增加 Windows DLL Version Resource 信息配置。
- 增加作者、公司、联系方式、版权、产品名称等元数据配置说明。
- 增加 `DLL_METADATA_CN.md` 中文配置说明。

1.1.0

### 安全改进

- 增加硬件感知的性能配置接口。
- 增加严格 Payload Schema，默认启用。
- 增加 Tracking 字段长度限制。
- 增加 `exp > iat` 检查。
- 增加 Token 最大有效期检查。
- 增加 Token 最大年龄检查。
- 将 Version + KID 绑定到 AES-GCM AAD，防止仅修改 Header 中的 KID。
- Unknown KID 对外统一返回 Token 认证失败，减少密钥存在性信息泄露。
- 增加 KID 数量限制。
- 增加 ABI 版本查询接口。

### 性能与兼容性

- 增加 CPU / 内存感知的 Worker、Batch 建议。
- 支持 Windows / Linux / macOS 动态库发现。
- 支持主要 x86_64 / ARM64 平台的构建设计。
- 压力测试改为分批提交任务，避免一次性创建大量 Future 导致测试程序内存压力。
- 增加 P50 / P95 / P99 延迟统计。
- 将原来的占位 Benchmark 改为实际 Generate / Verify 基准测试。

### 测试与文档

- 增加硬件检测测试。
- 完善 FFI、协议、安全、生产部署文档。
- README 改为从“全新电脑 / 全新服务器”开始的完整安装说明。

### 业务边界

以下能力仍然属于业务平台，而不是 Rust Engine：

- Replay 防护
- Click / Conversion 幂等
- Redis 状态
- PostgreSQL 业务数据
- Cap / 扣量
- 结算
- Postback 重试
- 分布式密钥存储

生产环境应结合 Redis、PostgreSQL、KMS/Vault 或同等基础设施实现。
