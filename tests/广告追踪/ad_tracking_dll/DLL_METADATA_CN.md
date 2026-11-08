> **文档维护说明（2026-11-08）**：Version Resource 的公司、产品、版本和版权字段必须与真实发布主体一致；不得填入虚构的法律声明或个人联系方式。发布后对 DLL 运行文件属性检查并生成 SHA-256 清单。 具体发布事实以当前 Cargo manifest/lock、CI、构建产物和签名校验为准；本机 Windows 结果不代表 Linux/macOS 结果。不得记录真实密钥或生产 token。
>
> ---
>
# DLL 文件详细信息配置说明

## 一、Windows DLL 可以显示哪些信息？

在 Windows 中右键：

`ad_tracking_engine.dll → 属性 → 详细信息`

可以显示：

- 文件说明
- 文件版本
- 产品名称
- 产品版本
- 公司
- 作者/开发者说明
- 版权所有
- 原始文件名
- 内部名称
- 注释
- 联系方式

本项目已经加入 Windows Version Resource 配置文件。

---

## 二、最重要：在哪里修改？

### 1. Windows DLL 信息

文件：

```text
windows/version.rc
```

这是**最主要的修改文件**。

例如：

```c
#define VER_COMPANYNAME_STR         "请修改为公司名称"
#define VER_LEGALCOPYRIGHT_STR      "请修改为版权所有信息"
#define VER_CONTACT_STR             "请修改为联系方式"
```

把它修改成自己的真实信息。

例如：

```c
#define VER_COMPANYNAME_STR         "某某科技有限公司"
#define VER_LEGALCOPYRIGHT_STR      "Copyright (C) 2026 某某科技有限公司"
#define VER_CONTACT_STR             "support@example.com"
```

---

## 三、产品名称在哪里修改？

同样修改：

```text
windows/version.rc
```

例如：

```c
#define VER_PRODUCTNAME_STR         "Ad Tracking Engine"
#define VER_FILEDESCRIPTION_STR     "广告追踪安全引擎"
```

如果需要中文产品名称：

```c
#define VER_PRODUCTNAME_STR         "广告追踪安全引擎"
#define VER_FILEDESCRIPTION_STR     "高性能广告追踪安全引擎"
```

---

## 四、版本号在哪里修改？

项目主版本：

```text
Cargo.toml
```

例如：

```toml
version = "1.1.0"
```

Windows DLL 文件版本：

```text
windows/version.rc
```

例如：

```c
#define VER_FILEVERSION             1,1,0,0
#define VER_FILEVERSION_STR         "1.1.0.0\0"
#define VER_PRODUCTVERSION          1,1,0,0
#define VER_PRODUCTVERSION_STR      "1.1.0\0"
```

建议两者保持一致。

---

## 五、作者信息在哪里修改？

当前 Windows DLL 标准 Version Resource 没有一个所有 Windows 版本都统一显示的“Author”字段。

因此推荐：

### CompanyName

```text
windows/version.rc
```

修改：

```c
#define VER_COMPANYNAME_STR "你的公司名称"
```

### Comments

```c
#define VER_COMMENTS_STR "Rust 高性能广告追踪安全引擎；作者：你的姓名"
```

如果希望明确记录作者，可以写：

```c
#define VER_COMMENTS_STR "作者：张三；团队：广告技术团队"
```

---

## 六、联系方式在哪里修改？

修改：

```text
windows/version.rc
```

例如：

```c
#define VER_CONTACT_STR "support@example.com"
```

也可以：

```c
#define VER_CONTACT_STR "support@example.com | https://example.com"
```

---

## 七、版权信息在哪里修改？

修改：

```text
windows/version.rc
```

例如：

```c
#define VER_LEGALCOPYRIGHT_STR "Copyright (C) 2026 某某科技有限公司"
```

中文也可以：

```c
#define VER_LEGALCOPYRIGHT_STR "版权所有 © 2026 某某科技有限公司"
```

---

## 八、原始 DLL 文件名

修改：

```c
#define VER_ORIGINALFILENAME_STR "ad_tracking_engine.dll"
```

如果最终 DLL 名称发生变化，这里也必须同步修改。

---

## 九、完整推荐配置示例

例如你的最终信息是：

```text
公司：某某科技有限公司
作者：张三
产品：广告追踪安全引擎
邮箱：support@example.com
官网：https://example.com
版权：© 2026 某某科技有限公司
```

那么：

```c
#define VER_COMPANYNAME_STR         "某某科技有限公司"
#define VER_FILEDESCRIPTION_STR     "广告追踪安全引擎"
#define VER_INTERNALNAME_STR        "ad_tracking_engine"
#define VER_LEGALCOPYRIGHT_STR      "版权所有 © 2026 某某科技有限公司"
#define VER_ORIGINALFILENAME_STR    "ad_tracking_engine.dll"
#define VER_PRODUCTNAME_STR         "Ad Tracking Engine"
#define VER_COMMENTS_STR            "作者：张三；Rust 高性能广告追踪安全引擎"
#define VER_CONTACT_STR             "support@example.com | https://example.com"
```

---

## 十、修改以后怎么重新生成 DLL？

全新 Windows 电脑首先安装：

1. Rust
2. Visual Studio Build Tools
3. Windows SDK

然后打开：

`x64 Native Tools Command Prompt for VS`

进入项目目录：

```bat
cd /d E:\ad_tracking_engine
```

执行：

```bat
build_windows.bat
```

或者：

```bat
cargo build --release
```

---

## 十一、非常重要：不要只修改 DLL 属性

推荐统一修改以下位置：

| 信息 | 修改文件 |
|---|---|
| 公司 | `windows/version.rc` |
| 作者 | `windows/version.rc` |
| 联系方式 | `windows/version.rc` |
| 官网 | `windows/version.rc` |
| 版权所有 | `windows/version.rc` |
| 产品名称 | `windows/version.rc` |
| DLL 文件描述 | `windows/version.rc` |
| Windows DLL 文件版本 | `windows/version.rc` |
| Rust 项目版本 | `Cargo.toml` |
| 项目版本显示 | `src/lib.rs` |
| README 产品信息 | `README.md` |
| 更新日志 | `CHANGELOG.md` |
| 生产检查 | `deploy/production_checklist.md` |

---

## 十二、发布前检查

重新编译 DLL 后，在 Windows 中：

1. 找到 DLL
2. 右键
3. 属性
4. 详细信息

检查：

```text
文件说明
文件版本
产品名称
产品版本
公司
版权所有
原始文件名
内部名称
注释
联系方式
```

确认没有出现：

```text
请修改为公司名称
请修改为版权所有信息
请修改为联系方式
```

再发布生产版本。

---

## 十三、注意

`windows/version.rc` 是 Windows 文件版本资源。

它不会把信息作为业务数据写入广告 Token，也不会改变 AES-GCM 加密协议。

它只是给 Windows PE/DLL 文件增加产品身份和版本元数据。

Linux `.so` 和 macOS `.dylib` 没有完全等同于 Windows“属性 → 详细信息”的统一机制，因此应继续通过：

- `Cargo.toml`
- 项目版本 API
- README
- CHANGELOG
- 构建产物命名
- 发布清单

维护跨平台产品信息。
