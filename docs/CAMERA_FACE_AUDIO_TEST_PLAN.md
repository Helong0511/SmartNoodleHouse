# 摄像头人脸迎宾 — 测试计划

## 单元 / 自动化（本地 Windows PowerShell）

```powershell
cd E:\SmartNoodleHouse
python -m pytest tests/测试_摄像头迎宾修复.py tests/测试_陌生访客迎宾与清空.py `
  tests/测试_海康摄像头对讲TTS.py tests/测试_RTSP帧完整性.py `
  tests/测试_摄像头Leader守卫.py tests/测试_人脸最佳帧窗口.py `
  tests/测试_迎宾播报时延.py tests/测试_人脸迎宾称呼.py -q
```

覆盖重点：

| 项 | 测试文件 |
|---|---|
| codec 失败不持久化 / 改密清缓存 / 冷却回滚 | `测试_摄像头迎宾修复.py` |
| 陌生客冷却与清空 | `测试_陌生访客迎宾与清空.py` |
| G.711 / 对讲复用 Client / 开关回退本机 | `测试_海康摄像头对讲TTS.py` |
| RTSP 花屏过滤 | `测试_RTSP帧完整性.py` |
| Leader 守卫 | `测试_摄像头Leader守卫.py` |
| SSRF / 活体令牌 | `测试_摄像头SSRF与活体.py` |

## 集成（Mock 设备）

1. 模拟帧 → 匹配授权会员 → 入队 → Mock TTS/ISAPI → 断言任务与日志。
2. 各环节失败：认证失败、codec 未知、队列满、TTS 失败 → 不得标成功、不得无限重试。
3. 并发：同会员多摄像头 / 多 Worker 抢冷却 → 仅一次入队。

## 性能测试（现场）

记录：采集帧率、检测耗时、匹配耗时、入队延迟、TTS、音频发送、端到端延迟、CPU/内存。勿编造 QPS。
