# 摄像头人脸迎宾 — 优化结果（第二轮）

> 基线见 `CAMERA_OPTIMIZATION_BASELINE.md`。日期：2026-10-26。  
> 真实海康端到端延迟：**未测**，不得外推为门店 SLA。

## 1. 最主要瓶颈（优化前）

1. **InsightFace CPU 检测/提特征**：空跑约 360–530 ms/帧（本机）；端到端主耗时通常在此，而非匹配。  
2. **热集匹配实现**：循环 + 每次 `cosine_similarity` 重算 L2；N=2000 时约 **14 ms**（相对检测仍小，但可白捡且利于扩库）。  
3. **`best_match` 持 asyncio 锁做冷库 DB 扫描**：多摄像头识别串行化。  
4. **可观测缺口**：识别阶段无稳定 `detect_extract_ms`/`match_ms`，难定位「慢在认还是播」。  
5. **DEGRADED 跳过识别**：坏帧状态导致整路停检（正确性回归）。

## 2. 已确认缺陷与修复

| ID | 类型 | 处理 |
|---|---|---|
| O1 | 正确性 | `ONLINE`/`DEGRADED` 均可识别 |
| O2 | 准确率一致性 | `_decrypt_vec` 强制 L2，与 live/enroll 对齐 |
| O3 | 性能 | 热集预归一化矩阵点积匹配 |
| O4 | 并发 | 冷匹配释放全局锁后再查库 |
| O5 | 可观测 | `face_pipeline_metrics` → API runtime → 前端「识别耗时」列 |

**未引入新依赖。未更换 InsightFace 模型。未放宽 `face_match_threshold`。**

## 3. 修改文件

- `backend/app/services/face_service.py`（含 Redis `speak_generation`）
- `backend/app/core/cache_keys.py`（`speak_generation_key`）
- `backend/app/workers/tts_worker.py`（播报中轮询 Redis 世代）
- `backend/app/services/face_extractor.py`（`light` 收集期路径）
- `backend/app/services/face_pipeline_metrics.py`（新）
- `backend/app/workers/recognition_worker.py`（light 收集 + 定稿 refine）
- `backend/app/api/cameras.py`
- `frontend/src/views/Cameras.vue`
- `frontend/src/utils/labels.ts`（DEGRADED）
- `tests/测试_人脸热集矩阵匹配.py`（新）
- `tests/测试_迎宾播报时延.py`（speak_gen Redis / light 签名）
- `docs/CAMERA_OPTIMIZATION_*.md`

## 4. 优化前后实测（本机微基准）

### 4.1 热集匹配（同机、同随机种子）

| N | 优化前风格（循环+重归一化）ms | 优化后矩阵 ms | 加速比 |
|---|---|---|---|
| 100 | 0.790 | 0.012 | ~65× |
| 500 | 3.449 | 0.043 | ~81× |
| 2000 | 13.951 | 0.255 | ~55× |

说明：相对整帧检测（数百 ms），匹配节省在小库上体感有限；库规模上升或冷路径并发时收益更明显。

### 4.2 检测 / 模型

| 指标 | 优化前基线 | 优化后 |
|---|---|---|
| model_load_ms | 3042 | 未改模型，预期同量级 |
| detect_extract_ms（噪声帧） | ~360–530 | 收集期 light（720、无 CLAHE）预期略降；定稿仍完整 960；**未做新一轮空跑计时** |

### 4.3 准确率

| 项 | 结果 |
|---|---|
| 有标签 FMR/FNMR | **本轮无数据集，不报告虚假百分比** |
| 预期影响 | L2 统一降低「注册/识别空间不一致」漏识；矩阵与逐条 cosine 单测一致 |
| 样本量 | 矩阵一致性：合成向量单元测试；非真人标签集 |

### 4.4 自动化回归

```
pytest tests/测试_人脸热集矩阵匹配.py tests/测试_摄像头迎宾修复.py
       tests/测试_陌生访客迎宾与清空.py tests/测试_海康摄像头对讲TTS.py
       tests/测试_人脸最佳帧窗口.py tests/测试_迎宾播报时延.py
       tests/测试_摄像头Leader守卫.py
→ 50 passed（含 speak_gen Redis / light / face_side_px / downscale 用例）
```

## 5. 资源与依赖

- CPU：匹配路径开销下降；检测仍占主导。  
- 内存：热集额外一份 `(N,512) float32` 矩阵（N=800 ≈ 1.6MB），可接受。  
- 新增依赖：**无**。

## 6. 未解决 / 待现场

- 真实 RTSP / ISAPI / 端到端「音频发送完成」P50/P95。  
- GPU（CUDA）本机不可用；勿宣称 GPU 加速。  
- 固定真人标签回归集与阈值重标定。  
- 多摄长时内存曲线。  
- TTS/对讲：本轮补 Redis `speak_generation` 跨进程打断；codec/改密仍依赖上轮修复。

## 7. 本轮续作（已落地）

1. **Redis `speak_generation`**：`INCR`/`GET` + 本地缓存；会员 `enqueue` 走 `bump_speak_generation_async`；陌生播报中 0.2s 轮询 Redis 供同步 abort。  
2. **收集期 light 检测**：`max_side=720`、跳过 CLAHE；定稿对 `best_frame` 再 `light=False` 完整提特征后匹配。  
3. **人脸像素可观测**：`face_side_px`（源分辨率短边）写入 runtime；列表显示「脸 Npx」；定稿 `refine_extract_ms` 与收集 `detect_extract_ms` 分离；`probe-face` 返回像素/耗时/偏小提示（**不**据此宣称准确率提升）。

## 8. 后续建议（仍未实施）

1. 用现场 `face_side_px` 样例决定是否改子码流/安装距离（本轮只提供观测，未改默认分辨率）。  
2. 标签集驱动阈值曲线；禁止再靠直觉拧 `threshold+0.04`。  
3. 收集期 light 路径的真人准确率对比（需标签视频，勿臆造 %）。  
4. 真实海康端到端 P50/P95。
