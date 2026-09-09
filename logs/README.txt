文件日志目录说明
================

按来源分目录（均在仓库根 logs/ 下）：

  backend/              后端 API / 调度 / worker
  frontend/             Web 管理端 / H5（经 POST /api/v1/client-logs 上报）
  wechat/               微信小程序
  alipay/               支付宝小程序
  archive/{source}/     满月后的 tar.gz 归档

策略：
  - 各端日志均上报后端落盘（后端直写；前端/微信/支付宝经 POST /api/v1/client-logs）
  - 客户端劫持 console + 全局错误 + API 失败；跳过 client-logs 自身防环路
  - 单文件上限 5MB，滚动为 app1.log、app2.log …
  - 近 7 天保留明文；超过 7 天的滚动文件自动 gzip
  - 满自然月（或超过约 30 天）打成 YYYY-MM.tar.gz
  - 最长保留约 90 天（3 个月），到期删除

环境变量：LOG_DIR（默认 logs）、LOG_LEVEL、JSON_LOGS
定时任务：每日 03:30 执行压缩/打包/清理
