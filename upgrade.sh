#!/usr/bin/env bash
# Upgrade an installed single-instance deployment. Regression tests mock services.
set +x
set -Eeuo pipefail
umask 022
stage='' backup='' step=初始化

usage() {
    cat <<'HELP'
用法：bash upgrade.sh [--remote origin] [--branch main] [--backup-dir DIR] [--check]
先停用全部自动任务，等待任务与邮件恢复结束，并自行备份 MySQL 和数据卷。
默认更新配置的 origin/main；仅接受快进，不覆盖源码或人工修改的前端产物。
--check  只检查本地环境、工作区、配置和任务状态，不拉取或更新服务。
--help   显示帮助。通过 PATH 选择宝塔 PHP、Composer 与 Node.js 22。
首次引导可设置 UPGRADE_PROJECT_ROOT 指向现有项目，脚本及辅助文件放在站点外。
HELP
}

main() {
    local remote=origin branch=main backup_base='' check_only=0
    while (($#)); do
        case "$1" in
            --help|-h) usage; return ;;
            --check) check_only=1; shift ;;
            --remote|--branch|--backup-dir)
                (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || { printf '%s\n' '参数缺少值。' >&2; return 2; }
                case "$1" in --remote) remote=$2 ;; --branch) branch=$2 ;; --backup-dir) backup_base=$2 ;; esac
                shift 2 ;;
            *) printf '%s\n' '未知参数；使用 --help 查看用法。' >&2; return 2 ;;
        esac
    done
    local root script_dir git_dir state helper probe current target project
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    root=$(cd -- "${UPGRADE_PROJECT_ROOT:-$script_dir}" && pwd -P)
    cd -- "$root"
    local tool
    for tool in git flock php composer node npm docker tar mktemp cp chmod date; do
        command -v "$tool" >/dev/null || { printf '缺少必要工具：%s\n' "$tool" >&2; return 1; }
    done
    git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null) || { printf '%s\n' '项目不是 Git 工作副本。' >&2; return 1; }
    [[ $(git rev-parse --show-toplevel) == "$root" ]] || { printf '%s\n' '请在完整项目 Git 工作副本使用脚本。' >&2; return 1; }
    exec 9>"$git_dir/appleid-upgrade.lock"
    chmod 600 "$git_dir/appleid-upgrade.lock"
    flock -n 9 || { printf '%s\n' '已有升级或检查正在执行。' >&2; return 1; }
    helper="$script_dir/scripts/upgrade_helper.php"
    probe="$script_dir/scripts/upgrade_probe.py"
    state="$git_dir/appleid-upgrade-artifacts.json"

    log() {
        printf '[升级] %s\n' "$1"
        if [[ -n "$backup" ]]; then printf '[升级] %s\n' "$1" >>"$backup/upgrade.log"; fi
    }
    cleanup() {
        local result=$?
        trap - EXIT
        if [[ -n "$backup" ]]; then
            printf 'after=%s\nresult=%s\n' "$(git rev-parse HEAD 2>/dev/null)" "$result" >>"$backup/versions.txt" || true
        fi
        if ((result != 0)); then
            log "失败阶段：$step；已停止，请核对任务与服务状态。" || true
            if [[ -n "$backup" ]]; then printf '备份目录：%s\n' "$backup" >&2; fi
        fi
        if [[ -n "$stage" && -d "$stage" ]]; then rm -rf -- "$stage"; fi
        exit "$result"
    }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # External tools can print credentials/URLs. Only controlled status messages
    # are logged; rerun a failing tool privately for detailed diagnostics.
    run() { "$@" >/dev/null 2>&1; }
    safe_php() { php -d display_errors=0 -d log_errors=0 "$helper" "$root" "$@"; }
    local -a compose=(docker compose --project-directory "$root/automation-server"
        --env-file "$root/automation-server/.env" -f "$root/automation-server/docker-compose.yml")
    step=环境与工作区检查
    log "$step"
    run git check-ref-format --branch "$branch"
    [[ "$remote" != -* ]] || return 2
    run git remote get-url "$remote"
    [[ $(git branch --show-current) == "$branch" ]] || { log '当前分支与目标分支不符。'; return 1; }
    safe_php platform
    safe_php workspace "$state"
    run composer --version
    [[ $(node -p 'process.versions.node.split(".")[0]') == 22 ]] || { log '需要 Node.js 22。'; return 1; }
    run npm --version
    run docker compose version
    run docker info
    project=$("${compose[@]}" config --format json 2>/dev/null | safe_php json-name)
    compose+=(-p "$project")

    container_state() {
        local id value
        id=$("${compose[@]}" ps --all --quiet "$1" 2>/dev/null)
        [[ "$id" =~ ^[a-f0-9]+$ ]] || { log '需要现有的单实例 Compose 服务。' >&2; return 1; }
        value=$(docker inspect --format '{{.State.Running}} {{.State.Paused}}' "$id" 2>/dev/null)
        case "$value" in 'true false') printf running ;; 'false false') printf stopped ;;
            *) log '服务正在暂停或无法确认状态。' >&2; return 1 ;; esac
    }
    idle() {
        local driver_status grid_status automation_status
        safe_php panel-idle
        [[ $(container_state redis) == running ]] || { log 'Redis 必须可用。'; return 1; }
        driver_status=$(container_state account-driver)
        grid_status=$(container_state selenium)
        automation_status=$(container_state automation-server)
        if [[ "$automation_status" == running ]]; then
            "${compose[@]}" exec -T automation-server python -B - "$driver_status" "$grid_status" <"$probe" 2>/dev/null
        else
            ((check_only == 0)) || { log '调度容器已停止，--check 无法执行完整检查。'; return 1; }
            # Retry after an interrupted upgrade without starting the scheduler.
            "${compose[@]}" run --rm --no-deps -T --entrypoint python automation-server -B - \
                "$driver_status" "$grid_status" <"$probe" 2>/dev/null
        fi
    }
    if ((check_only)); then
        step=只读任务检查
        idle
        log '检查通过；未拉取代码、执行迁移或重启服务。'
        return
    fi

    step=获取目标版本
    log "$step"
    current=$(git rev-parse HEAD)
    GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes}" \
        run git fetch --no-tags "$remote" "refs/heads/$branch"
    target=$(git rev-parse FETCH_HEAD)
    git merge-base --is-ancestor "$current" "$target" || { log '本地存在独有提交或历史分叉，不能快进升级。'; return 1; }
    [[ -z $(git ls-tree "$target" -- .env automation-server/.env runtime) ]] || { log '目标版本包含受保护的配置或运行数据。'; return 1; }

    [[ -n "$backup_base" ]] || backup_base="$(dirname -- "$root")/$(basename -- "$root")-upgrade-backups"
    mkdir -p -m 700 -- "$backup_base"
    backup_base=$(cd -- "$backup_base" && pwd -P)
    safe_php private-directory "$backup_base"
    stage=$(mktemp -d "$(dirname -- "$root")/.appleid-upgrade.XXXXXXXX")
    safe_php same-filesystem "$stage"
    mkdir -m 755 "$stage/source"
    cp -- "$helper" "$stage/helper.php"
    cp -- "$probe" "$stage/probe.py"
    helper="$stage/helper.php"
    probe="$stage/probe.py"
    safe_php fingerprint >"$stage/config-before.json"
    chmod 600 "$stage/config-before.json"
    git archive "$target" | tar -x -C "$stage/source"
    # Only Compose needs a copy of secrets in staging, never the Composer hooks.
    cp -- "$root/automation-server/.env" "$stage/source/automation-server/.env"
    chmod 600 "$stage/source/automation-server/.env"
    local -a staged_compose=(docker compose --project-directory "$stage/source/automation-server"
        --env-file "$root/automation-server/.env" -p "$project" -f "$stage/source/automation-server/docker-compose.yml")
    step=准备PHP依赖
    log "$step"
    run composer install --working-dir="$stage/source" --no-dev --prefer-dist --no-interaction --no-progress --optimize-autoloader --no-scripts
    run composer check-platform-reqs --working-dir="$stage/source" --no-dev
    step=安装前端依赖
    log "$step"
    run npm ci --include=dev --prefix "$stage/source/frontend"
    step=构建正式前端
    log "$step"
    run npm run build:production --prefix "$stage/source/frontend"
    step=构建预览前端
    log "$step"
    run npm run build --prefix "$stage/source/frontend"
    step=预构建容器镜像
    log "$step"
    run "${staged_compose[@]}" config --quiet
    run "${staged_compose[@]}" build --pull automation-server account-driver
    step=获取浏览器与Redis镜像
    log "$step"
    run "${staged_compose[@]}" pull selenium redis

    step=维护状态检查与配置备份
    log "$step"
    idle
    safe_php verify-config "$stage/config-before.json"
    backup=$(mktemp -d "$backup_base/$(date -u +%Y%m%dT%H%M%SZ)-${current:0:8}.XXXXXXXX")
    (umask 077; : >"$backup/upgrade.log")
    safe_php snapshot "$backup"
    printf 'before=%s\ntarget=%s\nproject=%s\n' "$current" "$target" "$project" >"$backup/versions.txt"
    local service id
    for service in automation-server account-driver selenium redis; do
        id=$("${compose[@]}" ps --all --quiet "$service" 2>/dev/null)
        docker inspect --format '{{.Id}} {{.Image}}' "$id" >>"$backup/versions.txt" 2>/dev/null
    done
    chmod 600 "$backup/versions.txt"
    log '配置与版本备份完成；此备份不包含 MySQL 和数据卷。'
    idle
    safe_php workspace "$state"
    [[ $(git rev-parse HEAD) == "$current" ]] || { log '准备期间本地提交发生变化。'; return 1; }
    step=停止空闲浏览器服务
    log "$step"
    run "${compose[@]}" stop automation-server
    run "${compose[@]}" stop account-driver selenium
    # Close the final pre-stop race before publishing code or running migrations.
    idle

    step=安装目标代码与产物
    log "$step"
    safe_php normalize "$state"
    run git merge --ff-only "$target"
    safe_php record-artifacts "$state"
    safe_php deploy "$stage" "$state"
    step=执行Composer初始化
    log "$step"
    run composer run-script post-autoload-dump --no-dev --no-interaction
    safe_php verify-config "$stage/config-before.json"
    step=执行登记的幂等迁移
    log "$step"
    run php db/update/review_fixes.php
    step=清理应用缓存
    log "$step"
    run php think clear --cache
    [[ ! -L runtime/route.php ]] || { log '路由缓存路径异常。'; return 1; }
    rm -f -- runtime/route.php
    safe_php verify-config "$stage/config-before.json"
    step=更新服务与健康检查
    log "$step"
    run "${compose[@]}" up -d --wait --wait-timeout 180
    "${compose[@]}" exec -T automation-server python -B - <<'PY' >/dev/null 2>&1
import json, os, urllib.request
request = urllib.request.Request('http://127.0.0.1:3939/health', headers={'token': os.environ['AUTOMATION_TOKEN']})
with urllib.request.urlopen(request, timeout=10) as response:
    assert json.load(response).get('status') is True
PY
    idle
    safe_php bootstrap
    safe_php verify-config "$stage/config-before.json"
    safe_php workspace "$state"
    for service in automation-server account-driver selenium redis; do
        id=$("${compose[@]}" ps --all --quiet "$service" 2>/dev/null)
        docker inspect --format '{{.Id}} {{.Image}}' "$id" >>"$backup/versions.txt" 2>/dev/null
    done
    step=完成
    log '升级成功；请核对页面和状态后，由管理员手动恢复任务。'
    printf '当前提交：%s\n备份目录：%s\n' "$target" "$backup"
}

main "$@"
