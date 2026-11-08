# 摄像头人脸迎宾 — 运维手册

## 添加摄像头

1. 后台填写 RTSP 地址、账号、密码（密码加密入库，勿写进文档仓库）。
2. 对讲 ISAPI 使用 **HTTP**（`resolve_isapi_base` 对 RTSP 默认 `http://主机`）。勿配 HTTPS 对讲基址（当前拒绝 `unsupported_audio_transport`）。
3. 业务参数打开「摄像头播放迎宾语音」；音量键 `camera_speaker_volume_percent`（播报前写入对讲通道 `speakerVolume`；无该字段才用软件增益）。

## 编码查询

- 运行中由 `HikvisionTwoWayAudio.resolve_codec` 探测；失败不写入 Redis 默认值。
- 日志关键字：`codec`、`codec_probe`。

## 常见故障

| 现象 | 排查 |
|---|---|
| 改密后对讲 401 | 确认更新密码走了 `invalidate_hikvision_caches`；重启后仍失败查 Digest |
| HTTPS/对讲失败 unsupported_audio_transport | 改为 HTTP ISAPI |
| 识别错人 | 看 `cold_scan top1/top2`；勿直接降阈值 |
| 有预览不识别 | 看 status DEGRADED、帧龄、花屏过滤；点「探测人脸」看 stale_frame / face_side_px |
| 停止摄像头卡住 | 本轮 stop 可打断重连 wait；仍卡则杀 Leader 进程 |
| 会员打断后电脑也响 | 应已修；确认已部署新 TTS |

## 日志与指标

- 采集：`Camera N ONLINE backend=FFMPEG open_ms=...`
- 识别 runtime：`detect_extract_ms` / `refine_extract_ms` / `match_ms` / `face_side_px`
- 对讲：`device_playback=unverified`；`status=completed|cancelled|failed`

## 诊断流程（简）

1. 启动摄像头 → 列表 ONLINE + 有帧。  
2. 探测人脸 → 非 stale；看相似度与脸像素。  
3. 试听摄像头 → 听扬声器（唯一真机出声证据）。  
4. 进店实测会员/陌生人/打断。

## 现场验收脚本（本地 Windows）

```powershell
$env:PYTHONPATH='E:\SmartNoodleHouse;E:\SmartNoodleHouse\backend'
python scripts\现场验收海康迎宾.py --camera-id 1
python scripts\现场验收海康迎宾.py --camera-id 1 --play-g711
# 人耳确认听到后：
python scripts\现场验收海康迎宾.py --camera-id 1 --play-g711 --heard yes
```

结果写入 `tests/_out/field_camera_<id>.json`。脚本故意不 import `face_service`（避免 InsightFace 冷启动卡住）。


## 2026-11-08 运维安全补充

- 不要把 RTSP URL（尤其包含账号密码）、ISAPI 凭据、摄像头 token 或抓拍图片放入文档、聊天、工单和命令历史。
- 修改摄像头密码后检查后端加密存储、缓存失效和连接重建；泄露时立即轮换设备凭据。
- 迎宾测试必须使用授权测试会员；未经授权顾客不得进入人脸匹配流程。
- 现场验收的 JSON 输出不得包含人脸向量、明文 RTSP 密码或顾客个人信息。
- 客户端动作完成标记不是活体证明；以服务端 challenge token 和动作验证结果为准。
