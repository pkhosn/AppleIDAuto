# Apple ID Automation Management

This source distribution excludes deployment secrets, business data, logs, and generated frontend assets. Configure and build it for your own deployment.TG:https://t.me/appleidauto01

[简体中文](README.md) | English

An Apple ID management and automation tool built with ThinkPHP, React, and Selenium. The management panel stores accounts, permissions, and business data. A separate scheduler manages tasks, while a browser driver performs operations on Apple pages.

## Features

- User and administrator dashboards for account management, enabling and disabling accounts, task status, execution logs, and account credentials.
- Account status and password checks, restoration of configured profile values, device removal, and scheduled password changes. Each feature can be enabled per account; separate schedules are available when independent scheduling is enabled.
- Controlled password recovery and security settings rollback through the account's rescue mailbox. Recovery depends on account configuration, valid Apple recovery emails, signature verification, and the state of Apple's pages.
- OCR and human CAPTCHA assistance through Telegram. Telegram task result notifications can include successes, failures, or both.
- Static proxy management, proxy sources, health checks, and proxy assignment to accounts.
- Sharing pages, sharing groups, templates, access passwords, and expiration dates.
- Subscription plans, balance top-ups, resuming unpaid orders, and redemption codes. Online payments require a compatible Epay gateway.

See the [configuration guide](docs/configuration.md) for requirements to enable each feature and the [operations guide](docs/operations.md) for execution and recovery limitations.

## Architecture

| Component | Technology and responsibilities |
| --- | --- |
| Management panel | ThinkPHP 8 and MySQL; business logic, permissions, sharing, payments, and HTTP APIs |
| [frontend](frontend/README.md) | React, TypeScript, and Vite; user and administrator SPAs |
| [automation-server](automation-server/README.md) | Python, FastAPI, Redis, and SQLite; scheduling, queues, status reporting, and verification |
| [account-driver](account-driver/README.md) | Python and Selenium Chromium; browser tasks executed in separate processes |

Nginx handles HTTPS and frontend routing, and PHP-FPM runs the management panel. Docker Compose runs the scheduler, browser driver, Selenium, and Redis. The current architecture uses a single scheduler deployment, with 3 queue consumers and 1 browser execution slot by default.

## Requirements

- Linux, Docker Engine, and the Docker Compose plugin.
- PHP 8.2; see the deployment guide for required extensions and installation details.
- MySQL with `utf8mb4`, Nginx, and PHP-FPM. CI uses MySQL 8.4.
- Node.js 22 and npm to build the frontend. The Python services run in Python 3.12 containers.
- Network access to Apple, the configured email services, and proxy sources. Selenium is configured with 2 GB of shared memory; allow additional resources for the other services.

## Installation and Documentation

For a new installation, clone the public repository over HTTPS. No GitHub account or SSH key is required. Replace `/opt/appleid` with your installation path; the target directory must be empty or not yet exist:

```bash
git clone --branch main https://github.com/wupingan/AppleIDAuto.git /opt/appleid
cd /opt/appleid
```

Then follow the [deployment guide](docs/deployment.md) to install dependencies, configure HTTPS, and open `/install` to create the first administrator. For an existing installation, use the upgrade steps below and preserve your current configuration and encryption keys.

The guides and module READMEs linked here are in Chinese.

| Document | Contents |
| --- | --- |
| [Deployment guide](docs/deployment.md) | Standard Linux and BaoTa installation, Nginx, MySQL, Redis, builds, and service startup |
| [Configuration guide](docs/configuration.md) | Matching keys and tokens between services, proxies, CAPTCHAs, email recovery, and payments |
| [Operations guide](docs/operations.md) | Upgrades, migrations, backups, troubleshooting, and known limitations |
| [Development guide](docs/development.md) | Directory structure, API boundaries, local development, tests, and contributing |
| [Independent schedules](automation-server/INDEPENDENT_SCHEDULES.md) | v4 configuration, feature switches, scheduling, and handling operations awaiting verification |

## Upgrading

Back up MySQL and the Compose data volumes, disable all automatic tasks in the panel, and wait for running tasks and email recovery to finish. For an existing installation on the `main` branch that already includes `upgrade.sh`, run:

```bash
cd /opt/appleid &&
git remote set-url origin https://github.com/wupingan/AppleIDAuto.git &&
bash upgrade.sh
```

Replace `/opt/appleid` with your actual project directory. These commands set `origin` to this project's public HTTPS URL; replace the repository URL if you use your own fork. Once the correct remote is configured, you can run `bash upgrade.sh` directly from the project directory for future upgrades.

To check the current state or upgrade with a custom configuration backup directory, use the appropriate command:

```bash
bash upgrade.sh --check
bash upgrade.sh --backup-dir /secure/appleid-upgrades
```

By default, the script performs a fast-forward update from the configured `origin/main`. It prepares PHP dependencies, frontend assets, and container images, runs registered idempotent migrations, then updates services and checks their health. It backs up only the two configuration files and version information, and stops if it finds source changes or manually modified build artifacts. An administrator must manually re-enable tasks after a successful upgrade.

If your version does not include the script, follow [the upgrade tool bootstrap instructions](docs/operations.md#首次获取升级工具) to extract the tools from the repository and run them from a temporary directory. See the [operations guide](docs/operations.md#使用升级脚本) for parameters, BaoTa environments, backups, and failure handling.

## License

This project uses the Apache-2.0 license; see [LICENSE](LICENSE) for the full text. Retain upstream copyright notices, licenses, and other notices in source files and dependencies. ThinkPHP and other third-party components are covered by their respective licenses.
