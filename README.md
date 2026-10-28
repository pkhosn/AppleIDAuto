# Apple ID 自动化管理

基于 ThinkPHP、React 和 Selenium 的 Apple ID 管理与自动化工具。管理站保存账号、权限和业务数据，独立调度服务管理任务，浏览器驱动执行 Apple 页面操作。TG交流群:https://t.me/appleidauto01

## 已实现功能

- 用户与管理员工作台：账号管理、启停、任务状态、运行记录和账号凭据管理。
- 检查账号状态和密码、恢复已配置的目标资料、清理设备及定期改密。各项功能可按账号分别启用；开启独立调度后可分别设置周期。
- 使用账号救援邮箱进行受控密码恢复和安全设置回滚。恢复取决于账号配置、有效的 Apple 恢复邮件、邮件验签和页面状态。
- OCR 与 Telegram 人工验证码协作；Telegram 任务结果通知可选择仅成功、仅失败或全部推送。
- 静态代理管理、代理提取来源、健康检查和账号代理分配。
- 分享页、分享组、模板、访问密码和有效期。
- 套餐、余额充值、订单续付与兑换码；在线支付需配置兼容的易支付网关。

功能启用条件见[配置指南](docs/configuration.md)，执行与恢复限制见[运维指南](docs/operations.md)。

## 技术结构

| 模块 | 技术与职责 |
| --- | --- |
| 管理站 | ThinkPHP 8、MySQL；业务、权限、分享、支付和 HTTP API |
| [frontend](frontend/README.md) | React、TypeScript、Vite；用户及管理员 SPA |
| [automation-server](automation-server/README.md) | Python、FastAPI、Redis、SQLite；调度、队列、回报与核验 |
| [account-driver](account-driver/README.md) | Python、Selenium Chromium；独立进程执行浏览器任务 |

Nginx 提供 HTTPS 和前端路由；PHP-FPM 运行管理站。Compose 提供调度服务、驱动、Selenium 和 Redis。当前架构按单套调度服务部署，默认 3 个队列消费者、1 个浏览器执行槽位。

## 运行要求

- Linux、Docker Engine 与 Docker Compose 插件。
- PHP 8.2，扩展和安装细节见部署指南。
- MySQL（`utf8mb4`）、Nginx、PHP-FPM；CI 使用 MySQL 8.4。
- 构建前端使用 Node.js 22 和 npm；Python 服务由 Python 3.12 容器运行。
- 可访问 Apple、所配置的邮件服务和代理来源。Selenium 配置了 2 GB 共享内存，主机还需为其他服务预留资源。

## 安装与文档

拉取仓库，将 `/opt/appleid` 替换为自己的安装目录，目标目录应为空或尚未创建：

```bash
git clone --branch main https://github.com/wupingan/AppleIDAuto.git /opt/appleid
cd /opt/appleid
```

拉取后按[部署指南](docs/deployment.md)安装依赖、配置 HTTPS，并打开 `/install` 创建首个管理员。已有站点请使用下面的升级步骤，保留原有配置和密钥。

| 文档 | 内容 |
| --- | --- |
| [部署指南](docs/deployment.md) | 通用与宝塔安装、Nginx、数据库、Redis、构建和服务启动 |
| [配置指南](docs/configuration.md) | 密钥对应关系、代理、验证码、邮件恢复与支付 |
| [自动化功能与排错指南](docs/automation-guide.md) | 面向使用者的功能说明、运行逻辑、界面操作和异常处理 |
| [运维指南](docs/operations.md) | 升级迁移、备份、故障排查和已知限制 |
| [开发指南](docs/development.md) | 目录、接口边界、本地开发、测试和贡献 |
| [独立周期说明](automation-server/INDEPENDENT_SCHEDULES.md) | v4 配置、开关、调度和待核验处理 |

## 升级

先备份 MySQL 和 Compose 数据卷，在后台停用所有自动任务，并等正在运行的任务和邮件恢复结束。已有站点位于 `main` 分支且包含 `upgrade.sh` 时，执行：

```bash
cd /opt/appleid &&
git remote set-url origin https://github.com/wupingan/AppleIDAuto.git &&
bash upgrade.sh
```

请把 `/opt/appleid` 换成实际项目目录。命令将 `origin` 设为本项目的公开 HTTPS 地址；使用自己的 fork 时，请替换仓库地址。地址已配置正确时，以后在项目目录直接执行 `bash upgrade.sh` 即可。

只检查状态或指定配置备份目录时，可分别使用：

```bash
bash upgrade.sh --check
bash upgrade.sh --backup-dir /secure/appleid-upgrades
```

脚本默认从已配置的 `origin/main` 快进更新。它会准备 PHP 依赖、前端产物和容器镜像，执行登记的幂等迁移，再更新服务并检查健康状态。脚本只备份两份配置和版本信息；发现源码或人工修改过的产物时会停止。升级成功后，请由管理员手动恢复任务。

旧版本尚无脚本时，按[首次获取升级工具](docs/operations.md#首次获取升级工具)从仓库提取到临时目录运行。参数、宝塔环境、备份与失败处理统一见[运维指南](docs/operations.md#使用升级脚本)。

## 许可

沿用 Apache-2.0，完整文本见 [LICENSE](LICENSE)。保留源文件和依赖中的上游版权、许可及声明；ThinkPHP 及其他第三方组件分别受其自身许可约束。
