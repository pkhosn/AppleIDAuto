# 自动化调度服务

使用 FastAPI、Redis 和 SQLite 管理账号任务、功能周期、互斥、重试、状态回报及只读核验。管理站提供账号配置，独立 account-driver 执行浏览器操作。

部署统一使用本目录的 [docker-compose.yml](docker-compose.yml)，配置从 [.env.example](.env.example) 创建。完整安装、HTTPS、Redis 和服务启动步骤见[部署指南](../docs/deployment.md)；令牌对应关系见[配置指南](../docs/configuration.md)。

## 执行行为

默认 3 个调度消费者共享 1 个驱动槽位；单个账号由 Redis 锁串行化。复合工作流默认关闭，开启后可在适用任务中复用浏览器与登录，每个修改步骤仍重新授权。

独立调度只合并已经到期的功能，未到期项保留计划时间。已确认改密但主站回写失败时仅恢复回写；结果不明的修改进入待核验，不自动重放。

任务结果通过账号所属用户配置的 Telegram Bot 推送，支持按成功或失败筛选。人工验证码交互使用同一 Telegram 配置，不受结果筛选影响。

代理、验证码和邮件恢复的启用条件见[配置指南](../docs/configuration.md)。邮件恢复使用账号自己的 IMAPS 配置；恢复邮件必须通过 Apple 对齐域的 DKIM 验签。

## 入口与数据

所有接口（包括 `GET /health`）都使用 `token` 请求头。驱动另用 `ACCOUNT_DRIVER_TOKEN`。SQLite 数据库和恢复暂存文件保存在 `automation-data`，请勿把它们当作临时缓存删除。

- [独立功能周期与发布开关](INDEPENDENT_SCHEDULES.md)
- [运维、迁移和已知限制](../docs/operations.md)
- [开发与隔离测试](../docs/development.md)
