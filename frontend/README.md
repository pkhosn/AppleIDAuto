# React 业务工作台

用户端、管理员端和登录注册界面使用 React、TypeScript 与 Vite。ThinkPHP 负责业务、Session、支付和公开分享页面；管理员正式路径由 bootstrap 配置返回。

用户“消息推送”页面配置 Telegram 任务结果通知与人工验证码所需的 Bot Token、Chat ID；救援邮箱和 IMAPS 凭据在账号编辑页配置。功能条件见[配置指南](../docs/configuration.md)。

## 构建

在本目录使用 Node.js 22：

```bash
npm ci
npm run dev
npm test
npm run lint
npm run build
npm run build:production
```

`build` 将文件输出到 `public/react-preview`，通过 `/react-preview/` 和 HashRouter 运行；`build:production` 输出到 `public/control-app`，由 Nginx 按正式路径提供，并使用 BrowserRouter。每次构建都会替换对应的输出目录。Vite 不包含 PHP 代理，完整开发环境请按[开发指南](../docs/development.md)配置。

Playwright 必须设置 `PLAYWRIGHT_BASE_URL`，仅指向自己控制的测试站点；认证测试需要额外的测试账号变量。详见开发指南中的测试说明。

- [部署与 Nginx](../docs/deployment.md)
- [配置与接口约束](../docs/configuration.md)
- [开发与测试](../docs/development.md)
