# 摄像头人脸识别与语音迎宾 — 审计报告

> 场景：本地调试 / 生产共用代码路径。审计日期：2026-10-26。  
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

关键文件：`camera/camera_manager.py`、`backend/app/workers/recognition_worker.py`、`backend/app/services/face_*.py`、`hikvision_audio.py`、`tts_worker.py`、`api/cameras.py`、`frontend/src/views/Cameras.vue`。

## 2. 已确认问题（修复前）与严重度

| ID | 严重度 | 问题 | 状态 |
|---|---|---|---|
| A1 | P0 | 改密后 Digest HTTP Client 未失效，对讲持续 401 | **已修复** |
| A2 | P1 | codec 探测失败仍把默认 alaw 写入 Redis/热缓存 | **已修复** |
| A3 | P1 | 会员冷却抢占后顾客缺失不回滚 | **已修复** |
| A4 | P1 | 陌生访客冷却仅进程内字典 | **已修复**（Redis NX + 降级） |
| A5 | P1 | `threshold+0.04` 无质量门槛可跳过多帧确认 | **已收紧** |
| A6 | P2 | 前端无 probe-face 入口 | **已修复** |
| A7 | P2 | `camera_speaker_tts_enabled` 种子默认 false 与 META 不一致 | **已对齐**（新库） |
| A8 | P2 | 重连无抖动；坏帧仍标 ONLINE | **已改善** |

## 3. 潜在 / 待现场验证

- 真实固件对讲编码、Content-Length:0 推流节奏、播放完成确认（设备无可靠 ACK → 标记 PLAYBACK_UNVERIFIED）。
- 多进程下 `speak_generation` 打断仍为进程内语义。
- 多人同框仅取最大脸；完整多 track 未做。
- 已有库若 `camera_speaker_tts_enabled=false`，需在业务参数中手动打开一次。

## 4. 测试基线（2026-10-26）

```
pytest tests/测试_摄像头迎宾修复.py tests/测试_陌生访客迎宾与清空.py
       tests/测试_海康摄像头对讲TTS.py tests/测试_RTSP帧完整性.py
       tests/测试_摄像头Leader守卫.py
→ 30 passed
```

## 5. 推荐修复顺序（已执行）

1. 凭证缓存失效 → 2. codec 探测诚实化 → 3. 冷却回滚 → 4. 陌生客 Redis 冷却 → 5. 快通质量门槛 → 6. 前端探测人脸。
