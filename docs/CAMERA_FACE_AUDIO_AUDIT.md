# 摄像头人脸识别与语音迎宾 — 审计报告

> 场景：本地调试 / 生产共用代码路径。审计日期：2026-10-26（深度审计续作）。  
> 目标设备：海康威视 DS-2CD3347SWDV3-LT（能力以现场探测为准，禁止仅凭型号推断）。

## 1. 实际架构与调用链

```
CameraManager (RTSP/TCP, Leader-only)
  → RecognitionWorker (BestFaceWindow + InsightFace live)
  → FaceEmbeddingCache.best_match (AUTHORIZED + MEMBER + ACTIVE)
    → WelcomeService.try_welcome / VisitorService.maybe_welcome_unknown
      → Redis TTS 队列 → TTSWorker
        → CameraSpeakerTTS → HikvisionTwoWayAudio (ISAPI TwoWayAudio G.711)
```

## 2. 问题清单（本轮深度审计）

| ID | 级别 | 位置 | 触发条件 | 影响 | 修复 | 自动化 | 需真机 |
|---|---|---|---|---|---|---|---|
| B1 | P0 | `face_service._cold_match` | 冷库按活跃度排序后首个 ≥threshold 即 return | 弱匹配盖过强匹配，错认/漏认 | **已修**：预算内取 top-1，记 top2 | `测试_人脸冷匹配top1.py` | 否 |
| B2 | P1 | `camera_manager._connect` | open 后再 set 超时 | 连接/读可能长期阻塞 | **已修**：open 前 set + 日志；不假定 set 生效 | 连接日志人工看 | 建议 |
| B3 | P1 | `CameraWorker._run` sleep | stop 时 `time.sleep` 不可打断 | 停止/重启卡顿 | **已修**：`Event.wait` | `测试_摄像头采集与音频协议.py` | 否 |
| B4 | P1 | `FrameBuffer` / probe | 墙上时钟算帧龄 | NTP 跳变误判过期 | **已修**：monotonic `age_sec`；probe `stale_frame` | 帧龄单测 | 否 |
| B5 | P0 | `hikvision_audio.play_g711` | HTTPS 基址 + 明文 TCP | 静默协议错配 | **已修**：拒绝 `unsupported_audio_transport` | 同协议测试 | 否 |
| B6 | P1 | `play_g711` / TTS | 取消也记成功或回退本机 | 打断后误播本机 | **已修**：结构化 status；cancelled 不回退 | TTS 单测 | 否 |
| B7 | P1 | audioData `status==0` | 无完整响应头仍推流 | 虚报握手成功 | **已修**：禁止 status=0 当成功 | `test_stream_audio_rejects_http_status_zero` | 否 |
| A1–A8 | — | 见上轮 | — | — | **已修**（改密/codec/冷却等） | 既有套件 | 部分 |
| C1 | P1 | TTS 无 task_id/状态 | 试听只返回 queued | 无法区分入队与推流结果 | **已修**：task_id + Redis + GET + 前端 poll；过期→cancelled | 多帧播报 / `test_dequeue_marks_stale_task_cancelled` | 否 |
| C2 | P1 | MultiFrame 近重复帧 | 同帧复用凑满确认 | 假确认加速误识 | **已修**：cosine≥0.995 不计进度 | 同上 | 否 |
| C3 | P1 | 活体 action_completed 默认 True | 客户端未断言也过 | 夸大活体 | **已修**：默认 False；可选 claimed_action | 活体单测 | 否 |

## 3. 潜在 / 未修（有原因）

| 项 | 原因 |
|---|---|
| 主/子码流选型 | 需门口真实像素与漏检对比；本轮仅提供 `face_side_px` 观测 |
| 标签集阈值重标定 / FMR·FNMR | 无独立验证集；禁止拧阈值 |
| 设备扬声器真出声 ACK | 固件无可靠完成事件 → 恒为 `device_playback=unverified` |
| HTTPS+TLS 推流（方案 B） | 未实现；当前方案 A 明确拒绝 HTTPS |
| 多人多 track | 仍取最大脸；错配风险待现场 |
| 长时内存/压测 P99 | 本轮未执行长时间压测 |

## 4. 测试基线（本轮）

```
pytest tests/测试_人脸冷匹配top1.py tests/测试_摄像头采集与音频协议.py
       tests/测试_人脸热集矩阵匹配.py tests/测试_摄像头迎宾修复.py
       tests/测试_海康摄像头对讲TTS.py tests/测试_迎宾播报时延.py
       tests/测试_摄像头Leader守卫.py tests/测试_RTSP帧完整性.py
       tests/测试_人脸最佳帧窗口.py tests/测试_陌生访客迎宾与清空.py
→ 60 passed（2026-10-26）
```
