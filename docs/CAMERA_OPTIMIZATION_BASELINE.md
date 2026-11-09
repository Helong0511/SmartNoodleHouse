> **文档维护说明（2026-11-08）**：性能基线是历史数据，重新测试时记录摄像头型号/固件、分辨率、帧率、网络、CPU/GPU、模型版本和样本场景，避免将不同环境数据直接比较。 当前事实以 main 代码、迁移、CI 和真实执行证据为准；历史快照不自动代表当前状态。禁止写入有效密钥、密码或 token。
>
> ---
>
# 摄像头人脸迎宾 — 优化基线（第二轮）

> 场景：本地 Windows 调试机。日期：2026-10-26。  
> 目标设备：海康 DS-2CD3347SWDV3-LT（本轮**无真实摄像头接入**，设备侧指标标为待现场）。  
> 原则：无实测不写提升百分比；「音频发送完成」≠「实际发声」。

## 1. 环境与版本

| 项 | 值 |
|---|---|
| OS | Windows 10.0.26300 |
| Python | 3.11.9 |
| OpenCV | 5.0.0 |
| NumPy | 2.1.3 |
| InsightFace | 1.0.1 / buffalo_l |
| ONNX Runtime providers（可用） | `AzureExecutionProvider`, `CPUExecutionProvider` |
| 实际 Applied providers | **CPUExecutionProvider only**（本机无 CUDA） |
| 模型冷加载 | live FaceAnalysis `det_size=640`, `det_thresh=0.28` |

## 2. 调用链计时点定义

| 阶段 | 起点 | 终点 | 指标名 |
|---|---|---|---|
| 模型冷启动 | `ensure_live_face_app` 开始 | app 就绪 | `model_load_ms` |
| 检测+提特征 | `extract_live_face_hit` 入 | 返回/抛错 | `detect_extract_ms` |
| 热集匹配 | `best_match` 入 | 返回 | `match_ms` |
| 跨帧/窗口 | BestFaceWindow 首命中 | 定稿 | 业务窗口 约 1.8–3.2s（设计值） |
| 迎宾入队 | `try_welcome` 开始 | enqueue 完成 | `enqueue_ms`（已有日志） |
| codec 解析 | `resolve_codec` | 返回 | `codec_ms`（已有） |
| TTS 合成 | synthesize 开始 | G711 就绪 | `synth_ms`（已有） |
| ISAPI open | PUT open 开始 | open 返回 | `open_ms`（已有） |
| 音频发送 | 首块写入 | 全部写完 | `send_ms` / `first_chunk_ms`（已有） |
| 端到端（定义） | 窗口定稿帧 | **音频发送完成** | `e2e_audio_sent_ms`（非实际发声） |

## 3. 本机可复现微基准（无摄像头）

### 3.1 推理与预处理

| 指标 | 实测 | 备注 |
|---|---|---|
| `model_load_ms`（冷） | **3042** | CPU buffalo_l 全套 |
| `detect_extract_ms`（720×1280 噪声帧，无脸，5 次） | P50≈**366**，首帧 **534** | 含 CLAHE 二次检测路径 |
| 长边 1440p→960 缩放（20 次均值） | **4.59 ms** | INTER_AREA |

### 3.2 热集匹配（512D，预归一化库）

当前实现：`FaceService.cosine_similarity` **每次重算 L2**，Python 循环。

| N（热集人数） | 循环+重归一化 ms | 矩阵乘法 ms（预归一化） |
|---|---|---|
| 100 | 0.614 | 0.011 |
| 500 | **3.207** | **0.027** |
| 2000 | **12.857** | **0.249** |

结论：会员库变大后，匹配路径可用矩阵点积显著降低 CPU；本机 N≤100 时非主瓶颈，但锁内冷库扫描才是多摄并发风险。

### 3.3 锁与并发（代码审计基线，非压测数字）

| 问题 | 证据 | 风险 |
|---|---|---|
| `best_match` 在 `async with self._lock` 内 await `_cold_match`（多轮 DB） | `face_service.py` | 多摄像头串行匹配，拉高排队延迟 |
| RecognitionWorker 仅处理 `status==ONLINE` | `recognition_worker.py` | 上轮引入的 `DEGRADED` 会被整路跳过识别 |
| 解密向量未强制 L2 归一化 | `_decrypt_vec` | 与 live `normed_embedding` 空间偶发不一致 → 漏识/误识 |

### 3.4 已有生产日志指标（有设备时可读）

TTS / 海康路径已打点：`codec_ms`、`synth_ms`、`push_ms`、`open_ms`、`first_chunk_ms`、`send_ms`。  
识别路径此前**缺少**稳定的 `detect_extract_ms` / `match_ms` 上报（本轮补齐）。

## 4. 准确率基线

| 项 | 状态 |
|---|---|
| 固定带标签回归集 | **未建立**（仓库无独立 FMR/FNMR 数据集） |
| 当前默认阈值 | `face_match_threshold` 种子 **0.48**；确认帧默认 2 |
| 高相似快通 | `threshold+0.04` 且 `ratio≥0.06`、`det≥0.55`（上轮已收紧） |
| 本轮准确率声明 | **不宣称误认率数字**；仅保证优化不故意放宽阈值 |

## 5. 真实设备基线（待现场）

| 指标 | 优化前 | 备注 |
|---|---|---|
| RTSP 连接耗时 P50/P95 | 待测 | |
| 有效帧年龄 | 待测 | |
| 采集 FPS / 检测 FPS | 待测 | 配置默认 detect_fps=3 |
| 端到端「音频发送完成」 | 待测 | |
| 重复迎宾率 | 待测 | Redis 会员冷却 + 陌生 NX |
| 长时间内存曲线 | 待测 | |

## 6. 本轮优化优先级（按收益/风险）

1. **P0 正确性**：DEGRADED 仍可识别；解密向量统一 L2。  
2. **P1 延迟/吞吐**：热集矩阵匹配；冷匹配不持全局 asyncio 锁。  
3. **P1 可观测**：识别阶段耗时写入 runtime，前端可看。  
4. **不做**：换模型、开伪 GPU、放宽阈值换速度。
