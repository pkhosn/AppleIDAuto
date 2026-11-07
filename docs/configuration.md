# 配置指南

PHP 使用 [.env.example](../.env.example) 作为配置示例；Compose 中的两个 Python 服务共用 [automation-server/.env.example](../automation-server/.env.example)。站点、注册、套餐和支付等业务设置保存在 MySQL，由管理员界面管理。首次配置步骤见[部署指南](deployment.md)。

请替换示例中的所有占位符。下文所说的默认值以示例配置和 Compose 为准；直接运行 Python 时，代码使用的回退值可能不同。

## 密钥与地址对应关系

| PHP 根 `.env` | 自动化 `.env` | 用途 |
| --- | --- | --- |
| `API_KEY` | `PANEL_API_KEY` | 调度访问 PHP `/api/*`，使用 `key` 请求头 |
| `[BACKEND] TOKEN` | `AUTOMATION_TOKEN` | PHP 访问调度服务，使用 `token` 请求头 |
| 无对应字段 | `ACCOUNT_DRIVER_TOKEN` | 调度访问驱动，使用 Bearer 认证；Compose 注入到两个服务 |
| `[BACKEND] API_URL` | 无直接对应字段 | 宿主机 PHP 默认访问 `http://127.0.0.1:3939` |
| 无直接对应字段 | `PANEL_URL` | 调度访问 PHP 的站点根 URL，需实际可达且匹配虚拟主机 |
| `[CREDENTIALS] CIPHER_KEY` | 不复制给 Python | Apple 账号等敏感字段的 AES-256-GCM 密钥 |
| `[MAILBOX] CIPHER_KEY` | 不复制给 Python | 邮箱凭据的独立 AES-256-GCM 密钥 |

三组服务令牌需要分别生成，每个至少 32 个字符；可使用 `openssl rand -hex 24`。两份 AES 密钥也必须分别生成，内容为 32 字节随机数的 Base64 编码。首次安装向导会自动生成密钥；手工部署时可分别执行：

```bash
php think credentials:key --show
php think mailbox:key --show
```

命令输出为秘密，只写入对应配置并安全保存。已有密文时保留原密钥；生成新密钥不能修复丢失的旧密钥。令牌变更需同步两端并重新创建相关容器；AES 密钥不能当作服务令牌轮换。

`[DATABASE]` 配置宿主机 PHP 的 MySQL 连接，`REDIS_URL` 配置容器的 Redis 连接。`DATABASE_PATH=/data/tasks.sqlite3` 是调度状态库，与主站 MySQL 不同。浏览器驱动通过 Compose 的 `SELENIUM_URL` 连接 Selenium。

## 任务开关、并发与超时

账号 `enable` 为总开关，密码检查、双重认证检查/回滚、资料恢复、设备清理和自动改密还有各自开关。独立周期与旧统一周期的切换、v4 配置和到期规则统一见[独立周期说明](../automation-server/INDEPENDENT_SCHEDULES.md)。设备清理会实际移除设备页中的设备，空列表也可成功。

| 配置 | 示例值 | 含义 |
| --- | --- | --- |
| `WORKER_CONCURRENCY` | `3` | 调度消费者数量，不等于浏览器数量 |
| `DRIVER_CONCURRENCY` | `1` | 驱动工作槽位；每项任务独立子进程执行 |
| Compose `SE_NODE_MAX_SESSIONS` | `1` | Selenium 会话上限 |
| `ACCOUNT_DRIVER_QUEUE_TIMEOUT` | `1800` 秒 | 等待浏览器槽位的排队时限 |
| `DRIVER_ACTION_TIMEOUT` | `900` 秒 | 驱动任务执行时限 |
| `ACCOUNT_DRIVER_JOB_TIMEOUT` | `930` 秒 | 调度端等待执行结果的时限 |
| `WORKFLOW_V2_ENABLED` | `false` | 复合工作流灰度开关，默认关闭 |
| `WORKFLOW_V2_PERCENT` | `10` | 开启复合工作流后参与灰度的比例 |

调整并发时同时核对驱动槽位、Grid 会话上限、代理桥接端口和资源；当前单实例状态管理不支持直接横向复制为集群。复合工作流在独立调度模式的适用任务中复用浏览器、登录和代理，每个修改步骤仍需重新授权。关闭灰度后使用兼容接口。

## 静态代理与提取来源

在“代理网络”页面管理静态 IP 和自动提取源。根配置 `ENABLE_PROXY_POOL=false` 表示默认不启用托管代理分配。

在代理页面配置节点或提取来源，并设置协议、认证、绑定数量和启停状态。没有适用的启用节点时，任务可以直连；如果已启用的节点都不健康或没有容量，要求代理的任务会暂停并返回 `proxy_unavailable`。普通任务、复合任务和只读核验都会在执行前检查账号是否启用及代理要求。仅密码回写的恢复流程不会访问 Apple。

支持 `http`、`socks5`，端点可使用 `host:port` 或 `username:password@host:port`；用户名和密码中的保留字符应进行 URL 编码。认证代理由驱动建立临时内网桥接，任务退出后回收，不需对外开放桥接端口。

默认连续失败阈值为 3、冷却 1800 秒、健康检查周期 300 秒；提取来源默认刷新 600 秒。来源地址和返回端点受大小、数量及公网地址校验限制，默认拒绝回环、私有和链路本地来源。排查连接错误时先区分来源抓取、节点健康和 Apple 目标连通性。

## Telegram 推送与验证码

在用户工作台的“消息推送”中配置 Telegram Bot Token 和 Chat ID，并选择仅推送成功、仅推送失败或成功和失败都推送。两项凭据必须同时填写，清空任意一项可停止任务结果通知；结果筛选不影响人工验证码交互。

驱动先尝试 OCR，不能自动通过时可等待人工答案。调度服务使用账号所属用户的 Telegram 配置，将挑战图片发到对应聊天。人工直接回复该图片，输入 4–8 位英文字母或数字；旧挑战失效后应回复新图片。

`CAPTCHA_HUMAN_TIMEOUT=180` 为每次等待秒数，`CAPTCHA_HUMAN_ATTEMPTS=3` 为人工尝试次数，`TELEGRAM_POLL_TIMEOUT=25` 为长轮询时限。同一 Bot 的挑战按回复消息 ID 隔离。Bot 应专用于本服务，不同时配置 webhook 或让其他程序消费 `getUpdates`。账号没有可用人工通道时，挑战不能保证完成。

## 邮件恢复

在账号编辑页配置该账号的救援邮箱、IMAPS 主机、端口和凭据，并选择允许的邮件恢复方式。服务通过标准 IMAPS 连接邮箱，页面中的“测试连通性”检查当前填写的登录信息和收件箱只读访问。PHP 会加密保存邮箱密码，但有权限的编辑界面和执行流程仍会使用解密后的凭据，因此应限制管理员权限。

自动恢复与管理员手动恢复使用不同入口：

- 账号和对应恢复选项已启用、邮箱配置完整且状态匹配时，自动任务可以触发邮件密码恢复或安全设置回滚。执行修改前会先冻结账号，并检查当前配置和代理条件。
- 管理员可在“邮件恢复”页面选择账号、确认并发起受控恢复。发起前必须保存并禁用账号、自动改密和设备操作，并启用对应的邮件恢复方式；入口记录进度和结果，不等同于“运行到期项”。
- `scripts/controlled_email_recovery.py` 和 `scripts/controlled_security_rollback.py` 提供高级 CLI 入口，有明确的一次性确认参数；仅在理解脚本前置条件后使用。直连探测还需要脚本要求的额外确认。

安全设置恢复邮件支持中文、英文及西班牙语；西班牙语恢复操作识别 `regresar a la configuración de seguridad anterior` 等明确链接文字，关闭成功确认需明确表示双重认证已关闭。其他语言不明确的邮件会停止恢复。

上述恢复流程都必须通过邮件校验：邮件须带有验证有效的 Apple 域 DKIM 签名，且签名域需与 Apple 发件域对齐；系统还会检查收件人、操作类型、时效和 Apple HTTPS 链接。任意 `Authentication-Results` 文本都不能作为可信证据。签名损坏、DNS 查询失败或邮件内容不明确时，恢复会停止并返回错误；账号保持冻结，等待核查。

## 注册、管理入口与支付

注册默认关闭，由“注册与安全”控制。开启公开注册时配置 Turnstile 的站点密钥、服务端密钥以及允许的域名/动作。管理员入口可在后台修改；前端通过 `/frontend-api/v1/bootstrap` 的 `admin_base` 获取路径，不应写死到文档、测试或反向代理规则。

要启用在线支付，请在后台“支付设置”中填写网关 URL、商户号和签名密钥。网关需兼容当前 `submit.php` 的跳转方式和签名回调：

| 管理站入口 | 用途 |
| --- | --- |
| `POST /pay/create` | 创建套餐订单并跳转收银台 |
| `POST /pay/recharge` | 创建余额充值订单 |
| `POST /pay/repay/:orderNo` | 继续支付本人待支付订单；充值沿用原金额和名称，套餐重新校验套餐 |
| `GET /pay/notify` | 服务端验签回调，校验订单和金额后幂等发放 |
| `GET /pay/return` | 浏览器支付返回页面，不能替代服务端通知 |

前三项需要登录和 CSRF；回调依赖签名。配置站点 HTTPS 地址，确保网关能访问 `/pay/notify`。对接字段和签名以 [PaymentService.php](../app/service/PaymentService.php)、[PayController.php](../app/controller/PayController.php) 为准，使用网关沙箱验证，勿用真实充值作为文档检查。

管理站向网关提交 `pid`、`type`、`out_trade_no`、`notify_url`、`return_url`、`name`、`money`、`param`、`sign_type=MD5` 和 `sign`。生成签名时，先按参数名排序，排除 `sign`、`sign_type`、空字符串和 null 值，再将参数拼成 `key=value` 并用 `&` 连接。最后直接追加商户密钥并计算 MD5。

通知需包含签名、`trade_status=TRADE_SUCCESS`、本地订单号 `out_trade_no`、网关流水号 `trade_no`、金额 `money` 和支付类型 `type`。处理成功回复 `success`，验签或业务校验失败回复 `fail`；重复通知不会重复发放套餐或余额。

### 分享页 iframe 嵌入

在管理后台「分享页」新建或编辑链接，打开「允许 iframe 嵌入」，保存后复制嵌入代码。现有链接默认关闭；嵌入地址是 `/share/分享代码/embed`，展示账号、密码、账号状态、最近检测时间和复制按钮，不渲染页面模板或提供账号操作。状态判断与普通分享页一致；尚无检测时间时显示 `-`。

「允许嵌入的网站」每行一个完整来源，例如 `https://shop.example.com` 或 `http://localhost:8080`。协议、主机和端口必须匹配；不支持路径、通配符或登录信息。留空允许任意网站嵌入。嵌套 iframe 的每一层父页面都必须符合来源策略。此设置使用浏览器 CSP `frame-ancestors`，不限制直接访问或服务端抓取。

分享访问密码、有效期、首次访问计时和账号归属校验继续生效。有访问密码时，在 iframe 内输入密码，通过 POST 当次验证；不依赖第三方 Cookie，也不把密码放进 URL。重新载入时需重新输入密码。

升级先备份数据库，再执行现有迁移命令（可重复运行）：

```bash
php db/update/review_fixes.php
php tests/test_share_embed.php
```

反向代理必须保留应用返回的 CSP，并且不能在嵌入入口额外添加 `X-Frame-Options: DENY` 或 `SAMEORIGIN`。若原配置统一添加此头，可在 Nginx `http` 上下文加入以下 map，将原有 X-Frame-Options 指令替换为下列指令；其他安全头保持原配置：

```nginx
map $uri $share_frame_options {
    default DENY;
    "~^/share/[a-z0-9_-]{4,32}/embed$" "";
}
# 在原有 server 中：
add_header X-Frame-Options $share_frame_options always;
```

可选 HTTP 回归使用隔离 fixture 服务（禁止指向生产）：`EMBED_TEST_URL=http://127.0.0.1:18941 php tests/test_share_embed.php`。该测试需要 openframe（开放）、restricted（来源 http://embed-parent-allowed）、protected（密码 gate-secret）、disabled（关闭）、expired（过期）五条虚构分享，共享虚构账号 fixture@example.invalid / FixturePass1!；missing 必须不存在。测试检查密码门禁、POST 校验、重新载入、过期、关闭及响应头。测试数据不得使用真实账号。

### 分享管理筛选与访问密码

分享列表默认显示“未分组”，可切换“全部分享”或指定分享组。分组筛选与全局搜索共同作用于服务端分页，切换分组会回到第一页并清除已选项。分享组管理中可直接点击删除按钮旁的“编辑”。

新建分享及分享组的访问密码默认关闭；开启后必须填写密码。编辑已设置密码的项目时，开启状态下留空保留原密码，关闭并保存会移除密码。组内链接使用所属分享组的密码，修改组设置会同步到所有组内链接。未传 `password_enabled` 的旧 API 请求保留原有“密码留空保持原值”的行为。

单条分享和分享组保存成功后关闭弹窗并刷新列表；保存失败保留输入。批量生成成功保留结果窗口，方便复制链接。

本地回归验证（仓库根目录，使用现有部署的 PHP 容器）：

```bash
docker exec appleidauto-new-php-1 php tests/test_share_groups.php
docker exec appleidauto-new-php-1 php tests/test_template_isolation.php
docker exec appleidauto-new-php-1 php tests/test_schedule_config.php
docker run --rm --memory=1024m -v "$PWD:/srv/app" -w /srv/app/frontend node:22-alpine npm test -- --maxWorkers=1
docker run --rm --memory=1024m -v "$PWD:/srv/app" -w /srv/app/frontend node:22-alpine npm run build:production
```

PHP 用例在事务中验证分享密码和筛选，执行后回滚，不触发 Apple 自动化任务。

### 复制分享信息

分享列表的“复制分享”复制完整网址；设置了访问密码时，同一行附带“访问密码”。所属分享组的密码同步适用于组内链接。自定义分享网址优先用于复制。

新保存的访问密码在 `share` / `share_group` 的 `password_cipher` 字段中额外使用现有凭据密钥加密，原密码哈希继续用于访问校验。关闭密码时同时清除哈希和密文。升级沿用 `php db/update/review_fixes.php`，可重复执行；应先升级数据库再部署功能。已有哈希无法还原，旧加密分享需重新保存密码后才能附带密码复制。

复制通过 `POST /frontend-api/v1/shares/:id/copy`，校验登录身份、归属或管理员范围和现有 CSRF；响应禁止缓存。普通列表、详情及分组接口不返回密码密文。弹窗已删除只读的“当前用户”，创建分享仍归属登录用户。

勾选多个分享后，可使用“批量复制”，按勾选顺序每条分享单独占一行，同一条的链接和密码保持在同一行，访问密码规则与单条复制一致。跨页勾选保留，切换分组会清除勾选。批量接口为 `POST /frontend-api/v1/shares/batch-copy`，一次最多 500 条，先校验全部归属，再生成完整内容；任何条目不可用均提示错误，不返回部分结果。

### 套餐同步、配额与续费

升级沿用 `php db/update/review_fixes.php`，新增用户 `current_plan_id` 和 `quota_custom`。新购买、余额购买或兑换关联当前套餐，恢复配额跟随；续费从未到期时间顺延，到期后从当前时间开始。

修改套餐会同步关联且未开启自定义配额的用户，更新账号、分享额度和最低手动间隔，保持原到期时间。下调额度不会删除已有资源，超额后限制新增。管理员在用户编辑中可开启“自定义配额”，关闭则恢复当前套餐配置；手动修改额度自动开启自定义。重新购买套餐恢复跟随所选套餐。

历史用户根据最近成功支付或兑换记录补齐关联。同一时间存在不同套餐记录、套餐不存在或无记录时保留原状态；已有额度与当前套餐不同则保留为自定义，避免覆盖历史单独配置。

用户工作台显示“我的套餐”、实际使用/上限/剩余额度、超额状态、到期时间及剩余天数。到期前 7 天和到期后显示站内提醒，当前套餐仍在售时可打开续费确认框；支付须确认，不自动扣款。“购买套餐”入口可选择其他套餐。

本地验证：`docker exec appleidauto-new-php-1 php tests/test_plan_membership.php`（事务回滚，覆盖同步、自定义、续费、历史回填和提醒），以及上文既有 Vitest/生产构建命令。

管理员编辑用户时，Telegram Token / Chat ID 留空或未提交会保留原值，填写新值才替换。只有开启“清除 Telegram 配置”并保存才清空两项；用户端“消息推送”页仍可直接管理本人配置。回归验证使用 `php tests/test_plan_membership.php`，覆盖遗漏、空白、替换和明确清除，执行后回滚。

### 分享启用与用户水印

分享默认开启。列表和编辑弹窗可切换，勾选后支持批量开启/关闭（每次最多 500 条，全部校验归属）。关闭保留原链接、密码、计时和其他设置，仍占用额度；重新开启不会重置有效期。普通页、iframe、分享 API 及手动操作均拒绝关闭的分享，提示“该分享已关闭”，不返回账号凭据。

用户端“分享页”顶部的“分享设置”控制本人所有分享的水印：跟随站点默认、自定义（1 至 40 字）、关闭。水印按分享所属用户读取，同时用于普通页和 iframe。管理员“品牌与站点”提供默认水印，不覆盖用户已选择的自定义或关闭模式；管理员可切换用户端设置自己的分享水印。

升级执行现有 `php db/update/review_fixes.php`，添加 share.enabled 和用户水印字段，默认兼容原展示。验证使用 `php tests/test_share_groups.php`、`php tests/test_share_embed.php` 及既有前端测试命令。

关闭提示可在用户端“分享设置”填写，默认“暂时停止分享”，1 至 100 个字符，作为纯文字显示。关闭的 iframe 继续遵守原允许来源策略，显示居中提示卡片。已打开 iframe 每 15 秒请求只含启用状态和提示的 `/share/:link/availability`，关闭时清除页面凭据并显示提示，重新开启时重新加载；带密码分享需要再次验证。网络暂时异常时保留最后确认状态。
