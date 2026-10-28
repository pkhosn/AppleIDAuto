# 部署指南

本指南统一说明通用 Linux 和宝塔部署。示例目录为 `/opt/appleid`，示例域名为 `apple.example.com`；请替换为自己的路径、域名和证书。已有站点按[运维指南](operations.md)升级，不重新执行首次安装。

源码不包含已构建的前端、实际 `.env`、证书、日志或业务数据库。项目仓库为 `https://github.com/wupingan/AppleIDAuto.git`；使用自己的 fork 时，请将文档中的仓库地址替换为自己的地址。

在线支付默认关闭，网关地址、商户号与商户密钥均为空。需要使用时，在自己的部署后台填写兼容的支付网关并启用；示例地址不能用于实际收款。

## 1. 准备环境

安装 Nginx、PHP 8.2 与 PHP-FPM、Composer 2、MySQL、Docker Engine、Docker Compose 插件、Node.js 22 和 npm。PHP CLI 与 PHP-FPM 均需启用 `pdo`、`pdo_mysql`、`curl`、`json`、`openssl`、`mbstring`、`dom`，并允许 `fsockopen`。

锁定的 PHP 依赖至少要求 PHP 8.0。根目录的 `composer.json` 和安装向导仍保留较低版本预检，因此不能据此使用 PHP 7.4。可运行 `composer check-platform-reqs` 检查依赖的实际要求。

先按 [README 中的拉取命令](../README.md#安装与文档)将项目克隆到 `/opt/appleid`。站点运行目录必须为 `/opt/appleid/public`。仅公开 Nginx 的 HTTP/HTTPS 端口，数据库和容器服务保留在可信网络。以下命令从项目根目录执行：

```bash
cd /opt/appleid
composer install --no-dev --prefer-dist --no-interaction
composer check-platform-reqs
npm ci --prefix frontend
npm run build:production --prefix frontend
cp .env.example .env
cp automation-server/.env.example automation-server/.env
chmod 600 .env automation-server/.env
```

配置中以 `replace-with-` 开头的值都是占位符，不能直接用于实际运行。首次安装向导会生成服务令牌和加密密钥；其他参数请参照两份示例配置。已有站点不要覆盖 `.env` 文件。

PHP-FPM 用户需要能写入 `runtime/` 和两份 `.env`，也需要能写入安装时用于原子替换配置的父目录。请按实际 PHP-FPM 用户设置权限；宝塔常用 `www`，其他系统可能使用 `www-data`。不要把整个仓库递归设为可写，也不要让密钥文件对所有人可读。安装后可以收回配置目录的写权限，但运行期间仍需允许写入 `runtime/`。

## 2. 准备数据库与 Redis

创建专用空数据库和专用 MySQL 用户，字符集使用 `utf8mb4`。用户需能读写业务表，初始化和迁移阶段还需对应的建表、改表权限。不要用已有业务库测试安装。

```sql
CREATE DATABASE appleid CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

首次安装由向导导入 [db/db.sql](../db/db.sql)，不需要先手动导入。先启动 Compose 中的 Redis：

```bash
cd /opt/appleid/automation-server
docker compose up -d redis
docker compose exec -T redis hostname -i
```

Compose 的 Redis 仅声明 `expose: 6379`，没有发布宿主机端口。PHP-FPM 在 Linux 宿主机运行时，向导的“Redis 检测地址”可填写上面得到的、宿主机可达的容器 IP；端口为 `6379`，数据库为 `0`，容器地址填写 `redis`。容器 IP 可能变化，仅用于当次宿主机连接检测；调度服务通过稳定的 Compose 服务名 `redis` 连接。

如使用独立 Redis，分别填写 PHP 所在主机和调度容器能访问的地址及认证信息。不要把未映射的 Compose Redis 当作宿主机 `127.0.0.1:6379`。若本机网络不允许访问容器 IP，应先提供受限的宿主机可达 Redis，再继续向导。

## 3. 配置 HTTPS 与 Nginx

申请域名证书并配置 HTTPS。会话 Cookie 使用 Secure 属性，HTTP 不适合作为登录入口。

完整站点示例见 [examples/nginx.conf](examples/nginx.conf)。替换域名、目录、证书和 PHP-FPM socket 后加入 Nginx 配置，再检查并加载：

```bash
nginx -t
# 通过系统服务或面板加载已检查的配置
```

示例同时覆盖根页面、`/user/*`、动态管理员路径的 SPA 刷新，以及 `/frontend-api/*`、`/api/*`、`/share/*`、`/shareapi/*`、`/pay/*` 和 `/install` 的 PHP 转发。管理员路径由管理站配置返回，不应在 Nginx 中固定成某个私有前缀。

如果在反向代理后终止 TLS，还需让 PHP 正确识别可信代理转发的 HTTPS 信息，保持站点 URL、浏览器访问地址和支付回调地址一致。

### 宝塔设置

1. 安装 Nginx、MySQL 和 PHP 8.2，并确认 CLI 与站点使用的 PHP 都加载了必要扩展。
2. 在面板添加自己的域名，将项目目录指向仓库，并把运行目录设为 `public`；然后创建专用数据库。
3. 申请证书、开启 HTTPS，使用示例中的路由规则替换冲突的默认伪静态配置。宝塔 PHP 8.2 socket 通常为 `/tmp/php-cgi-82.sock`，以面板实际配置为准。
4. 若保留面板提供的 PHP include，检查是否与示例的 PHP location 重复，最终只保留一套入口规则。禁止通过站点访问 `.env`、源码、依赖或备份。
5. 后续安装与服务启动沿用本指南，无需另一套宝塔命令。

## 4. 完成首次安装

打开 `https://apple.example.com/install`。依次完成环境检查、MySQL 与 Redis 连接检测，填写 HTTPS 站点 URL 和首个管理员账号。

向导会初始化数据库、生成独立的凭据和邮箱 AES 密钥、同步三组服务令牌到两份 `.env`，并写入 `runtime/install.lock`。已有有效密钥会保留；发现已有密文而密钥缺失或无效时会停止，必须先恢复原密钥。

保存成功后，确认 `automation-server/.env` 中的 `PANEL_URL` 指向调度容器实际能访问的管理站地址，通常是前面配置的 HTTPS 域名。默认虚拟主机未必能通过 `host.docker.internal` 正确访问；容器连接还需满足 DNS、TLS 和 Nginx `server_name` 配置。字段对应关系见[配置指南](configuration.md)。

`PANEL_URL` 是调度服务访问管理站的地址。公网入口应使用 HTTPS；已有仅向 Docker 网络开放、带访问限制的 HTTP API 入口时，可以保留其内网根地址。升级检查接受 `host.docker.internal`、localhost、回环地址和 RFC1918 私网 IPv4 的 HTTP 根地址，拒绝公网 HTTP、路径、凭据、查询参数和片段。内网入口无需开放前端页面，但必须支持带 `key` 认证头的 `POST /api/check_api`。升级后的内网健康检查从调度容器执行，不跟随重定向、不输出密钥；HTTPS 部署继续检查公开 bootstrap 接口。不要只为通过检查把有效的内网地址改成可能受 CDN 规则拦截的公网地址。

向导会将配置文件权限设为 `0600`。运行 Compose 的用户需要有权限读取自动化配置；不要因此把配置改成所有用户都可读。

安装向导会创建首个管理员，无需开启公开注册。如果要在已有数据库中添加管理员，可运行 `php think register <用户名> <密码>`。密码会出现在命令历史和进程参数中，请只在受控终端使用。公开注册需在后台“注册与安全”中配置，不能通过遗留的 `ENABLE_REGISTER` 环境项开启。

## 5. 启动并检查服务

完成向导、核对配置后：

```bash
cd /opt/appleid/automation-server
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose exec -T automation-server python -c 'import os, urllib.request; req = urllib.request.Request("http://127.0.0.1:3939/health", headers={"token": os.environ["AUTOMATION_TOKEN"]}); print(urllib.request.urlopen(req, timeout=10).read().decode())'
docker compose logs --tail=100 automation-server account-driver selenium
```

若曾在向导前启动过调度和驱动，向导更新令牌后需要重新创建相关容器以加载环境变量。调度健康检查也需要令牌，上述命令从容器环境读取，不打印令牌。检查日志时不要复制凭据、邮件链接或验证码图片到公开问题单。

默认网络与持久化：

| 服务 | 访问方式 | 持久化 |
| --- | --- | --- |
| PHP 管理站 | 对外 HTTPS，运行目录 `public` | MySQL、根 `.env`、`runtime` 中的运行文件 |
| automation-server | 宿主机 `127.0.0.1:3939` | `automation-data`，含 SQLite 和恢复暂存数据 |
| account-driver | Compose 内网 `3940` | `driver-artifacts`，诊断截图等 |
| Selenium | Compose 内网 `4444`；调试端口 `7900/5900` 仅绑定宿主机回环 | 浏览器会话按任务回收 |
| Redis | Compose 内网 `6379` | `redis-data`，AOF |

默认 3 个调度消费者共享 1 个驱动槽位和 1 个 Selenium 会话。启用真实账号前先确认密钥、回报链路、账号开关和代理策略；不要用真实账号作为安装验证。

独立周期发布开关默认关闭。需要启用时按[独立周期说明](../automation-server/INDEPENDENT_SCHEDULES.md)核对兼容性和状态，配置全局周期不会自动打开发布开关。
