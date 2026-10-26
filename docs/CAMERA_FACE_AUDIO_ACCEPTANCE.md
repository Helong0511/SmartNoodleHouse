# 摄像头人脸迎宾 — 验收报告

## 自动化 / 模拟（已执行）

| 项 | 结果 | 说明 |
|---|---|---|
| 迎宾相关 pytest 子集 | **44 passed**（本轮核对子集） | 采集音频协议 / 迎宾时延 / 冷匹配 / 多帧播报 / 海康 TTS / 活体接线 |
| audioData `status=0` | **模拟通过** | 握手拒绝，禁止当成功 |
| 过期入队 → speak_task `cancelled` | **模拟通过** | `dequeue` 写 `reason=stale` |
| `CameraSpeakerTTS.speak` 返回 `cancelled` | **模拟通过** | 取消不回退本机扬声器 |
| 冷匹配 A=0.47&lt;B=0.78 | **模拟通过** | `测试_人脸冷匹配top1.py` |
| HTTPS 对讲拒绝 | **模拟通过** | `unsupported_audio_transport` |
| stop 打断重连等待 | **模拟通过** | `_connect` 打桩失败路径 |
| 后台试听轮询 `speak-tasks` | **已接线** | `Cameras.vue` `pollSpeakTask`；仍≠设备出声 ACK |
| 前端 `vite build` | **通过**（约 23s，历史） | 跳过卡住的 `vue-tsc -b`；完整 `npm run build` 仍建议本地再跑 |

## 真实设备（待现场验证）

目标：海康 DS-2CD3347SWDV3-LT。下列项**未执行**，不得标通过。

| # | 步骤 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| 1 | 读设备能力 / 对讲编码 | 真实 codec | 未执行 | 待验证 |
| 2 | RTSP 持续采集 + 超时日志 | ONLINE，open_ms/timeout_set 有日志 | 未执行 | 待验证 |
| 3 | 会员迎宾出声 | 扬声器可闻 | 未执行 | 待验证 |
| 4 | 陌生客 + 会员打断 | 陌生停、会员播，不回退 PC | 未执行 | 待验证 |
| 5 | HTTP ISAPI（非 HTTPS） | 推流 completed，device_playback=unverified | 未执行 | 待验证 |
| 6 | 多人进店 | 不错配姓名 | 未执行 | 待验证 |
| 7 | 断网恢复 | 退避后恢复 | 未执行 | 待验证 |
| 8 | 门口 face_side_px | 列表可见，辅助码流决策 | 未执行 | 待验证 |

说明：音频「推流完成」≠「顾客已听到」；无设备 ACK 时记 **PLAYBACK_UNVERIFIED**。

## 验收结论

- **代码正确性（冷匹配、HTTPS 拒绝、取消语义、帧龄）**：自动化模拟通过。
- **真机播报与识别 SLA**：**未验收**。
- **性能提升百分比**：**未宣称**（无本轮新基线对比表外推）。
