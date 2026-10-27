# 摄像头人脸迎宾 — 验收报告

## 自动化 / 模拟（已执行）

| 项 | 结果 | 说明 |
|---|---|---|
| 迎宾相关 pytest 子集 | **V 聚焦 73 passed**（内存 dequeue / 启采 to_thread / 合成中 abort / local_only） | 迎宾时延+采集协议+多帧三文件 |
| 会员抢占待播试听 | **模拟通过** | `test_enqueue_member_preempts_pending_test_speak` |
| MultiFrame TTL monotonic | **模拟通过** | `test_multiframe_ttl_uses_monotonic` |
| 会员播报可 speak_gen 打断 | **模拟通过** | `_abortable` 含 member |
| 识别节拍 monotonic | **模拟通过** | yield/抑制不用 time.time() |
| CameraManager 启停加锁 | **模拟通过** | force 替换仅一 worker |
| both 试听 bump speak_gen | **模拟通过** | `test_test_speak_both_bumps_speak_generation` |
| speak_task Redis 宕机本地回退 | **模拟通过** | `test_speak_task_local_fallback_when_redis_down` |
| speak_gen ensure 不压低世代 | **模拟通过** | Lua `max(cur,want)` |
| 内存 dequeue 跳过 stale 取下一项 | **模拟通过** | Redis 宕机降级路径 |
| 会员 enqueue None 释放冷却 | **模拟通过** | `try_welcome` 回滚 |
| 内存 member_pending | **模拟通过** | 与 Redis 语义对齐 |
| speak_gen TTL 后仍单调 bump | **模拟通过** | Lua `max(redis,local)+1` |
| 队列 FIFO `RPUSH+BLPOP` | **模拟通过** | stale dequeue 走 `blpop` |
| member_pending 不入队 + 释放冷却 | **模拟通过** | enqueue None；陌生冷却键删除 |
| need_taste 含偏好副模板 | **模拟通过** | 默认 `{口味}` 副句纳入 |
| audioData `status=0` / `302` | **模拟通过** | 仅允许 100/200 握手继续推流 |
| 试听连点旧任务 `cancelled/replaced` | **模拟通过** | `LREM` + `test_enqueue_test_speak_marks_replaced_cancelled` |
| 多脸择优 `面积×det_score` | **模拟通过** | `test_pick_face_prefers_area_times_det_score` |
| 过期入队 → speak_task `cancelled` | **模拟通过** | `dequeue` 写 `reason=stale` |
| `CameraSpeakerTTS.speak` 返回 `cancelled` | **模拟通过** | 取消不回退本机扬声器 |
| 冷匹配 A=0.47&lt;B=0.78 | **模拟通过** | `测试_人脸冷匹配top1.py` |
| HTTPS 对讲拒绝 | **模拟通过** | `unsupported_audio_transport` |
| stop 打断重连等待 | **模拟通过** | `_connect` 打桩失败路径 |
| 后台试听轮询 `speak-tasks` | **已接线** | `Cameras.vue` `pollSpeakTask`；仍≠设备出声 ACK |
| 前端 `vite build` | **通过**（约 23s，历史） | 跳过卡住的 `vue-tsc -b`；完整 `npm run build` 仍建议本地再跑 |

## 真实设备（现场）

目标：库内 `cameras.id=1`（`肠旺面_测试摄像头`，`rtsp://192.168.1.189:554/.../101`）。证据：`tests/_out/field_camera_1.json`；脚本 `scripts/现场验收海康迎宾.py`。

| # | 步骤 | 预期 | 实际（2026-10-27） | 通过 |
|---|---|---|---|---|
| 1 | 读设备能力 / 对讲编码 | 真实 codec | **alaw**（ISAPI `resolve_codec` ≈3.7s） | **通过** |
| 2 | RTSP 持续采集 | 能开流读帧 | 注入凭据后开流成功，帧 **2560×1440**（≈2.2s） | **通过** |
| 3 | 会员/试听出声 | 扬声器可闻 | G711 推流 completed；**人耳确认听到「欢迎光临」**（2026-10-27） | **通过** |
| 4 | 陌生客 + 会员打断 | 陌生停、会员播，不回退 PC | 业务进程在线：Redis 入队 `unknown_visitor`→`member`；unknown **cancelled / speak_gen_bumped**；member **completed / ok**，`device_playback=unverified`（非 local_only） | **通过**（业务打断；非真人进出） |
| 5 | HTTP ISAPI（非 HTTPS） | 推流 completed，unverified | `http://192.168.1.189`，play 完成 open_ms≈942 send_ms≈1461 | **通过** |
| 6 | 多人进店 | 不错配姓名 | 未做（画面为办公空景，无多人同时入镜） | 待验证 |
| 7 | 断网恢复 | 退避后恢复 | **软重开** RTSP 两次均成功；拔网线/断电源未测 | 部分（软通过） |
| 8 | 门口 face_side_px | 列表/探测可见 | 漏检诊断帧 `tests/_out/field_miss_cam1.jpg`：办公隔断/工位空景，**无人脸**；`face_side_px` 无法观测 | 部分（有帧无脸） |

说明：项 3 人耳确认「欢迎光临」；项 4 证据 `tests/_out/field_interrupt_cam1.json`。复测推流：`python scripts/现场验收海康迎宾.py --camera-id 1 --play-g711 --heard yes`。

## 验收结论

- **代码正确性（冷匹配、HTTPS 拒绝、取消语义、帧龄）**：自动化模拟通过。
- **真机项 1/2/3/4/5**：**已通过**（3 人耳；4 为在线业务打断链路，非真人进出）。
- **仍待人/场景：** 项 6 多人；项 7 拔线；项 8 门口有人脸（当前镜头为空办公景）。
- **性能提升百分比**：**未宣称**。
