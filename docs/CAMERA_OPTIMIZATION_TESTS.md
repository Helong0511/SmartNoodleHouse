# 摄像头优化 — 测试说明（第二轮）

## 本地 Windows（PowerShell）

```powershell
cd E:\SmartNoodleHouse
python -m pytest tests/测试_人脸热集矩阵匹配.py tests/测试_摄像头迎宾修复.py `
  tests/测试_陌生访客迎宾与清空.py tests/测试_海康摄像头对讲TTS.py `
  tests/测试_人脸最佳帧窗口.py tests/测试_迎宾播报时延.py `
  tests/测试_摄像头Leader守卫.py -q
```

2026-10-26 续作后以本机 pytest 为准（见 RESULTS）。

## 覆盖映射

| 优化项 | 测试 |
|---|---|
| 矩阵匹配正确性 / 加速 | `测试_人脸热集矩阵匹配.py` |
| 冷匹配不持锁 | 同上 `test_best_match_releases_lock_during_cold_match` |
| L2 解密归一化 | 同上 `test_decrypt_vec_normalizes` |
| 改密清缓存 / codec / 冷却回滚 | `测试_摄像头迎宾修复.py` |
| 陌生客 Redis 冷却 | `测试_陌生访客迎宾与清空.py` |
| 窗口择优 | `测试_人脸最佳帧窗口.py` |
| 海康 G711 / Client 复用 | `测试_海康摄像头对讲TTS.py` |
| speak_generation Redis / light / face_side_px | `测试_迎宾播报时延.py` |

## 准确率 / 现场（未自动化）

- 会员正确认、拒识陌生、侧脸/遮挡/逆光/多人：需标签视频 + 真实摄像头。  
- 前端：启动摄像头后列表「识别耗时」应出现检/配 ms；DEGRADED 标签可显示。  
- 安全：沿用既有 `测试_摄像头SSRF与活体.py`、权限与门店隔离清单；本轮未改鉴权边界。

## 性能复测命令（匹配微基准）

```powershell
cd E:\SmartNoodleHouse
python -c "import time,sys,numpy as np; sys.path[:0]=['backend','.']; from app.services.face_service import FaceEmbeddingCache, FaceService; from app.services.face_extractor import EMBEDDING_DIM; ..."
```

详见 `CAMERA_OPTIMIZATION_RESULTS.md` §4.1。
