# 摄像头人脸迎宾 — 修复记录

## A1 改密后 Digest Client 失效（P0）

- **表现**：后台改摄像头密码后试听/迎宾 401，直至进程重启。
- **根因**：`_SHARED_HTTP` 键仅为 `base|channel`，不含凭证。
- **修复**：键改为 `base|channel|auth_fp`；新增 `invalidate_hikvision_caches`；`update_camera` 改源/密时调用。
- **文件**：`hikvision_audio.py`、`api/cameras.py`
- **风险**：无；旧 Client 被 aclose。

## A2 codec 探测失败不持久化默认值（P1）

- **表现**：探测失败或非 200 仍把 alaw 写入 Redis 7 天，真机 ulaw 时噪声/无声。
- **修复**：`_detect_codec` 失败只返回临时默认值；`resolve_codec(block=False)` 不再把默认值写入热缓存；后台探测用 pending 去重。
- **文件**：`hikvision_audio.py`
- **风险**：首播在未探测成功前仍可能用临时 alaw（明确为临时，不锁死缓存）。

## A3 会员冷却失败回滚（P1）

- **表现**：抢到 Redis 冷却后顾客不存在，冷却不释放。
- **修复**：`WelcomeService.release_cooldown`；顾客缺失/入队失败时删除冷却键。
- **文件**：`face_service.py`

## A4 陌生访客 Redis 冷却（P1）

- **表现**：多 Worker 可重复播「欢迎光临」。
- **修复**：`unknown_welcome_cooldown_key` + SET NX EX；Redis 失败回退进程内字典并记日志。
- **文件**：`cache_keys.py`、`face_service.py`、`测试_陌生访客迎宾与清空.py`

## A5 高相似快通质量门槛（P1）

- **表现**：`sim >= threshold+0.04` 即可跳过多帧确认。
- **修复**：额外要求 `face_area_ratio>=0.06` 且 `det_score>=0.55`。
- **文件**：`recognition_worker.py`

## A6 前端探测人脸（P2）

- **修复**：`Cameras.vue` 增加「探测人脸」调用 `POST /cameras/{id}/probe-face`。

## A7 种子默认对齐（P2）

- **修复**：`sql/data_sys.sql` 中 `camera_speaker_tts_enabled` 默认改为 `true`（仅影响新库；已有 false 需手动开）。

## A8 重连抖动与 DEGRADED（P2）

- **修复**：重连退避加随机抖动；坏帧且缓冲过期时状态 `DEGRADED`。
- **文件**：`camera/camera_manager.py`
