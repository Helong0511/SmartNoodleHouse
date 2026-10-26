# 摄像头人脸迎宾 — 修复记录

> 日期：2026-10-26。无数据库结构变更。

## B1 冷匹配 top-1（P0）

- **文件**：`backend/app/services/face_service.py` `_cold_match`
- **改动**：去掉「首个 ≥threshold 即 return」；在最多 20 批预算内比较全部候选人，返回最高相似度；日志输出 top1/top2/delta。
- **兼容**：返回签名仍为 `(customer_id|None, sim)`。
- **风险**：冷库扫描可能略增（相对旧「早退」）；有批次数上限。

## B2/B3/B4 RTSP 生命周期与帧龄（P1）

- **文件**：`camera/camera_manager.py`
- **改动**：FFMPEG 路径 `VideoCapture()` → set 超时 → `open`；失败再尝试 open 后 set；重连用 `_stop.wait`；`FrameBuffer.age_sec()` 用 monotonic；runtime 增加 backend/坏帧计数。
- **说明**：`.set()==True` 不证明超时一定被 FFMPEG 执行，日志已注明。

## B5/B6/B7 音频协议与播报状态（P0/P1）

- **文件**：`hikvision_audio.py`、`tts_worker.py`
- **改动**：`play_g711` 返回 `{status, device_playback, error?}`；HTTPS 返回 `unsupported_audio_transport`；禁止 `status==0` 当成功；`CameraSpeakerTTS.speak` 返回 `ok|cancelled|failed|local`，取消不回退本机。
- **兼容**：调用方需处理 dict / 字符串返回值（已改 TTS worker）。
- **单测**：`test_stream_audio_rejects_http_status_zero`、`test_camera_speaker_speak_returns_cancelled`。

## C1 播报 task_id 与状态（P1）

- **文件**：`cache_keys.py`、`face_service.py`、`tts_worker.py`、`cameras.py`、`Cameras.vue`
- **改动**：入队生成 `task_id`；Redis `snh:tts:speak_task:{id}`；`GET /cameras/speak-tasks/{task_id}`；试听返回 task_id；过期入队写 `cancelled`/`reason=stale`；前端 `pollSpeakTask` 轮询（仍≠设备出声 ACK）。
- **单测**：`test_dequeue_marks_stale_task_cancelled`。

## C2 多帧近重复不计确认（P1）

- **文件**：`face_liveness.py` `MultiFrameTracker`
- **改动**：与上一帧余弦 ≥0.995 只刷新时间、不增加确认帧数。

## C3 活体挑战默认不信任客户端完成标记（P1）

- **文件**：`face_liveness_service.py`
- **改动**：`action_completed` 默认 False；未完成不消费 token；可选 `claimed_action` 须匹配；文档标明非视觉证明。

## 其它

- `cameras.probe-face`：过期帧返回 `reason=stale_frame`。
- `recognition_worker`：帧龄改读 `age_sec()`。
- 上轮 A1–A8、Redis speak_generation、light 检测、face_side_px 仍有效。
