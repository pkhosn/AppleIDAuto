# Account Driver

Linux 下的独立浏览器驱动服务，通过 Selenium Chromium 执行账号检查、受控恢复、资料恢复、设备清理和改密。部署入口为 [automation-server/docker-compose.yml](../automation-server/docker-compose.yml)，公共安装和配置见[部署指南](../docs/deployment.md)、[配置指南](../docs/configuration.md)。

## 异步接口

`GET /health` 无需认证，其余接口使用 `Authorization: Bearer <ACCOUNT_DRIVER_TOKEN>`。

健康响应的 `queued` 和 `running` 分别表示排队及执行中的任务数，升级工具结合调度、恢复锁和 Selenium 会话检查空闲状态。

| 接口 | 用途 |
| --- | --- |
| `POST /check`、`POST /recover` | 状态检查与受控恢复 |
| `POST /update_password`、`POST /delete_devices` | 改密与实际移除设备，空设备列表也可成功 |
| `POST /workflow` | 复合步骤；调度侧默认不开启 |
| `POST /request_email_reset`、`POST /complete_email_reset`、`POST /complete_security_rollback` | 受控邮件恢复流程 |
| `GET /jobs/{job_id}` | 读取阶段、心跳、步骤结果、修改状态和挑战 |
| `POST /jobs/{job_id}/authorization/{challenge_id}` | 回应修改前授权 |
| `GET /jobs/{job_id}/captcha/{challenge_id}/image` | 获取受保护的验证码图片 |
| `POST /jobs/{job_id}/captcha/{challenge_id}/answer` | 提交人工答案 |

提交操作会返回一个异步任务。调用方需要轮询任务状态，直到拿到执行结果。内部通信日志不会记录凭据；调用方也应避免打印完整请求或结果。

## 隔离与清理

每个任务由独立子进程执行，父进程管理 HTTP、队列、心跳、授权和验证码通信。每个槽位独占认证代理桥接端口，桥接只用于 Compose 内网。

任务超时后，父进程会撤销挑战和授权，结束任务及代理桥接进程组，并回收已登记的 Selenium 会话。确认任务进程退出后，才会释放槽位。结果无法确认的修改交由调度服务核验，不会自动重放。创建会话时如果无法取得会话 ID，仍需依赖 Grid 超时清理。

截图等诊断材料写入 `driver-artifacts`，可能含敏感信息；按配置保留并限制访问。测试及维护方式见[开发指南](../docs/development.md)，恢复限制见[运维指南](../docs/operations.md)。
