# 摄像头人脸迎宾 — 测试计划

## 本地 Windows（PowerShell）

```powershell
cd E:\SmartNoodleHouse
python -m pytest tests/测试_人脸冷匹配top1.py tests/测试_摄像头采集与音频协议.py `
  tests/测试_人脸热集矩阵匹配.py tests/测试_摄像头迎宾修复.py `
  tests/测试_海康摄像头对讲TTS.py tests/测试_迎宾播报时延.py `
  tests/测试_摄像头Leader守卫.py tests/测试_RTSP帧完整性.py `
  tests/测试_人脸最佳帧窗口.py tests/测试_陌生访客迎宾与清空.py -q
```

## 覆盖映射

| 项 | 测试 | 类型 |
|---|---|---|
| 冷匹配 top-1 | `测试_人脸冷匹配top1.py` | 单元/模拟 |
| HTTPS 拒绝 / 取消 / 帧龄 / stop | `测试_摄像头采集与音频协议.py` | 单元/模拟 |
| 矩阵匹配 / 冷锁释放 | `测试_人脸热集矩阵匹配.py` | 单元 |
| 改密/codec/冷却 | `测试_摄像头迎宾修复.py` | 单元 |
| G711 / TTS ok·fail·cancel | `测试_海康摄像头对讲TTS.py` | 单元/模拟 |
| speak_gen Redis Lua 单调 bump | `测试_迎宾播报时延.py` | 单元 |
| FIFO BLPOP / member_pending / need_taste | `测试_迎宾播报时延.py` / `测试_多帧确认去重与播报任务.py` / `测试_陌生访客迎宾与清空.py` | 单元 |
| ensure 不压低 / 内存 stale 续取 / 冷却回滚 | 同上 | 单元 |
| both bump / speak_task 本地回退 / play_ulaw dict | `测试_迎宾播报时延.py` / `测试_海康摄像头对讲TTS.py` | 单元 |
| 会员 abort / 识别 monotonic / CameraManager 锁 | `测试_迎宾播报时延.py` / `测试_摄像头采集与音频协议.py` | 单元 |
| MultiFrame TTL / 会员抢占试听 | `测试_多帧确认去重与播报任务.py` | 单元 |
| stop join / connect abort | `测试_摄像头采集与音频协议.py` | 单元 |
| 陌生轨迹 TTL monotonic | `测试_陌生访客迎宾与清空.py` | 单元 |
| overview 计 DEGRADED | `测试_摄像头采集与音频协议.py` | 单元 |
| speak_task 本地 TTL monotonic | `测试_迎宾播报时延.py` | 单元 |
| 入队后 commit 失败仍抑制 | `测试_迎宾播报时延.py` | 单元 |
| edge 亦启 TTSWorker / 停采打断 | `测试_迎宾播报时延.py` | 单元 |
| overview Leader 无 worker 降级 | `测试_摄像头采集与音频协议.py` | 单元 |
| serialize Leader 无 worker 降级 | `测试_摄像头采集与音频协议.py` | 单元 |
| 坏帧 DEGRADED/RECONNECTING 契约 | `测试_摄像头采集与音频协议.py` | 单元 |
| overview 认 runtime.running | `测试_摄像头采集与音频协议.py` | 单元 |
| 非 Leader 启采 40922 | `测试_摄像头Leader守卫.py` / 采集协议 | 单元 |
| 试听抢占待播陌生 | `测试_多帧确认去重与播报任务.py` | 单元 |
| queue_full 不 bump | `测试_多帧确认去重与播报任务.py` | 单元 |
| 死线程 status/runtime OFFLINE | `测试_摄像头采集与音频协议.py` | 单元 |
| 花屏过滤 | `测试_RTSP帧完整性.py` | 单元 |
| 窗口择优 | `测试_人脸最佳帧窗口.py` | 单元 |

## 真机（未自动执行）

见 `CAMERA_FACE_AUDIO_ACCEPTANCE.md` 真机表。无设备不得勾通过。

## 压测 / 长稳

本轮**未执行**多小时连续运行与 P99 采集。
