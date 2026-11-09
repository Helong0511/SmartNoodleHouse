> **文档维护说明（2026-11-08）**：2026-11-08 本机 Windows 验证：cargo metadata --locked 成功；cargo test --locked --all-targets 为 7 passed/0 failed，benchmark target generate/verify 成功；cargo build --release --locked 成功，DLL 282,624 bytes。该证据只对应当时本机 commit，其他平台需按 CI matrix 单独验证。 具体发布事实以当前 Cargo manifest/lock、CI、构建产物和签名校验为准；本机 Windows 结果不代表 Linux/macOS 结果。不得记录真实密钥或生产 token。
>
> ---
>
# 广告追踪 ID Engine 1.1.0

**Rust 高性能广告 Tracking Token 加密引擎 / Python ctypes / C ABI / 跨平台 / 硬件感知**

> 本文档按照“**全新电脑 / 全新服务器，从零开始安装**”编写。
> 假设目标机器没有安装 Rust、Python、Git、C/C++ 编译环境，也没有配置任何项目依赖。
> 请严格按照当前机器的操作系统章节执行。

---

## 目录

1. [项目简介](#1-项目简介)
2. [核心能力与边界](#2-核心能力与边界)
3. [项目目录](#3-项目目录)
4. [支持的平台与硬件](#4-支持的平台与硬件)
5. [全新 Ubuntu Linux 服务器部署](#5-全新-ubuntu-linux-服务器部署)
6. [全新 Windows 电脑部署](#6-全新-windows-电脑部署)
7. [全新 macOS 电脑部署](#7-全新-macos-电脑部署)
8. [首次编译与 Cargo.lock](#8-首次编译与-cargolock)
9. [完整测试](#9-完整测试)
10. [硬件检测与性能自适应](#10-硬件检测与性能自适应)
11. [压力测试](#11-压力测试)
12. [Python 调用](#12-python-调用)
13. [C/C++ 调用](#13-cc-调用)
14. [Go 调用](#14-go-调用)
15. [Token 格式](#15-token-格式)
16. [Payload 字段规则](#16-payload-字段规则)
17. [密钥管理与轮换](#17-密钥管理与轮换)
18. [Replay 与 Conversion 幂等](#18-replay-与-conversion-幂等)
19. [生产环境部署](#19-生产环境部署)
20. [不同硬件配置建议](#20-不同硬件配置建议)
21. [跨平台与跨架构说明](#21-跨平台与跨架构说明)
22. [安全要求](#22-安全要求)
23. [发布前检查](#23-发布前检查)
24. [升级与回滚](#24-升级与回滚)
25. [常见问题](#25-常见问题)
26. [全新 Ubuntu 服务器快速清单](#26-全新-ubuntu-服务器快速清单)
27. [最终架构建议](#27-最终架构建议)

---

# 1. 项目简介

这是一个面向广告追踪、CPL、归因、Click ID、Conversion 等业务的 **Rust 原生加密核心**。

典型流程：

```text
广告 / 落地页
    │
    ▼
业务服务生成 Tracking Token
    │
    ▼
Rust AES-256-GCM 加密
    │
    ▼
Base64URL Token
    │
    ▼
点击 / 跳转 / 归因
    │
    ▼
Rust Verify
    │
    ▼
验证成功后得到可信 Payload
    │
    ▼
业务层继续执行 Click / Conversion / Postback
```

本项目通过 **C ABI** 提供稳定的原生接口，并提供 Python `ctypes` 封装。

可被以下语言或技术调用：

- Python
- FastAPI
- C
- C++
- Go
- 其他支持 C ABI / FFI 的语言

---

# 2. 核心能力与边界

## 2.1 Engine 负责什么

- Tracking Token 生成
- AES-256-GCM 加密
- Token 完整性验证
- Token 解密
- Token 结构验证
- Click ID 生成
- KID 密钥版本管理
- Token 时间有效性检查
- Payload 字段限制
- FFI 参数检查
- FFI panic 防护
- 跨平台动态库加载
- 主机 CPU / 内存检测
- Worker / Batch 性能建议

## 2.2 Engine 不负责什么

以下属于广告平台业务层，不应硬塞进加密 Engine：

- Redis Replay 防护
- PostgreSQL Conversion 幂等
- 广告扣量
- CPL 计费
- Publisher / Advertiser
- Offer / Campaign
- 结算
- Postback 重试
- Cap
- 价格规则
- 权限系统
- 用户登录
- HTTP API
- 分布式任务队列

建议架构：

```text
                    ┌────────────────────┐
                    │   广告业务 API     │
                    │ FastAPI / Go 等    │
                    └─────────┬──────────┘
                              │
                     C ABI / FFI 调用
                              │
                    ┌─────────▼──────────┐
                    │ Rust Tracking      │
                    │ Encryption Engine  │
                    └─────────┬──────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
         Redis             PostgreSQL       KMS/Vault
      Replay/缓存         Conversion/业务    密钥管理
```

---

# 3. 项目目录

```text
ad_tracking_engine_production_v1_cn/
│
├── Cargo.toml
├── README.md
├── LICENSE
├── VERSION.txt
├── CHANGELOG.md
│
├── src/
│   └── lib.rs                         # Rust 加密核心
│
├── include/
│   └── tracking.h                     # C ABI 头文件
│
├── python/
│   ├── tracking.py                   # Python ctypes 封装
│   ├── smoke_test.py                 # Python 冒烟测试
│   └── requirements.txt              # Python 测试依赖
│
├── tests/
│   ├── test_ffi.py                   # FFI 测试
│   ├── test_vectors.py               # Token 协议测试
│   ├── test_hardware.py               # 硬件检测测试
│   └── stress_test.py                # 压力测试
│
├── benches/
│   └── benchmark.rs                 # Rust 基准测试
│
├── deploy/
│   ├── production_checklist.md       # 生产发布检查清单
│   └── systemd-notes.md              # Linux 服务部署说明
│
├── build_ubuntu.sh                    # Ubuntu 构建脚本
├── build_windows.bat                  # Windows 构建脚本
└── run_stress_windows.bat             # Windows 压测脚本
```

---

# 4. 支持的平台与硬件

## 4.1 主要平台

| 操作系统 | CPU 架构 | 原生库 |
|---|---|---|
| Ubuntu / Debian / 其他 Linux | x86_64 | `.so` |
| Linux | ARM64 / aarch64 | `.so` |
| Windows | x86_64 | `.dll` |
| Windows | ARM64 | `.dll` |
| macOS Intel | x86_64 | `.dylib` |
| macOS Apple Silicon | arm64 | `.dylib` |

**注意：** 一个架构编译出来的动态库不能直接假设可以运行在所有其他 CPU 架构上。生产环境应针对目标平台分别编译和测试。

## 4.2 软件最低建议

```text
Python：3.10+
Rust：1.85+（推荐 stable）
Cargo：与 stable Rust 配套
```

## 4.3 硬件建议

| 机器 | 建议用途 |
|---|---|
| 1C / 2GB | 开发 / 极低流量测试 |
| 2C / 4GB | 小型 API 服务 |
| 4C / 8GB | 推荐生产起步配置 |
| 8C / 16GB | 中高并发 |
| 16C / 32GB+ | 高并发或多业务进程 |

Engine 本身内存占用并不高。整套广告平台最终容量还取决于 API、Redis、数据库、网络、日志和业务代码。

---

# 5. 全新 Ubuntu Linux 服务器部署

以下以 Ubuntu 22.04 / 24.04 为例。

## 5.1 第一步：确认服务器信息

登录全新服务器后执行：

```bash
cat /etc/os-release
uname -a
uname -m
nproc
free -h
```

重点确认：

- 系统版本
- CPU 架构
- CPU 核数
- 内存容量

x86_64 示例：

```text
x86_64
```

ARM64 示例：

```text
aarch64
```

## 5.2 第二步：更新系统

```bash
sudo apt update
sudo apt upgrade -y
```

## 5.3 第三步：安装基础工具

```bash
sudo apt install -y \
    build-essential \
    curl \
    git \
    pkg-config \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv
```

检查：

```bash
gcc --version
make --version
curl --version
python3 --version
pip3 --version
git --version
```

## 5.4 第四步：安装 Rust

推荐使用 rustup。

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

安装过程中选择默认选项即可。

让当前终端立即加载 Cargo：

```bash
source "$HOME/.cargo/env"
```

检查：

```bash
rustc --version
cargo --version
rustup --version
```

设置 stable：

```bash
rustup toolchain install stable
rustup default stable
rustup update
```

## 5.5 第五步：检查 Rust Target

```bash
rustup target list --installed
```

普通 Ubuntu x86_64：

```text
x86_64-unknown-linux-gnu
```

ARM64：

```bash
rustup target add aarch64-unknown-linux-gnu
```

如果直接在 ARM64 服务器本机编译，一般使用默认 host target 即可。

## 5.6 第六步：创建项目目录

例如：

```bash
sudo mkdir -p /opt/ad_tracking_engine
sudo chown -R "$USER":"$USER" /opt/ad_tracking_engine
cd /opt/ad_tracking_engine
```

将项目完整复制到该目录。

最终类似：

```text
/opt/ad_tracking_engine/
├── Cargo.toml
├── src/
├── python/
├── tests/
└── include/
```

## 5.7 第七步：创建 Python 虚拟环境

```bash
cd /opt/ad_tracking_engine
python3 -m venv .venv
```

激活：

```bash
source .venv/bin/activate
```

升级 pip：

```bash
python -m pip install --upgrade pip setuptools wheel
```

安装测试依赖：

```bash
python -m pip install -r python/requirements.txt
```

检查：

```bash
python --version
python -m pip --version
```

## 5.8 第八步：首次编译

```bash
cargo build --release
```

成功后 Linux 通常会生成：

```text
target/release/libad_tracking_engine.so
```

## 5.9 第九步：使用 Ubuntu 构建脚本

```bash
chmod +x build_ubuntu.sh
./build_ubuntu.sh
```

## 5.10 第十步：确认动态库

```bash
ls -lh target/release/
```

可以进一步检查：

```bash
file target/release/libad_tracking_engine.so
```

---

# 6. 全新 Windows 电脑部署

以下以 Windows 10/11 x64 为主要示例。

## 6.1 查看 CPU / 内存 / 系统架构

打开 PowerShell：

```powershell
Get-CimInstance Win32_Processor | Select-Object NumberOfCores,NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsArchitecture
```

## 6.2 安装 Git

安装 Git 后检查：

```powershell
git --version
```

## 6.3 安装 Visual Studio Build Tools

Rust Windows MSVC 编译环境通常需要 Microsoft C++ Build Tools。

安装时选择：

```text
Desktop development with C++
```

并确保包含：

```text
MSVC C++ build tools
Windows SDK
```

安装完成后重新打开 PowerShell。

## 6.4 安装 Rust

使用 rustup 安装 stable MSVC 工具链。

安装后：

```powershell
rustc --version
cargo --version
rustup show
```

## 6.5 安装 Python 3.10+

安装后：

```powershell
python --version
python -m pip --version
```

## 6.6 创建 Python 虚拟环境

进入项目：

```powershell
cd C:\ad_tracking_engine
```

创建虚拟环境：

```powershell
python -m venv .venv
```

激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r python\requirements.txt
```

## 6.7 编译 Rust

```powershell
cargo build --release
```

Windows 目标动态库通常为：

```text
target\release\ad_tracking_engine.dll
```

也可以运行：

```powershell
.\build_windows.bat
```

## 6.8 如果 Python 找不到 DLL

可以把 DLL 放到项目约定的 `bin` 目录，或者加入 PATH。

PowerShell 临时加入 PATH：

```powershell
$env:PATH="C:\ad_tracking_engine\bin;" + $env:PATH
```

然后重新启动 Python 程序。

---

# 7. 全新 macOS 电脑部署

支持 Intel 和 Apple Silicon。

## 7.1 查看硬件

```bash
uname -m
sysctl -n hw.ncpu
sysctl -n hw.memsize
```

Apple Silicon：

```text
arm64
```

Intel：

```text
x86_64
```

## 7.2 安装 Xcode Command Line Tools

```bash
xcode-select --install
```

检查：

```bash
clang --version
make --version
```

## 7.3 安装 Homebrew（如果没有）

安装完成后：

```bash
brew --version
```

## 7.4 安装 Git / Python

```bash
brew install git python
```

检查：

```bash
git --version
python3 --version
```

## 7.5 安装 Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

检查：

```bash
rustc --version
cargo --version
```

## 7.6 创建 Python 虚拟环境

```bash
cd /path/to/ad_tracking_engine
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r python/requirements.txt
```

## 7.7 编译

```bash
cargo build --release
```

macOS 动态库通常为：

```text
target/release/libad_tracking_engine.dylib
```

---

# 8. 首次编译与 Cargo.lock

## 8.1 为什么需要 Cargo.lock

生产环境建议固定依赖解析结果，避免未来重新构建时依赖版本发生非预期变化。

如果项目没有 Cargo.lock，第一次在可信构建环境执行：

```bash
cargo generate-lockfile
```

然后提交到 Git：

```bash
git add Cargo.lock
git commit -m "锁定 Rust 依赖版本"
```

之后生产构建使用：

```bash
cargo build --release --locked
```

## 8.2 注意

本压缩包不应伪造 Cargo.lock。

如果目标机器可以访问 crates.io，并且 Rust/Cargo 已正确安装，应由实际构建环境生成并审核真实的 Cargo.lock。

---

# 9. 完整测试

## 9.1 Python 语法检查

Linux / macOS：

```bash
python -m py_compile \
    python/tracking.py \
    python/smoke_test.py \
    tests/test_ffi.py \
    tests/test_vectors.py \
    tests/test_hardware.py \
    tests/stress_test.py
```

Windows：

```powershell
python -m py_compile python\tracking.py python\smoke_test.py tests\test_ffi.py tests\test_vectors.py tests\test_hardware.py tests\stress_test.py
```

## 9.2 Python 测试

```bash
python -m pytest -q
```

如果 native library 尚未编译，涉及 FFI 的测试可能无法运行，这是正常的安装顺序问题；先完成 Rust 编译，再重新执行。

## 9.3 Rust 单元测试

```bash
cargo test
```

## 9.4 Rust 发布构建

```bash
cargo build --release
```

## 9.5 Clippy

```bash
cargo clippy --all-targets --all-features -- -D warnings
```

## 9.6 依赖安全检查

安装 cargo-audit：

```bash
cargo install cargo-audit
```

执行：

```bash
cargo audit
```

如果企业 CI 已经使用其他依赖扫描工具，也应将结果纳入发布门禁。

---

# 10. 硬件检测与性能自适应

本项目不会根据服务器性能降低 AES、Token 校验、时间限制等安全要求。

**硬件自适应只调整性能建议，不调整安全边界。**

检测内容包括：

- CPU 可用并行度
- Linux CPU affinity
- 系统内存
- 推荐 Worker 数
- 推荐 Batch Size

## 10.1 查看 Python 硬件配置

```bash
python -c "from python.tracking import hardware_profile; import pprint; pprint.pp(hardware_profile())"
```

## 10.2 查看 Engine 性能配置

```bash
python -c "from python.tracking import TrackingEngine; import pprint; pprint.pp(TrackingEngine.performance_info())"
```

## 10.3 默认策略

大致策略：

| CPU | 推荐 Batch |
|---:|---:|
| 1-2 核 | 2000 |
| 3-4 核 | 5000 |
| 5-8 核 | 10000 |
| 9 核以上 | 20000 |

推荐 Worker 默认上限为 16。

内存不足时会进一步降低 Worker 建议值。

## 10.4 环境变量覆盖

Worker：

```bash
export AD_TRACKING_WORKERS=4
```

Batch：

```bash
export AD_TRACKING_BATCH_SIZE=5000
```

Windows PowerShell：

```powershell
$env:AD_TRACKING_WORKERS="4"
$env:AD_TRACKING_BATCH_SIZE="5000"
```

这些参数用于性能调优，不能用来绕过安全限制。

---

# 11. 压力测试

压力测试不要一次性创建几十万甚至几百万个 Python Future。

本项目的压力测试采用**分批提交任务**，避免测试程序本身因为 Future 数量过大造成内存压力。

## 11.1 Linux / macOS

```bash
python tests/stress_test.py
```

## 11.2 Windows

```powershell
python tests\stress_test.py
```

或者：

```powershell
.\run_stress_windows.bat
```

## 11.3 压测关注指标

程序会关注：

- 总请求数
- 成功数
- 失败数
- QPS / 吞吐量
- 平均延迟
- P50
- P95
- P99
- 最大延迟
- Click ID 唯一性
- Token 篡改检测
- 非法 Token 拒绝

不要只看平均 QPS。

生产容量评估至少应该同时观察 P95/P99、CPU、内存和实际业务链路。

---

# 12. Python 调用

Python 封装位于：

```text
python/tracking.py
```

基本使用：

```python
from python.tracking import TrackingEngine

engine = TrackingEngine()

engine.set_key(1, "0123456789abcdef0123456789abcdef")
engine.set_current_kid(1)

token = engine.generate({
    "offer_id": 1001,
    "campaign_id": 2001,
    "publisher_id": 3001,
    "source": "facebook",
})

payload = engine.verify(token)
print(payload)
```

生产环境不要把真实密钥硬编码到源代码。

---

# 13. C/C++ 调用

头文件：

```text
include/tracking.h
```

动态库：

```text
Linux   -> libad_tracking_engine.so
Windows -> ad_tracking_engine.dll
macOS  -> libad_tracking_engine.dylib
```

调用时必须遵守 C ABI 中定义的内存释放规则。

由 Engine 返回、明确要求调用释放函数的缓冲区，必须使用对应的 Engine free API 释放，不能混用其他 allocator。

---

# 14. Go 调用

Go 可以通过 cgo 调用 C ABI。

推荐结构：

```text
Go API
  ↓
cgo
  ↓
tracking.h
  ↓
Rust shared library
```

生产环境建议：

1. 单独封装 Go package。
2. 不要在业务代码中到处直接写 cgo。
3. 统一处理 native error。
4. 统一处理动态库加载。
5. 做并发测试。
6. 做 Linux x86_64、Linux ARM64 等目标架构测试。

---

# 15. Token 格式

当前 Token 采用二进制结构后再进行 Base64URL 编码。

核心结构：

```text
Version
  +
KID
  +
Nonce
  +
Ciphertext + Authentication Tag
```

AES-GCM 的认证数据（AAD）绑定 Version + KID。

因此攻击者不能只修改 Token Header 中的 KID 来尝试切换密钥并继续通过认证。

Token 使用 URL-safe Base64，适合放在：

- 广告 URL
- Query Parameter
- Redirect
- Click URL
- Tracking 参数

但业务系统仍然必须对 URL 长度进行整体控制。

---

# 16. Payload 字段规则

默认启用严格 Payload 模式。

允许的主要字段包括：

```text
v
kid
iat
exp
click_id
offer_id
campaign_id
publisher_id
advertiser_id
source
sub_id
fbclid
fbp
gclid
utm_source
utm_medium
utm_campaign
utm_content
utm_term
```

## 16.1 字符串长度

一般 Tracking 字符串限制为最多 256 字符。

`click_id` 单独限制在合理长度范围内。

## 16.2 数字字段

以下 ID 字段必须是非负 JSON 整数：

```text
offer_id
campaign_id
publisher_id
advertiser_id
```

## 16.3 时间

必须满足：

```text
exp > iat
```

同时存在：

- 最大 TTL
- 最大 Token Age
- 过期校验
- 未来时间偏移校验

---

# 17. 密钥管理与轮换

Engine 内部保存的是**当前进程内存中的密钥状态**。

因此：

```text
进程 A 的 Key
≠
进程 B 自动共享的 Key
```

多进程、多实例、多服务器部署必须由外部系统统一管理密钥。

推荐：

```text
KMS / Vault / Secret Manager
          ↓
      启动时加载
          ↓
Rust Engine
```

## 17.1 KID

KID 用于标识密钥版本。

建议：

```text
当前 KID = 新密钥
旧 KID   = 暂时保留
```

等所有旧 Token 超过最大有效期，再删除旧 KID。

## 17.2 轮换流程

```text
生成新 Key
    ↓
加入新 KID
    ↓
新 Token 使用新 KID
    ↓
旧 KID 继续 Verify
    ↓
等待旧 Token 全部过期
    ↓
删除旧 KID
```

不要在新 Key 上线的同时立即删除旧 Key。

## 17.3 卸载 DLL 前显式清理内存密钥

Rust 的全局 `static` 状态不能被假定会在 DLL 卸载时自动执行析构。因此，宿主程序必须在停止所有并发调用、准备卸载动态库之前调用：

- C/C++：`tracking_clear_keys()`
- Python：`engine.clear_keys()`

该接口会覆写并清除所有已配置密钥，同时将当前 KID 设为空。清理后必须重新调用 `tracking_set_key` 和 `tracking_set_current_kid` 才能继续生成 Token。

**生命周期要求：**
1. 先停止接收新的 Token 生成/验证请求。
2. 等待所有正在执行的 FFI 调用结束。
3. 调用 `tracking_clear_keys()`。
4. 再释放宿主侧资源并卸载动态库。

不要在仍有并发调用时清理密钥；调用方必须负责协调关闭流程。该接口不能保证清除操作系统、编译器、调用方输入缓冲区或密码管理器中的其他副本，因此长期密钥仍应由 KMS/Vault 管理。

---

# 18. Replay 与 Conversion 幂等

**AES-GCM 认证成功不等于请求不能重复使用。**

例如：

```text
同一个合法 Token
        ↓
第一次请求：成功
        ↓
第二次请求：仍然可能是合法 Token
```

因此 Replay Protection 必须由业务层实现。

## 18.1 Redis

需要“一次性消费”的场景，可以使用：

```text
SETNX
```

配合合理 TTL。

## 18.2 PostgreSQL

Conversion 应建立唯一约束，例如业务唯一键：

```text
click_id + conversion_type
```

具体唯一键必须根据业务规则设计。

## 18.3 并发幂等

不能只在 Python/Go 代码里：

```text
先 SELECT
再 INSERT
```

因为高并发下两个请求可能同时 SELECT 不存在，然后同时 INSERT。

应该依赖数据库唯一约束、事务和明确的冲突处理。

---

# 19. 生产环境部署

## 19.1 推荐架构

```text
Internet
   │
   ▼
Nginx / API Gateway
   │
   │ HTTPS
   ▼
Go / FastAPI API
   │
   ├──────────────► Redis
   │
   ├──────────────► PostgreSQL
   │
   ├──────────────► KMS / Vault
   │
   ▼
Rust Tracking Engine
```

## 19.2 Linux systemd

`.so` 是动态库，不是独立 daemon。

正确方式是：

```text
systemd
  ↓
API 服务
  ↓
加载 libad_tracking_engine.so
```

而不是让 systemd 直接启动 `.so`。

详细说明见：

```text
deploy/systemd-notes.md
```

## 19.3 TLS

Token、Click、Conversion、Postback 等网络请求必须使用 HTTPS。

不要因为 Token 已经加密，就认为 HTTP 是安全的。

Token 加密和传输层加密解决的是不同问题。

## 19.4 日志

不要在普通日志中记录完整 Token。

不要记录：

- AES Key
- Secret
- 完整 Access Token
- 完整 Tracking Token
- 不必要的 PII

推荐记录：

```text
trace_id
request_id
click_id（按业务安全策略）
offer_id
publisher_id
事件类型
结果
耗时
错误类型
```

---

# 20. 不同硬件配置建议

## 20.1 1C / 2GB

用途：

- 开发
- 功能测试
- 极低流量

不建议承担大型广告平台生产流量。

## 20.2 2C / 4GB

用途：

- 小型 API
- 测试环境
- 低流量生产

建议从较低 Worker 数开始。

## 20.3 4C / 8GB

这是一个比较合理的生产起步配置。

建议：

```text
Worker：根据硬件检测结果
Batch：通常 5000 附近开始测试
```

然后通过真实 QPS、P95、P99 调整。

## 20.4 8C / 16GB

可以支持更高并发的 API 宿主，但实际容量必须经过压测。

不要简单认为：

```text
CPU × 2 = QPS × 2
```

真实吞吐量还受：

- Python GIL / worker 模型
- JSON
- Redis
- PostgreSQL
- 网络
- 日志
- GC（如果宿主语言存在）
- 锁竞争

影响。

## 20.5 16C / 32GB+

适合：

- 多业务进程
- 高并发 API
- 大规模压测
- 多 Worker

但仍建议先压测再确定生产 Worker 数。

---

# 21. 跨平台与跨架构说明

## 21.1 Windows

```text
Rust
 ↓
DLL
 ↓
Python ctypes
```

## 21.2 Linux

```text
Rust
 ↓
SO
 ↓
Python / Go / C++
```

## 21.3 macOS

```text
Rust
 ↓
DYLIB
 ↓
Python ctypes / C ABI
```

## 21.4 ARM64

如果目标服务器是 ARM64：

```bash
uname -m
```

确认：

```text
aarch64
```

然后在该架构机器上编译并测试。

不要拿 x86_64 `.so` 直接复制到 ARM64 服务器。

---

# 22. 安全要求

必须遵守：

1. 生产密钥不提交 Git。
2. 生产密钥不要硬编码进 Python/Rust 源码。
3. 优先使用 KMS/Vault/Secret Manager。
4. Token 只通过 HTTPS 传输。
5. Unknown KID 必须拒绝。
6. AES-GCM Tag 校验失败必须拒绝。
7. 过期 Token 必须拒绝。
8. 非法 Payload 必须拒绝。
9. FFI 错误必须 fail closed。
10. 管理接口必须认证授权。
11. 生产日志不得泄露密钥。
12. Replay 必须由业务层处理。
13. Conversion 幂等必须由 Redis/数据库等可靠机制保证。
14. 多进程必须统一密钥版本。
15. 发布前必须进行依赖安全检查。

---

# 23. 发布前检查

正式发布前执行：

```bash
cargo fmt --all -- --check
cargo test
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release
cargo audit
python -m pytest -q
```

然后检查动态库：

Linux：

```bash
file target/release/libad_tracking_engine.so
sha256sum target/release/libad_tracking_engine.so
```

Windows PowerShell：

```powershell
Get-FileHash target\release\ad_tracking_engine.dll -Algorithm SHA256
```

macOS：

```bash
file target/release/libad_tracking_engine.dylib
shasum -a 256 target/release/libad_tracking_engine.dylib
```

发布包必须记录：

- Engine 版本
- Git Commit
- Rust 版本
- Target
- 构建时间
- 动态库 SHA-256
- 测试结果

---

# 24. 升级与回滚

## 24.1 升级

推荐：

```text
旧版本
  ↓
新版本编译
  ↓
完整测试
  ↓
灰度部署
  ↓
监控
  ↓
逐步扩大流量
```

## 24.2 回滚

必须保留上一版本动态库和完整构建信息。

不要只保存：

```text
old.so
```

还应保存：

```text
版本号
Git Commit
Cargo.lock
Rust 版本
Target
SHA-256
配置
```

密钥轮换时尤其要注意：旧版本必须继续能够验证仍在有效期内的旧 Token。

---

# 25. 常见问题

## 25.1 `cargo: command not found`

说明 Rust/Cargo 没有安装或 PATH 未生效。

Linux/macOS：

```bash
source "$HOME/.cargo/env"
cargo --version
```

如果仍然不存在，重新安装 rustup。

## 25.2 `python: command not found`

Linux 可能使用：

```bash
python3 --version
```

然后使用：

```bash
python3 -m venv .venv
```

## 25.3 Python 找不到 native library

先确认 Rust 已经编译：

```bash
find target -type f \( -name '*.so' -o -name '*.dll' -o -name '*.dylib' \)
```

然后确认当前操作系统和 CPU 架构与动态库一致。

## 25.4 Linux 报 linker 错误

重新安装：

```bash
sudo apt install -y build-essential pkg-config
```

## 25.5 Windows 报 MSVC 错误

检查 Visual Studio Build Tools 是否安装：

```text
Desktop development with C++
MSVC
Windows SDK
```

## 25.6 Token 验证失败

依次检查：

1. KID 是否存在。
2. 当前进程是否加载正确 Key。
3. Token 是否被修改。
4. Token 是否已经过期。
5. `exp` 是否大于 `iat`。
6. Token 是否超过最大年龄。
7. Payload 是否违反严格 Schema。
8. 是否在不同进程之间错误地使用了不同 Key。

## 25.7 多台服务器互相不能验证 Token

最常见原因是不同实例没有加载相同的 KID/Key。

解决方案：

```text
KMS / Vault / Secret Manager
          ↓
所有实例统一加载
          ↓
相同 Key Set
```

## 25.8 Redis 挂了怎么办

Engine 本身不能替业务系统决定 Replay 策略。

必须明确：

```text
Redis 不可用
   ↓
拒绝 / 降级 / 隔离
```

对于涉及资金、扣量、Conversion 的场景，不应该静默把“无法检查 Replay”当成“安全”。

---

# 26. 全新 Ubuntu 服务器快速清单

如果你拿到一台全新的 Ubuntu 22.04/24.04 服务器，可以按下面顺序执行。

## 第一步：系统

```bash
sudo apt update
sudo apt upgrade -y
```

## 第二步：基础工具

```bash
sudo apt install -y build-essential curl git pkg-config ca-certificates python3 python3-pip python3-venv
```

## 第三步：Rust

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustc --version
cargo --version
```

## 第四步：进入项目

```bash
cd /opt/ad_tracking_engine
```

## 第五步：Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r python/requirements.txt
```

## 第六步：生成锁文件（第一次没有 Cargo.lock 时）

```bash
cargo generate-lockfile
```

## 第七步：编译

```bash
cargo build --release --locked
```

## 第八步：Rust 测试

```bash
cargo test
```

## 第九步：Python 测试

```bash
python -m pytest -q
```

## 第十步：硬件检测

```bash
python -c "from python.tracking import hardware_profile; import pprint; pprint.pp(hardware_profile())"
```

## 第十一步：压力测试

```bash
python tests/stress_test.py
```

## 第十二步：发布前检查

```bash
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release --locked
```

---

# 27. 最终架构建议

如果这个 Engine 最终用于正式的广告/CPL 平台，建议采用：

```text
                         Internet
                            │
                            ▼
                    Nginx / Gateway
                            │
                    HTTPS / Rate Limit
                            │
                            ▼
                  ┌───────────────────┐
                  │ Go / FastAPI API  │
                  └─────────┬─────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
           Redis        PostgreSQL      Rust Engine
              │             │             │
              │             │             │
         Replay/Cache   Conversion     AES-GCM
              │         Idempotency    Token Verify
              │             │             │
              └─────────────┼─────────────┘
                            │
                            ▼
                    KMS / Vault / Secret
```

核心原则：

> **Rust Engine 负责“可信 Token”，业务系统负责“可信事件”。**

也就是说：

```text
Token 合法
    ≠
Conversion 一定合法

AES-GCM 验证成功
    ≠
请求不能 Replay

click_id 合法
    ≠
广告一定应该扣量
```

因此，生产级广告平台必须把：

- 加密
- 身份
- Replay
- Conversion 幂等
- 计费
- 扣量
- 结算
- 权限
- 审计

分层实现。

---

## 版本信息

```text
Engine Version: 1.1.0
ABI Version:    1
Python:         3.10+
Rust:           stable
```

## 文档状态

本文档专门面向：

- 全新电脑
- 全新服务器
- 开发环境初始化
- 测试环境部署
- 生产环境部署
- 跨平台编译
- 硬件适配
- 安全发布

如果遇到编译、动态库加载或测试问题，建议严格按照“系统 → 基础依赖 → Rust → Python → 编译 → 测试 → 压测”的顺序排查，不要跳步骤。


## Windows DLL 文件信息（作者 / 公司 / 联系方式）

Windows 发布版 DLL 已预留 Version Resource。

主要配置文件：

```text
windows/version.rc
```

修改公司、联系方式、版权、产品名称等信息：

```c
#define VER_COMPANYNAME_STR      "你的公司名称"
#define VER_LEGALCOPYRIGHT_STR   "版权所有 © 2026 你的公司名称"
#define VER_CONTACT_STR          "support@example.com"
#define VER_PRODUCTNAME_STR      "Ad Tracking Engine"
#define VER_FILEDESCRIPTION_STR  "广告追踪安全引擎"
```

作者信息建议写入 `Comments`：

```c
#define VER_COMMENTS_STR "作者：你的姓名；团队：你的团队"
```

完整修改方法请阅读：

```text
DLL_METADATA_CN.md
```

版本号主要修改：

```text
Cargo.toml
windows/version.rc
```

Windows DLL 构建环境需要 Visual Studio Build Tools + Windows SDK，以提供 `rc.exe` 等工具。

> 注意：本项目源码可以生成 Rust `cdylib`，但 Windows Version Resource 是否最终链接进 DLL，取决于具体 MSVC 构建链。发布前必须在 Windows 目标环境实际检查 DLL 的“属性 → 详细信息”。


## Windows 编译报错：`expected Result<i64, i32>, found Result<Result<i64, i32>, i32>`

如果执行：

```powershell
cargo build --release
```

出现：

```text
error[E0308]: mismatched types
expected Result<i64, i32>
found Result<Result<i64, i32>, i32>
```

请确认 `src/lib.rs` 中的 `now_unix()` 已使用当前版本实现。

原因是旧实现对 `Result` 做了两层包装，导致返回类型变成：

```text
Result<Result<i64, i32>, i32>
```

而函数声明要求：

```text
Result<i64, i32>
```

当前版本已经修复为先处理 `duration_since()`，再转换 `u64 -> i64`：

```rust
fn now_unix() -> Result<i64, i32> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| ERR_INTERNAL)?;

    i64::try_from(duration.as_secs()).map_err(|_| ERR_INTERNAL)
}
```

如果你是从旧 ZIP 升级，请不要只替换 DLL；应替换整个项目源码后重新：

```powershell
cargo clean
cargo build --release
```

然后再执行完整测试。



# 28. 商业发布版 DLL 加固

本项目的 Release 配置已经加入：

- `opt-level = 3`
- `lto = "fat"`
- `codegen-units = 1`
- `panic = "abort"`
- `strip = "symbols"`
- `debug = false`
- `incremental = false`
- Windows MSVC `DEBUG:NONE`
- Windows `/OPT:REF`
- Windows `/OPT:ICF`
- Windows `/DYNAMICBASE`
- Windows `/NXCOMPAT`

并在密钥替换、删除以及 Engine 状态销毁时主动清零密钥数组。

## 28.1 Windows 正式构建

在 Visual Studio Developer Command Prompt / x64 Native Tools Command Prompt 中：

```bat
build_windows.bat
```

成功后：

```text
bin/ad_tracking_engine.dll
bin/ad_tracking_engine.dll.sha256
```

## 28.2 正式交付禁止包含

```text
*.pdb
target/debug/
*.rlib
*.rmeta
.env
私钥
生产 API Secret
测试密钥
```

## 28.3 发布检查

PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\release_check.ps1
```

## 28.4 重要安全边界

Rust Release 加固、去符号、LTO、字符串控制和完整性检查只能提高逆向成本，不能保证 DLL “无法反编译”。

任何运行在客户机器上的秘密理论上都可能被提取。

因此：

1. 长期生产密钥不要硬编码进 DLL。
2. License 使用服务器私钥签名，DLL 只内置公钥。
3. 核心归因、扣量、结算、防刷、Replay 等业务规则放服务端。
4. DLL 负责客户端必须完成的本地计算与协议封装。
5. 生产 DLL 不输出敏感 Token、密钥或完整请求的 Debug 日志。

## 28.5 密钥生命周期

DLL 内的 Key 仍然只是运行时秘密，不应当被当作永久安全边界。

当前版本会在：

- 替换旧 KID Key 时
- 删除 KID Key 时
- DLL 全局状态销毁时

主动清零保存的 `[u8; 32]` 密钥数组。

但是 `current_key()` / `key_for()` 为加密操作复制出的短生命周期栈变量也只存在于当前操作期间，并在使用后显式清零。

## 28.6 逆向现实

Release 加固可以降低：

- 符号可读性
- 调试信息暴露
- 简单字符串搜索价值
- 未优化代码结构可读性

但不能阻止：

- 反汇编
- 动态调试
- API Hook
- 内存分析
- Patch
- 行为分析

商业核心算法和服务器私钥必须保持在服务端。

