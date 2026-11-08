> **文档维护说明（2026-11-08）**：回滚需区分应用代码、模型/配置、设备设置和数据库迁移；先确认备份可恢复、schema 兼容和旧 token 已撤销。禁止用回滚掩盖数据迁移失败或恢复未验证的数据。 当前设备/代码事实以 main 和现场探测为准，模拟测试不能替代真实摄像头验收。不得记录有效密钥、密码或 token。
>
> ---
>
# 摄像头人脸迎宾 — 回滚说明

> 场景：本地调试 / 生产均可按此回退。日期：2026-10-26。

## 代码回退

1. 还原本轮改动的关键文件（git checkout / 还原 PR），至少包括：
   - `backend/app/services/face_service.py`（冷匹配 top-1）
   - `camera/camera_manager.py`（RTSP 超时 / 可打断重连 / 帧龄）
   - `backend/app/services/hikvision_audio.py`（HTTPS 拒绝、结构化 play 结果）
   - `backend/app/workers/tts_worker.py`（cancelled 不回退本机）
   - `backend/app/api/cameras.py`（`stale_frame`）
   - `backend/app/workers/recognition_worker.py`（monotonic 帧龄）
2. 重启后端 API / Leader Worker。
3. 前端若有 Cameras.vue 识别列改动，重新构建后台前端并部署。

## 配置 / Redis

- 无需数据库迁移回退（本轮未改库结构）。
- 可选清理：`DEL snh:hik:codec:*`、`DEL snh:welcome:speak_gen:*`。
- 若曾把 ISAPI 配成 HTTPS：回退后仍应改回 **HTTP** 对讲基址（当前实现拒绝 HTTPS 明文推流）。

## 故障恢复

- 摄像头卡死：后台「停止」再「启动/重启」；仍卡则重启 Leader 进程。
- 对讲无声：确认 `camera_speaker_tts_enabled=true`、账号密码、HTTP ISAPI、设备扬声器。
- 识别错人：勿盲目降阈值；先看日志 `cold_scan top1/top2` 与列表「脸 Npx」。
