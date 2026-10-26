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

## D1–D5 握手白名单 / 任务结案 / 试听打断 / UI / 多脸（续）

- **D1**：`hikvision_audio` audioData 仅允许 HTTP 100/200，302 等一律失败。
- **D2**：入队替换 pending 时写旧 `task_id` → `cancelled`（`replaced` / `member_preempt`）。
- **D3**：`test_speak_camera` 入队 bump `speak_gen`；TTS 对试听挂 `should_abort`，cancelled 带 `reason`。
- **D4**：`Cameras.vue` 区分 `stale_frame`；`pollSpeakTask` 展示 reason 与超时提示。
- **D5**：`_pick_largest_face` 改为面积×det_score。

## E1–E5 打断令牌 / FIFO / 口味模板 / 冷却释放（续）

- **E1**：`bump_speak_generation_async` 用 Lua `max(GET, floor)+1` + `SET EX`，键过期后仍相对本地抬升，避免打断令牌回落。
- **E2**：试听/会员替换 pending 改为 `LREM` 精确删除，避免 `DELETE` 抹掉并发入队。
- **E3**：出队由 `BRPOP` 改为 `BLPOP`，与 `RPUSH` 组成 FIFO，避免旧任务饿死。
- **E4**：`need_taste` / `need_product` 同时检查主模板与偏好/餐品副模板（默认副句含 `{口味}`）。
- **E5**：`enqueue` 因 `member_pending` 返回 `None` 时，陌生迎宾删除 Redis NX 冷却，避免无声占窗。

## F1–F4 ensure 竞态 / 内存队列 / 会员冷却（续）

- **F1**：`_redis_ensure_speak_gen_at_least` 改为 Lua `max(cur,want)`，禁止非原子 SET 压低并发 bump。
- **F2**：内存 `dequeue` 丢弃 stale 后继续取，与 Redis 路径一致。
- **F3**：进程内队列满时 `enqueue` 返回 `None`；`try_welcome` 无 `task_id` 则释放会员冷却。
- **F4**：Redis 不可用时内存入队同样处理 `member_pending` / `replaced` / `member_preempt`。

## 其它

- `cameras.probe-face`：过期帧返回 `reason=stale_frame`。
- `recognition_worker`：帧龄改读 `age_sec()`。
- 上轮 A1–A8、Redis speak_generation、light 检测、face_side_px 仍有效。
