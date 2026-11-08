> **文档维护说明（2026-11-08）**：systemd 应启动宿主 API 服务，由 API 进程加载 Rust 动态库；不能直接执行 .so。运行用户、文件权限、库搜索路径、ABI、架构和重启策略均需在目标发行版现场验证。 具体发布事实以当前 Cargo manifest/lock、CI、构建产物和签名校验为准；本机 Windows 结果不代表 Linux/macOS 结果。不得记录真实密钥或生产 token。
>
> ---
>
# Linux systemd 部署说明

## 1. 重要概念

`libad_tracking_engine.so` 是一个动态库，不是一个独立运行的服务器程序。

正确关系是：

```text
systemd
   ↓
启动 Go / FastAPI / 其他 API 服务
   ↓
API 进程加载 libad_tracking_engine.so
   ↓
调用 Rust C ABI
```

不要尝试让 systemd 直接执行 `.so` 文件。

## 2. 动态库权限

建议：

- API 服务账号拥有动态库的读取/执行权限。
- 不允许普通用户修改生产动态库。
- 动态库发布时记录 SHA-256。
- 生产机器不要从不可信第三方下载 DLL/SO。

检查：

```bash
ls -lh /opt/ad_tracking_engine/target/release/libad_tracking_engine.so
file /opt/ad_tracking_engine/target/release/libad_tracking_engine.so
sha256sum /opt/ad_tracking_engine/target/release/libad_tracking_engine.so
```

## 3. 密钥文件权限

如果由于环境限制必须使用密钥文件，至少应确保只有服务账号可以读取。

例如：

```bash
chmod 600 /etc/ad_tracking_engine/keys.json
chown adtracking:adtracking /etc/ad_tracking_engine/keys.json
```

更推荐使用 KMS、Vault 或 Secret Manager，而不是长期保存明文密钥文件。

## 4. 环境变量

性能参数可以通过环境变量进行调整：

```bash
export AD_TRACKING_WORKERS=4
export AD_TRACKING_BATCH_SIZE=5000
```

这些参数不能改变安全限制。

## 5. TLS

推荐让 Nginx / API Gateway 负责 HTTPS：

```text
Client
  ↓ HTTPS
Nginx / Gateway
  ↓ 内网
API
  ↓
Rust Engine
```

不要因为 Tracking Token 已经使用 AES-GCM 加密，就认为 HTTP 可以代替 HTTPS。

## 6. 日志

不要把以下内容写入普通日志：

- AES Key
- Secret
- 完整 Token
- 完整 Access Token
- 不必要的个人信息

推荐使用：

```text
trace_id
request_id
click_id（按业务安全策略）
event_type
result
latency
error_code
```

## 7. 多 Worker

如果 API 使用多个 Worker：

```text
Worker 1 → Engine Key Set
Worker 2 → Engine Key Set
Worker 3 → Engine Key Set
```

每个进程都必须加载一致的 KID / Key。

Engine 内存状态不会自动跨进程同步。

## 8. 发布与回滚

建议每次发布保存：

```text
Engine Version
Git Commit
Cargo.lock
Rust Version
Target
动态库 SHA-256
配置版本
```

升级流程：

```text
编译
 ↓
测试
 ↓
灰度
 ↓
监控
 ↓
扩大流量
```

回滚时恢复上一版本动态库及对应配置，并确保旧 Token 所需的 KID 仍然存在。
