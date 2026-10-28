# 运维指南

部署入口见[部署指南](deployment.md)，密钥和功能配置见[配置指南](configuration.md)。本页介绍现有站点的升级、数据保全和故障处理。

## 升级与迁移

### 使用升级脚本

`upgrade.sh` 面向已完成安装、使用本项目 Compose 的单实例 Git 部署，默认更新已配置的 `origin/main`。公开仓库通过 HTTPS 拉取时无需 GitHub 账号、访问令牌或 SSH 密钥；如果保留 SSH 地址，仍需配置 SSH 密钥。私有 fork 需要提前配置对应的 Git 凭据。脚本不接收或打印访问令牌。

工作副本必须位于所选分支且能快进；脚本不会自动切换分支、stash、覆盖源码或合并分叉。下面使用本项目的公开仓库；使用自己的 fork 时，请替换仓库地址。脚本会自行拉取目标提交，并在依赖和产物准备完成、任务停止后更新工作区，因此升级前无需手动执行 `git pull`。

运行前先手动备份 MySQL 和 Compose 数据卷，在后台停用所有账号自动任务。等正在运行的任务和邮件恢复结束，并处理待核验结果及密码回写后，再安排维护窗口。维护期间暂停账号编辑、手动恢复等写操作，避免其他管理员重新提交任务。升级脚本不会替代这些准备工作。

```bash
cd /opt/appleid &&
git remote set-url origin https://github.com/wupingan/AppleIDAuto.git &&
bash upgrade.sh --check
```

检查通过后执行升级：

```bash
bash upgrade.sh
```

已有调度服务通过受限内网 HTTP API 连接管理站时，可保留该 `PANEL_URL`；升级脚本不会要求这类入口开放前端路由或经过公网 CDN。内网地址范围与健康检查方式见[部署指南](deployment.md)。公网 HTTP 地址仍会被拒绝。

`--check` 只检查本地环境、工作区、配置和当前任务状态，不拉取、构建、备份、迁移或重启服务；会使用 Git 目录中的并发锁。调度容器需正在运行才能完成此只读检查。正常升级支持对本脚本中断后已停止的服务进行重试，检查时可启动仅执行只读探针的临时容器，不启动调度器。

旧驱动的 `/health` 如缺少 `running` 计数，检查会返回 `driver_idle_status_unavailable`。此时先在后台确认全部任务和邮件恢复确已结束，再在 `automation-server/` 执行 `docker compose stop account-driver`，回到项目根目录重新检查和升级。脚本不会仅凭队列为空就推断旧驱动空闲；新驱动健康接口会同时返回排队数和执行数。

| 参数或设置 | 默认值与用途 |
| --- | --- |
| `--remote NAME` | `origin`；使用已配置的 Git 远程，可用于自己的 GitHub fork |
| `--branch NAME` | `main`；当前分支必须相同，仅快进更新 |
| `--backup-dir DIR` | 项目外的同级 `<项目目录名>-upgrade-backups`；指定目录也必须在项目外，仅当前用户可访问 |
| `--check`、`--help` | 只读检查、显示用法 |
| `UPGRADE_PROJECT_ROOT` | 仅站点外首次引导时指定目标项目；通常无需设置 |

工具要求为 Bash、Git、flock、PHP 8.2 及必要扩展、Composer、Node.js 22/npm、Docker Engine 和支持 `up --wait` 的 Docker Compose 插件。账号及任务检查需使用已有数据库配置，数据库账号应有已登记迁移所需的建表、改表权限。部署目录及其父目录需允许创建同文件系统的受限暂存目录，运行用户还需有 Docker 权限。

如果原部署使用 `docker compose -p` 自定义项目名，升级时请通过 `COMPOSE_PROJECT_NAME` 指定相同名称，以便沿用原有容器和数据卷。新增的必填配置需按更新说明提前补齐；脚本不会用示例文件覆盖现有配置。

宝塔上先通过 PATH 选择站点对应 PHP、Composer 和 Node.js 22，例如 PHP 8.2 可将 `/www/server/php/82/bin` 放在 PATH 前部，并用 `php -v`、`composer --version`、`node --version` 确认。脚本通过 PATH 使用工具，不重启或改写宝塔与 Nginx 配置。若 PHP-FPM 的 OPcache 关闭了文件变更检测，应按自己的服务管理方式重新加载 PHP-FPM，并重新检查 bootstrap 后再恢复任务。

升级开始时会固定远程提交，并在站点外准备生产 PHP 依赖、正式版和预览版前端，以及容器镜像。准备失败时不会替换现有部署。脚本确认所有账号已停用、Redis 中没有任务锁、队列和浏览器均空闲，且没有活动任务或需要处理的待核验操作后，保存配置与版本信息，再次检查状态并停止浏览器相关服务。之后才会快进代码、安装准备好的依赖和产物、执行登记迁移、清理应用及路由缓存，并更新容器、检查调度和 PHP bootstrap。

升级不会覆盖两份 `.env`，也不会轮换密钥；安装锁、Session、日志和数据卷会保留。备份目录权限为 `0700`，配置副本权限为 `0600`，同时记录提交、镜像 ID、步骤和结果。这份备份不包含 MySQL、SQLite、Redis 或恢复暂存，不能代替完整数据备份。

前端构建目录由脚本登记内容哈希和权限，记录保存在 Git 元数据目录。后续仅接受本脚本生成且未被人工修改的产物；其他未提交源码、暂存改动、人工产物或未登记文件会导致停止。请在源码中定制页面，不直接编辑部署产物，也不要删除哈希记录来绕过保护。

任何一步失败都会返回非零状态，并指出失败阶段。外部命令的原始输出不会写入升级日志，以免泄露配置。请根据失败阶段在受控终端排查，修复后重新运行完整脚本；即使目标版本已经安装，脚本也会重新执行流程，登记迁移可以重复运行。脚本不会自动回退数据库或重新启用任务。如果进程在替换目录时被强制终止，先检查部署目录和哈希状态，不要强制覆盖，也不要直接恢复旧密码数据。健康检查通过后，请管理员核对界面和任务状态，再手动恢复任务。

### 首次获取升级工具

旧版本没有升级脚本时，可在项目目录运行下面的引导命令。命令通过公开 HTTPS 地址获取版本信息，将升级脚本和配套辅助文件提取到站点外，再检查和升级。运行前仍需完成上面的备份、停用任务和维护准备，并确认信任该远程提供的脚本。使用自己的 fork 时，请替换下方仓库地址。

```bash
cd /opt/appleid
(
  set -euo pipefail
  git remote set-url origin https://github.com/wupingan/AppleIDAuto.git
  git fetch --no-tags origin refs/heads/main
  UPGRADE_BOOTSTRAP_DIR=$(mktemp -d)
  trap 'rm -rf -- "$UPGRADE_BOOTSTRAP_DIR"' EXIT
  git archive FETCH_HEAD upgrade.sh scripts/upgrade_helper.php scripts/upgrade_probe.py \
    | tar -x -C "$UPGRADE_BOOTSTRAP_DIR"
  UPGRADE_PROJECT_ROOT="$PWD" bash "$UPGRADE_BOOTSTRAP_DIR/upgrade.sh" --check
  UPGRADE_PROJECT_ROOT="$PWD" bash "$UPGRADE_BOOTSTRAP_DIR/upgrade.sh"
)
```

### 手动升级与旧库迁移

需要手动处理旧版本或排查脚本故障时，按以下步骤操作。下面的数据库和服务命令会产生实际变更，不属于只读检查。

1. 记录当前配置与账号启用状态，暂停新增任务，等待运行任务结束；先处理 `uncertain` 结果。
2. 备份 MySQL、两份 `.env` 和 Compose 数据卷，确认能恢复原加密密钥及调度状态。
3. 在隔离环境安装锁定依赖、构建前端、验证源码与迁移。不要复制示例配置覆盖已有 `.env`。
4. 已有数据库执行适用的增量迁移，然后更新相互兼容的 PHP、前端、调度和驱动。以下命令会修改当前配置指向的数据库，只在已备份且确认目标后执行：

```bash
cd /opt/appleid
php db/update/review_fixes.php
```

该迁移可重复执行，补齐分享组及分享、模板的关联字段；不轮换密钥或重写账号数据。空库安装由 `db/db.sql` 包含这些结构。不要在已有库重新导入初始化 SQL。

如果旧数据库缺少 `account.automation_schedule`，还需检查 [migrate_independent_schedules.php](../tests/migrate_independent_schedules.php)。该脚本会在本地写入敏感备份，并把旧周期初始化为自定义配置。运行前必须用 `SCHEDULE_BACKUP_DIR` 指定权限受限的绝对路径，并核对目录权限。这个脚本用于迁移，不是日常测试。独立周期的开关说明见[独立周期说明](../automation-server/INDEPENDENT_SCHEDULES.md)。

更新 Python 源码需要重建并重新创建相应容器；只改宿主机文件不会更新镜像内代码。在任务已停止后，从自动化目录执行：

```bash
cd /opt/appleid/automation-server
docker compose up -d --build
docker compose ps
```

检查健康状态、回报和前端页面后，再按原状态恢复任务。修改配置令牌也需要重新创建相关服务。回退优先使用兼容版本和功能开关；不要直接恢复旧数据库覆盖升级后已确认的新密码。

## 备份与恢复

备份至少应包括：

- MySQL 完整业务数据，包括用户、账号、分享、订单和站点配置。
- 根 `.env` 与自动化 `.env`，尤其是两份 AES 密钥；将密钥备份与公开源码分开保管。
- `automation-data` 中的 SQLite、密码暂存与恢复状态，以及 `redis-data`。备份 SQLite 时停止写入或使用一致性备份方式，不只复制正在写入的单个数据库文件。
- 需要保留的 `driver-artifacts`、部署配置和对应源码。截图、邮箱链接、临时凭据和日志均按敏感数据处理。

请在权限受限的位置保存加密备份，设置保留期限，并演练恢复流程。恢复时不要只还原主站数据库而遗漏调度状态或原加密密钥。先冻结账号并核对 Apple 状态，再允许修改任务。密钥丢失时应从备份中恢复原密钥，不能重新生成一个替代值。

## 任务失败与待核验

升级检查可以原样保留已停用、无强制执行标记且未安排核验的 `profile_restore` 待核验记录；检查不会把结果改为成功，也不会重新启用该功能。所有账号仍必须停用，队列、锁、浏览器及密码回写必须为空。已启用、正在核验、强制执行的资料恢复，以及其他功能的待核验记录仍会阻止升级。纯源码仓库不需要跟踪前端产物，已登记的构建目录可正常连续升级。

浏览器任务由独立子进程执行。任务超时后，父进程会撤销修改授权和验证码等待，结束任务及代理桥接进程组，并回收已登记的 Selenium 会话。只有确认进程退出后才会释放槽位。如果创建会话时响应丢失，导致无法取得会话 ID，仍需依赖 Grid 超时清理。

修改已提交但结果不明时会进入 `uncertain` 状态。自动恢复只会进行只读核验，不会再次移除设备、保存资料或修改密码。如果核验仍无法确认结果，需要人工处理；不要反复点击“运行”来代替核验。

新密码会先写入权限受限的暂存目录。主站回写后，系统会重新读取并确认密码一致，再删除暂存文件。如果 Apple 已确认改密但主站回写失败，系统会进入仅回写恢复，不再访问 Apple，也不会再次改密。不要为了清理队列而手动删除 `scheduled-password-escrow` 或恢复暂存文件。

## 故障排查

以下 Compose 命令请在 `automation-server/` 目录执行。带认证的健康检查命令见部署指南：

```bash
docker compose ps
docker compose logs --tail=100 automation-server account-driver selenium
```

| 现象 | 核对事项 |
| --- | --- |
| 登录后反复退出或 CSRF 失败 | HTTPS、Cookie、PHP Session 可写目录、浏览器是否切换域名或协议 |
| 控制台刷新 404、管理员页打不开 | Nginx SPA 回退、动态 `admin_base`、前端产物；遗忘入口可受控执行 `php think admin:path:reset` 重置为通用入口 |
| 页面返回 HTML 而不是 JSON | `/frontend-api/*` 是否正确转发 PHP，安装是否完成 |
| PHP 与调度认证失败 | `BACKEND.TOKEN`/`AUTOMATION_TOKEN`、`API_KEY`/`PANEL_API_KEY` 是否分别一致，容器是否重建 |
| 容器无法回报管理站 | `PANEL_URL`、DNS、证书、虚拟主机匹配及容器出站网络；不能假定默认主机别名可匹配站点 |
| 安装向导 Redis 检测失败 | 宿主机可达的地址与容器连接地址不同；默认 Compose 没有发布 Redis 端口 |
| `proxy_unavailable` | 节点启停、健康状态、容量、凭据、提取源及目标连通性 |
| 一直排队或 Selenium 会话不足 | 驱动槽位、Grid 会话数、容器资源、超时清理是否完成 |
| `page_changed`、恢复不明确 | 私下查看诊断截图和驱动日志，核对 Apple 页面变化；保留冻结状态 |
| 邮件验签失败 | 原始邮件是否保留、Apple DKIM 签名、DNS TXT 可达性、收件人与邮件时效 |
| Telegram 结果通知未收到 | 所属用户“消息推送”中的 Bot Token、Chat ID、结果筛选，以及 Bot 是否能向该聊天发送消息 |
| Telegram 验证码无响应 | 是否回复当前挑战图片、Bot 是否被其他程序占用；验证码交互配置见[配置指南](configuration.md#telegram-推送与验证码) |
| 密文无法读取 | 原 `.env`、密钥长度与对应关系；停止修改并从受限备份恢复 |

`admin:path:reset` 会修改后台入口；它和迁移、令牌轮换命令均不属于只读检查。对外提交故障材料前去除密码、令牌、邮箱链接、完整邮件和截图中的个人信息。

## 定期改密结果不确定

旧周期和独立周期均在提交改密前将候选密码写入调度数据目录下的
`scheduled-password-escrow/account-<id>-*.secret`（目录 0700、文件 0600）。
管理站成功接收新密码后才删除候选；提交后失败或回报中断会保留候选。
旧周期发现未处理候选时停用账号，避免再次修改密码。候选仅表示已准备的密码，
不能单凭文件存在判定 Apple 已采用它；须通过授权的只读登录核验后再同步管理站。
不要删除候选文件来绕过检查，也不要从日志或诊断截图推测密码。

Apple 成功弹窗可能位于关闭的 Shadow DOM 内。驱动在判断错误文本之前检查
无障碍树中的明确成功提示；页面跳转或密码框消失仍不能单独证明成功。

## 已知限制

- 依赖审计仍有已知限制：锁定的 `league/flysystem` 1.x 受 `CVE-2026-102601`（`PKSA-w9tt-7782-78jx`）低危公告影响，受 `topthink/think-filesystem` 的旧版本约束，不能仅在兼容的 1.x 范围升级消除。以 `composer audit --locked` 的实际结果为准，依赖审计 CI 可能因此失败；没有忽略此告警。
- 严格邮件验签会拒绝被转发链路破坏 DKIM 签名的邮件；DNS 验证失败同样停止恢复，不降级为信任认证头。
- Apple 页面、风控、验证码和恢复资格可能变化，自动化成功不构成保证；待核验操作不得自动重放。
- 默认部署使用单实例 SQLite 状态库和驱动进程内任务管理。提高消费者数量不代表支持多副本集群。
