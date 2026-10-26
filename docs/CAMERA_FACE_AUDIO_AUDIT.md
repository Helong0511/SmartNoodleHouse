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
| D1 | P0 | audioData 非 100/200 | 302 等中间态仍推流 | 假成功 | **已修**：非白名单一律拒绝 | `test_stream_audio_rejects_http_redirect` | 否 |
| D2 | P1 | 队尾替换旧 task | 停在 queued | 前端轮询卡死 | **已修**：写 cancelled/replaced\|member_preempt | 多帧播报单测 | 否 |
| D3 | P1 | 试听不 abort / 不 bump | 打断不了 in-flight | 连点/会员无效 | **已修**：test_speak_camera bump+abort | 逻辑单测+既有 TTS | 建议 |
| D4 | P1 | 前端 stale_frame / poll | 误报无人脸；超时静默 | 运维误判 | **已修**：分支提示 + reason/超时 | 手工 UI | 否 |
| D5 | P2 | 多脸只按面积 | 大框弱脸压过 | 错匹配风险 | **已修**：面积×det_score | 择优单测 | 现场 |
| E1 | P0 | speak_gen Redis TTL 后 INCR 从 1 重计 | 打断令牌回落，会员无法打断旧陌生句 | **已修**：Lua `max(redis,local)+1` + SET EX | `test_bump_speak_generation_async_uses_redis_lua` | 否 |
| E2 | P1 | 队尾替换用 DELETE 整表 | 抹掉并发入队任务 | **已修**：LREM 精确删 + RPUSH | 多帧播报 replaced 单测 | 否 |
| E3 | P1 | 出队 BRPOP（LIFO） | 旧任务饿死、人走后仍播新句 | **已修**：BLPOP 与 RPUSH 组成 FIFO | dequeue stale 单测 | 否 |
| E4 | P1 | need_taste 只看主模板 | 默认副句 `{口味}` 永不查库 | **已修**：主模板∨偏好模板 | `test_need_taste_covers_preference_template_placeholder` | 否 |
| E5 | P2 | member_pending 仍占陌生冷却 | 无声却冷却占窗 | **已修**：enqueue None 删冷却键 | 陌生迎宾冷却释放单测 | 否 |
| F1 | P0 | `_redis_ensure_speak_gen` 非原子 GET+SET | 与 bump 竞态可把 Redis 世代压低 | **已修**：Lua `max(cur,want)` | `test_redis_ensure_speak_gen_never_lowers` | 否 |
| F2 | P1 | 内存 dequeue 遇 stale 直接 return None | 后项饿死到下次轮询 | **已修**：丢弃后继续取 | `test_memory_dequeue_skips_stale_and_returns_next` | 否 |
| F3 | P1 | 会员 enqueue 失败/满仍占冷却 | 无声占窗；QueueFull 曾返回假成功 task_id | **已修**：QueueFull→None；try_welcome 检查 tid | `test_try_welcome_releases_cooldown_when_enqueue_none` | 否 |
| F4 | P2 | 内存降级无 member_pending/抢占 | Redis 宕机时行为不一致 | **已修**：内存 drain 对齐 Redis 语义 | `test_memory_enqueue_unknown_skipped_when_member_pending` | 否 |
| G1 | P1 | `test_speak`(both) 不 bump/不 abort | 会员或连点无法打断双端试听推流 | **已修**：both 与 camera 同 bump+abort | `test_test_speak_both_bumps_speak_generation` | 否 |
| G2 | P1 | 试听 API 恒 `queued:true` | 入队失败仍提示成功 | **已修**：`queued=bool(task_id)`；前端 warning | API+Cameras.vue | 否 |
| G3 | P1 | 会员队列整理失败后普通入队不抢占 | 「欢迎光临」仍挡会员 | **已修**：回退前尽力 LREM 陌生句 | 逻辑+既有 preempt 单测 | 否 |
| G4 | P2 | Redis 宕机时 speak_task 不可读 | 试听轮询 404 | **已修**：进程内 TTL 缓存回退 | `test_speak_task_local_fallback_when_redis_down` | 否 |
| G5 | P2 | `play_ulaw`/`play_wav` 忽略结构化结果 | 调用方易当成功 | **已修**：返回 dict（含 status） | `test_hikvision_play_ulaw_open_put_close` | 否 |
| G6 | P2 | TTS 空文本直接 continue | task 永久 queued | **已修**：写 cancelled/`empty_text` | 代码路径 | 否 |
| H1 | P1 | 会员播报不挂 speak_gen abort | 下一句会员/试听无法打断长句 | **已修**：member 纳入 `_abortable` | `test_member_speak_is_abortable_by_speak_gen` | 否 |
| H2 | P1 | 识别 yield/抑制用 `time.time()` | 时钟回拨可永久跳过识别 | **已修**：改 `monotonic` | `test_recognition_worker_deadlines_use_monotonic` | 否 |
| H3 | P1 | CameraManager 启停无锁 | 并发 start 可双 Worker | **已修**：`RLock` 串行化 | `test_camera_manager_start_force_replaces_under_lock` | 否 |
| I1 | P1 | MultiFrameTracker TTL 用 `time.time()` | 时钟回拨破坏确认窗 | **已修**：改 `monotonic` | `test_multiframe_ttl_uses_monotonic` | 否 |
| I2 | P1 | 会员入队不抢占待播试听/旧会员 | 试听句挡门迎宾 | **已修**：`_pending_drop_reason` 统一抢占 | `test_enqueue_member_preempts_pending_test_speak` | 否 |
| I3 | P2 | 陌生迎宾无视待播摄像头试听 | 试听中仍插「欢迎光临」 | **已修**：`test_speak_pending` 跳过 | 与 member_pending 同路径 | 否 |
| J1 | P1 | stop `join(5)` < RTSP open 8s | 重启时旧线程仍可能占 VideoCapture | **已修**：join≥open+4s；`_connect` 见 stop 放弃 | `test_stop_join_timeout_covers_rtsp_open_budget` | 否 |
| J2 | P1 | 陌生轨迹 TTL 用 `time.time()` | 时钟回拨永久复用访客号 | **已修**：改 `monotonic` | `test_unknown_tracks_ttl_uses_monotonic` | 否 |
| J3 | P2 | 试听 UI 仅认 ONLINE | DEGRADED 误弹「可能离线」 | **已修**：ONLINE/DEGRADED/has_frame | Cameras.vue | 否 |
| K1 | P1 | overview 只计 ONLINE | DEGRADED 被算离线，KPI 误导 | **已修**：计入 online + 透出 degraded | `test_cameras_overview_counts_degraded_as_usable` | 否 |
| K2 | P2 | start/创建 的 last_online 仅 ONLINE | 降级态不刷新最近在线 | **已修**：ONLINE/DEGRADED 均写 | cameras.py | 否 |
| K3 | P2 | speak_task 本地 TTL 用墙钟 | 时钟回拨永久挂住轮询态 | **已修**：`updated_mono` + monotonic prune | `test_speak_task_local_ttl_uses_monotonic` | 否 |
| L1 | P1 | 入队成功后 commit 失败不写 yield/抑制 | TTS 已播仍可再排陌生「欢迎光临」 | **已修**：ok/welcomed 在 try 外判定再写 | `test_recognition_worker_sets_suppress_even_if_commit_fails` | 否 |
| L2 | P3 | `cameraStatusTagType` 无 DEGRADED | 降级与离线同色 | **已修**：`warning` | labels.ts | 否 |
| M1 | P0 | Leader 仅 `tts_provider==local` 启 TTSWorker | 生产 edge 迎宾队列永不消费、永不出声 | **已修**：edge/local 均启 | `test_leader_starts_tts_worker_for_edge_provider` | 否 |
| M2 | P1 | stop/delete 不 bump speak_gen | 停采后对讲仍推完旧句 | **已修**：bump + 出队见无 worker 则 `camera_stopped` | stop/TTS 单测 | 否 |
| M3 | P2 | overview 无 worker 信库 ONLINE | Leader 重启失败后 KPI 假在线 | **已修**：Leader 无 worker 降为 OFFLINE | `test_cameras_overview_leader_demotes_stale_online_without_worker` | 否 |
| N1 | P1 | `_serialize` 无 worker 仍用库 ONLINE | 列表/详情假在线，与 overview 不一致 | **已修**：Leader 同降 OFFLINE | `test_serialize_leader_demotes_stale_online_without_worker` | 否 |
| N2 | P2 | 停采 cancel 含 `test_speak`(both) | 双端试听整单取消、本机也不播 | **已修**：仅 member/unknown/test_speak_camera | TTS 单测 | 否 |
| N3 | P3 | 双端试听不 poll speak-task | both 无推流结果提示 | **已修**：camera/both 均 poll | Cameras.vue | 否 |

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
pytest tests/测试_迎宾播报时延.py tests/测试_多帧确认去重与播报任务.py
       tests/测试_陌生访客迎宾与清空.py tests/测试_摄像头采集与音频协议.py
       tests/测试_海康摄像头对讲TTS.py tests/测试_人脸冷匹配top1.py
→ 见 ACCEPTANCE 最新计数（F1–F4 核对子集）
```
