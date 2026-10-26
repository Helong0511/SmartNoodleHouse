# 摄像头优化 — 回滚说明（第二轮）

## 何时回滚

- 匹配结果与优化前明显不一致（误认升高）且已排除阈值/数据因素。  
- 热集内存不可接受（极大 N）。  
- 前端 runtime 字段导致旧客户端解析失败（本项目自有管理端，概率低）。

## 回滚方式

### A. Git 回滚本轮相关文件

还原：

- `backend/app/services/face_service.py`
- `backend/app/services/face_pipeline_metrics.py`（删除）
- `backend/app/workers/recognition_worker.py`
- `backend/app/api/cameras.py`
- `frontend/src/views/Cameras.vue`
- `frontend/src/utils/labels.ts`
- `tests/测试_人脸热集矩阵匹配.py`（删除）

然后重启 API / 前端构建。无需数据库迁移。

### B. 仅回退匹配算法（保留可观测）

在 `FaceEmbeddingCache._match_hot` 改回逐条 `FaceService.cosine_similarity` 循环；保留 `_decrypt_vec` L2 与冷匹配放锁（建议保留后两者）。

### C. 配置层

本轮**未**改默认 `face_match_threshold` / 模型名。若现场误拧阈值，在业务参数恢复种子默认 **0.48** 并清设置缓存即可。

## 验证回滚成功

```powershell
python -m pytest tests/测试_人脸热集矩阵匹配.py tests/测试_摄像头迎宾修复.py -q
```

若已删除矩阵测试文件，则跑上轮迎宾相关用例即可。
