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

## G1–G6 both 试听打断 / 假入队 / 整理回退 / 状态回退（续）

- **G1**：`test_speak`(both) 入队 bump `speak_gen`，TTS 挂 `should_abort`（与摄像头试听一致）。
- **G2**：`POST test-speak` 的 `queued` 以是否有 `task_id` 为准；前端未入队改 warning。
- **G3**：会员队列整理异常回退前，尽力 `LREM` 同机位 `unknown_visitor`。
- **G4**：`speak_task` 进程内 TTL 缓存，Redis 读写失败时仍可轮询。
- **G5**：`play_ulaw`/`play_wav` 返回与 `play_g711` 同结构的 dict。
- **G6**：TTS 消费到空文本写 `cancelled/empty_text`，不留永久 queued。

## H1–H3 会员打断 / 识别 monotonic / 采集锁（续）

- **H1**：TTS 对 `member` 同样挂 `speak_gen` abort，新会员/试听可打断上一句长播报。
- **H2**：`RecognitionWorker` 的 yield/抑制/近阈值/采样节拍改 `time.monotonic()`。
- **H3**：`CameraManager` 用 `threading.RLock` 串行化 start/stop/list/get，防止并发双开。

## I1–I3 多帧 TTL / 会员抢占试听（续）

- **I1**：`MultiFrameTracker` 确认窗 TTL 改 `monotonic`。
- **I2**：会员入队统一 `_pending_drop_reason`：抢占陌生/摄像头试听/同机位旧会员 pending。
- **I3**：陌生迎宾遇同机位待播 `test_speak`/`test_speak_camera` 时写 `test_speak_pending` 并跳过（并释放冷却）。

## J1–J3 stop 超时 / 访客轨迹 / DEGRADED 试听（续）

- **J1**：`CameraWorker.stop` join 超时覆盖 RTSP open 预算；`_connect` 在 open 前后见 `_stop` 即释放并返回 False，降低重启双开同路风险。
- **J2**：陌生访客进程内轨迹 TTL 与内存冷却改 `monotonic`（Redis NX 冷却仍用服务端墙钟 TTL）。
- **J3**：后台试听 UI 将 `DEGRADED` 与 `ONLINE` 同等视为可对讲；probe 过期提示同步提及 DEGRADED。

## K1–K3 overview 降级 / last_online / speak_task 本地 TTL（续）

- **K1**：`GET /cameras/overview` 将 `DEGRADED` 计入 `online`，并返回 `degraded`；前端 KPI 有降级时显示「其中降级」。
- **K2**：启动/创建后 `last_online_at` 在 `ONLINE` 与 `DEGRADED` 时均更新。
- **K3**：进程内 `speak_task` 用 `updated_mono` 做 TTL；对外响应仍只暴露墙钟 `updated_at`。

## L1–L2 入队后抑制 / DEGRADED 标签色（续）

- **L1**：`recognition_worker` 会员/陌生迎宾入队成功后，即使 `db.commit` 失败仍写 `_tts_yield_until` / `_suppress_unknown_until`（TTS 已入队会播，勿再叠「欢迎光临」）。
- **L2**：后台 `cameraStatusTagType` 将 `DEGRADED` 标为 `warning`，与离线 `info` 区分。

## M1–M3 edge TTS / 停采打断 / overview 假在线（续）

- **M1**：Leader 对 `TTS_PROVIDER=edge` 与 `local` 均启动 `TTSWorker`（原先仅 local，生产 edge 队列永不消费）。
- **M2**：`stop`/`delete` bump `speak_gen`；TTS 出队后若 Leader 上无该机位 worker 则 `cancelled/camera_stopped`。
- **M3**：overview 在 Leader 且无 worker 时不把库内 `ONLINE/DEGRADED/RECONNECTING` 计入在线（非 Leader 仍信库）。

## N1–N3 列表假在线 / both 试听 / poll（续）

- **N1**：`_serialize` 与 overview 同：Leader 无 worker 时列表/详情不展示库内 ONLINE/DEGRADED。
- **N2**：`camera_stopped` 取消仅限 member/unknown_visitor/test_speak_camera；`test_speak`(both) 仍可叠本机。
- **N3**：后台试听 `both` 与 `camera` 一样轮询 speak-task。

## O1–O3 DEGRADED 契约 / KPI 一致 / Leader 启采（续）

- **O1**：坏帧且近期好帧≤5s→`DEGRADED`；无近期好帧→`RECONNECTING`（不再把不可识别态标成 DEGRADED 虚增 usable）。
- **O2**：overview 与 `_serialize` 均以 `get_runtime().running`（线程存活）为准。
- **O3**：`_start_worker` 捕获 `CameraNotLeaderError` → `AppException(40922, status=409)`。

## P1–P4 试听抢占 / bump 时机 / 创建兜底 / 死线程（续）

- **P1**：摄像头试听 kind 对同机位 `unknown_visitor` → `test_speak_preempt`。
- **P2**：`enqueue` 仅在 Redis/内存入队成功后 `_bump_speak_gen_after_enqueue`。
- **P3**：`create_camera(auto_start)` 遇 40922 保留 OFFLINE + `start_note=not_leader`。
- **P4**：`get_status`/`get_runtime`/`get_worker`/`list_camera_ids` 读前 `_cleanup_stale_workers`。

## Q1–Q3 force bump / update 重建 / 合成 abort（续）

- **Q1**：`start`/`update(force)` 与 stop/delete 同走 `_bump_speak_gen_safe`。
- **Q2**：`update` 需重建时不依赖本地 `get_worker`；非 Leader → `restart_note=not_leader`。
- **Q3**：`_play_on_camera` 在 codec/合成前后查 `should_abort`。

## R1–R4 入队幂等 / bump 序 / TOCTOU / mono 过期（续）

- **R1**：Redis `RPUSH` 成功后 status/bump 失败只记日志，禁止再 `RPUSH`/内存回填（`_finalize_redis_enqueue` + `redis_pushed`）。
- **R2**：`start`/`update(force)` **先** `_bump_speak_gen_safe` **再** `_start_worker`，避免新线程已起时 bump 被 gen0 吸收。
- **R3**：`unknown_visitor` 入队后复检同机位 member/试听 pending，命中则 `LREM` 自身。
- **R4**：队列项写 `enqueued_mono`；`is_stale_item` 优先 monotonic。

## S1–S5 内存 TOCTOU / open-hold abort / create bump / 停采 abort / UI note（续）

- **S1**：内存降级陌生入队后同样 TOCTOU 复检（与 Redis R3 对齐）。
- **S2**：`play_g711` 在 open 后与尾 hold 分段查 `should_abort`，取消尽快 `/close`。
- **S3**：`create(auto_start)` 与 start 对称：先 bump 再 force 启采。
- **S4**：TTS abort 在 member/unknown/test_speak_camera 上：Leader 无 worker 视同打断。
- **S5**：后台创建/更新 toast 识别 `start_note`/`restart_note=not_leader`。

## T1–T3 非 Leader 停删 / 试听 keep-latest / cancel reason（续）

- **T1**：`stop`/`delete` 经 `_require_leader_for_capture_control`；非 Leader → AppException 40922，禁止写库假 OFFLINE。
- **T2**：`_finalize_redis_enqueue` 对试听/会员入队后再扫同机位，按 `_pending_drop_reason` LREM 旧项（并发双 RPUSH 兜底）。
- **T3**：TTS abort 经 `abort_meta.reason` 区分 `speak_gen_bumped` 与 `camera_stopped`。

## U1–U4 停采序 / 试听族 / 内存 keep-latest / finalize 重试（续）

- **U1**：`stop`/`delete` 先 bump 再 `asyncio.to_thread(stop)`；Manager 锁内仅 pop+signal，join 在锁外；force 启采先锁外 join 旧线程再启新。
- **U2**：`test_speak` 与 `test_speak_camera` 同机位互 `replaced`（`test_speak_pc` 不互抢）。
- **U3**：内存入队 put 后 keep-latest，对齐 Redis finalize。
- **U4**：普通 RPUSH 外层异常时再试 `_finalize_redis_enqueue`，禁止裸 return。

## V1–V4 内存 dequeue / 启采 to_thread / 合成中 abort / local_only（续）

- **V1**：`dequeue` 在 Redis BLPOP 空时仍 drain 内存 backlog（Redis 恢复后不饿死故障窗口入队）。
- **V2**：`start`/`update`/`create`/MJPEG 启采均 `asyncio.to_thread(_start_worker)`，对齐停采不堵事件循环。
- **V3**：`_play_on_camera` 对 codec/合成用 `_await_with_abort` 短周期轮询。
- **V4**：`speak_outcome=local` → `device_playback=local_only`；前端区分本机回退提示。

## 其它

- `cameras.probe-face`：过期帧返回 `reason=stale_frame`。
- `recognition_worker`：帧龄改读 `age_sec()`。
- 上轮 A1–A8、Redis speak_generation、light 检测、face_side_px 仍有效。
