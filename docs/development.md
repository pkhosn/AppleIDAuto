# 开发指南

本指南介绍当前实现和验证方式。部署与业务配置分别见[部署指南](deployment.md)和[配置指南](configuration.md)；安装步骤统一在部署指南中说明。

## 目录与职责

| 路径 | 内容 |
| --- | --- |
| `app/controller`、`app/service`、`app/model` | ThinkPHP HTTP 入口、业务服务和数据模型 |
| `route/app.php`、`config` | 路由、中间件与框架配置 |
| `db/db.sql`、`db/update` | 空库结构与增量迁移 |
| `frontend/src`、`frontend/e2e` | React 控制台与浏览器测试 |
| `public/control-app`、`public/react-preview` | 正式版与预览版构建输出 |
| `view`、`public/static` | PHP 分享、支付、安装等页面仍使用的模板和资源 |
| `automation-server/app`、`automation-server/scripts` | 调度服务、恢复流程和受控运维脚本 |
| `account-driver/app` | 浏览器任务、独立执行进程、验证码和授权通信 |
| `tests` | 主站回归与部分运维脚本；不能把整个目录当作无副作用测试执行 |
| `.github/workflows/security.yml` | 依赖审计、回归和隔离 MySQL 集成测试 |

## 接口边界

- `/frontend-api/v1/*` 面向 React，基于 PHP Session。响应统一为 `success/data/message/errors/meta`，写操作使用 bootstrap 返回的 CSRF 令牌。管理员权限由后端检查，前端路径不是权限边界。
- 消息推送通过 `GET/POST /frontend-api/v1/notifications` 读取和保存所属用户的 Telegram 配置；救援邮箱通过账号编辑接口按账号保存，`POST /frontend-api/v1/mailbox/test` 检查表单中的 IMAPS 配置。
- `/api/*` 面向调度服务，接受 POST 并校验 `key` 请求头，与浏览器 Session API 分开。
- `/shareapi/v2/resolve` 是公开分享解析入口；`/share/*` 提供 PHP 分享页。
- `/pay/*` 的支付与回调约束见配置指南。支付结果以验签通知和数据库状态为准。
- 调度服务使用 `token` 头；驱动除 `/health` 外使用 `Authorization: Bearer ...`。驱动异步返回 `job_id`，随后轮询结果，不能把入队成功当成操作成功。

新增会修改 Apple 账号的浏览器操作时，也要处理执行前授权、提交状态、父子进程通信、超时终止、只读核验和主站回写。凭据、恢复邮件链接和验证码答案不能写入通信日志。

## 本地开发

在独立 Linux 开发环境安装 PHP/Composer、Node.js 22 和 Python 3.12，使用自己的空数据库和临时数据目录。不要复用部署目录的真实 `.env`。

```bash
composer install
npm ci --prefix frontend
npm run dev --prefix frontend
```

Vite 当前没有内置 PHP 反向代理。`npm run dev` 用于前端开发，完整登录和接口测试还需自行配置同源开发代理，或在隔离 HTTPS 测试站点使用预览产物：

```bash
npm run build --prefix frontend
# 产物 public/react-preview，访问 /react-preview/，使用 HashRouter
npm run build:production --prefix frontend
# 产物 public/control-app，由 Nginx 承接正式路径，使用 BrowserRouter
```

上述构建会替换对应输出目录；发布前核对 PHP 路由和当前管理员配置。根目录的 npm 脚本只构建旧 PHP 页面样式，不替代 React 构建。

## 回归检查

先安装 Composer 依赖，再从仓库根目录运行主站回归。常规回归使用模拟对象。不要用真实 Apple 账号运行受控恢复脚本。

邮件签名测试需要 OpenSSL CLI（支持 `genrsa -traditional`），测试运行时在内存中生成临时 RSA 密钥。纯源码检出时，前端产物检查会跳过；执行正式版构建后可验证产物。

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
npm test --prefix frontend
npm run lint --prefix frontend
npm run build:production --prefix frontend
```

两个 Python 服务均使用 `unittest`。分别创建虚拟环境，避免同名 `app` 包或不同依赖版本混用；以下命令从仓库根执行，虚拟环境保存在受忽略的 `.venv` 目录：

```bash
python3.12 -m venv automation-server/.venv
automation-server/.venv/bin/python -m pip install -r automation-server/requirements.txt
PYTHONPATH=automation-server automation-server/.venv/bin/python -m unittest discover -s automation-server/tests -v
python3.12 -m venv account-driver/.venv
account-driver/.venv/bin/python -m pip install -r account-driver/requirements.txt
PYTHONPATH=account-driver account-driver/.venv/bin/python -m unittest discover -s account-driver/tests -v
```

也可在隔离工作副本先构建当前 Compose 镜像，再运行 `bash tests/run_all.sh`。该脚本挂载的是测试目录，应用代码来自镜像，不能用旧镜像验证新源码；准备其所需的测试专用配置，不复用生产数据卷。

### MySQL 集成验证

`tests/test_review_business.php` 验证空库安装、迁移重复执行、跨用户删除、并发续费、重复回调、配额与充值续付。必须使用独立 MySQL，数据库名称只接受 `apple_review_test` 或 `apple_review_test_` 前缀；测试会清空该测试库。不要把测试账号授予其他业务库权限。

```bash
TEST_DB_HOST=127.0.0.1 \
TEST_DB_PORT=3306 \
TEST_DB_NAME=apple_review_test \
TEST_DB_USER=review_test \
TEST_DB_PASSWORD='replace-with-test-only-password' \
php tests/test_review_business.php
```

CI 使用隔离的 MySQL 8.4 服务。其他 PHP 检查脚本如 `tests/test_schedule_config.php` 会连接应用配置的数据库，即使会回滚也只在隔离环境运行。`migrate_independent_schedules.php`、`switch_independent_schedules.php` 是运维工具，不在通用测试循环中执行；旧周期迁移必须显式指定 `SCHEDULE_BACKUP_DIR`，详见运维指南。

`tests/check_schedule_release.php` 还要求显式设置 `RELEASE_CHECK_BASE_URL`，且目标 HTTPS 站点必须与本地数据库和 Session 配置对应；它会创建随后清除的诊断会话。`tests/update_rocket_template.php` 是可选模板更新示例，必须显式设置 `SHARE_TEMPLATE_ID` 和受限的绝对备份目录 `TEMPLATE_BACKUP_DIR`，会修改模板及关联分享页。这些辅助脚本不应作为通用回归运行。

### Playwright

必须显式设置 `PLAYWRIGHT_BASE_URL`，未设置时配置直接报错，避免意外访问其他站点。安装浏览器后，在 `frontend` 目录执行：

```bash
npx playwright install chromium
PLAYWRIGHT_BASE_URL=https://apple.example.com/react-preview/ npm run test:e2e
```

地址必须指向自己控制的隔离测试站点。`automation-cycles.spec.ts` 拦截业务 API，使用虚构账号验证周期界面；它需要预览入口 `/react-preview/`。`control-panel.spec.ts` 会请求真实测试站点，动态管理员路径从 bootstrap 读取。认证用例由 `E2E_USERNAME`、`E2E_PASSWORD` 启用，管理员用例还需 `E2E_ADMIN=1`；账号编辑回填检查可另外设置 `E2E_ACCOUNT_USERNAME` 及对应邮箱预期值。只使用测试账号，不将凭据提交到仓库。

### 依赖与语法

```bash
composer validate --no-check-publish
composer audit --locked
npm audit --prefix frontend
python3 -m pip_audit -r automation-server/requirements.txt
python3 -m pip_audit -r account-driver/requirements.txt
php -l app/service/ShareSchemaMigration.php
php -l db/update/review_fixes.php
```

Python 审计需另行安装 `pip-audit`。已知依赖限制见[运维指南](operations.md)，不能以固定测试数量或过去的审计结论替代当前检查。

## 贡献方式

提交修改时，请说明修复的问题、行为变化和验证方法。涉及数据库时，同时提供可重复执行的迁移和隔离验证。更新相关文档；公共说明集中维护，其他页面通过链接引用。修复权限、支付、密码回写或超时问题时，补充能复现缺陷的回归测试。

保留上游版权与许可，不提交 `.env`、数据库、备份、真实账号、邮件原文或敏感截图。报告安全问题时先使用目标仓库已配置的私密渠道；没有确认的仓库地址、维护者身份或联系邮箱不写入文档。项目许可见 [LICENSE](../LICENSE)。
