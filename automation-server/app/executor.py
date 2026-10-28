import asyncio

from .config import settings


class AccountExecutor:
    def __init__(self, client=None):
        if client is None:
            from .account_client import AccountClient
            client = AccountClient()
        self.client = client

    async def run(self, task_info: dict) -> tuple[bool, str, str | None, str | None, dict | None]:
        if not task_info.get('enable', True):
            return False, '任务已禁用', None, 'disabled', None
        if task_info.get('proxy_required') and not task_info.get('proxy_available'):
            return False, '暂无健康代理，任务已暂停等待代理恢复', None, 'proxy_unavailable', None
        if task_info.get('_use_workflow') and hasattr(self.client.__class__, 'run_workflow'):
            return await self._run_workflow(task_info)
        missing = [key for key in ("username", "password") if not task_info.get(key)]
        if missing:
            return False, "管理站缺少字段：" + ", ".join(missing), None, "invalid_payload", None
        if not task_info.get("enable", True):
            return False, "任务已禁用", None, "disabled", None
        if task_info.get("proxy_required") and not task_info.get("proxy_available"):
            return False, "暂无健康代理，任务已暂停等待代理池恢复", None, "proxy_unavailable", None
        payload = self._payload(task_info)
        payload["_telegram"] = {
            "bot_token": str(task_info.get("tg_bot_token") or ""),
            "chat_id": str(task_info.get("tg_chat_id") or ""),
            "account": str(task_info.get("username") or ""),
        }
        # 账号资料自动恢复：把账号级开关与"首次"基线随 check 下发给驱动，
        # 驱动在登录 normal 状态检测变化并尽量改回首次姓名。
        profile_restore = task_info.get("profile_restore")
        if isinstance(profile_restore, dict) and profile_restore:
            payload["profile_restore"] = {"enabled": True, **profile_restore}
        checked = await self._check_with_retries(payload)
        if not checked["status"]:
            if checked.get('workflow_mutation_started') and checked.get('workflow_mutation_feature') == 'profile_restore':
                task_info['_workflow_mutation_ever_started'] = True
                task_info.setdefault('_feature_outcomes', {})['profile_restore'] = 'uncertain'
            return False, checked.get("message", "账号检查失败"), None, checked.get("error_code"), None

        state = checked.get("state", "unknown")
        messages = []
        new_password = None

        if state == "locked":
            return False, "账号已锁定，自动恢复已禁用", None, "locked", None
        if state == "two_factor":
            return False, "账号需要二步验证，自动恢复已禁用", None, "two_factor", None
        elif state == "password_invalid":
            return False, "账号密码校验失败", None, "password_invalid", None
        elif state == "authentication_challenge_unrendered":
            return False, "Apple 登录认证未建立，安全挑战页面未显示", None, state, None
        elif state == "identity_verification_failed":
            return False, "Apple 无法验证身份（密码错误或已触发限流，请稍后重试或更换代理）", None, state, None
        elif state != "normal":
            return False, f"未知账号状态：{state}", None, state, None

        if task_info.get("check_password_correct"):
            messages.append("账号密码正确性检测成功：账号登录状态正常")
        if task_info.get("enable_auto_disable_two_factor"):
            messages.append("双重认证检测成功：未发现需要解除的双重认证")
        if task_info.get("enable_auto_restore_profile"):
            profile_results = checked.get("profile_results")
            profile = checked.get("profile")
            summary = profile.get("summary") if isinstance(profile, dict) else ""
            if not summary and isinstance(profile_results, dict):
                summary = "；".join(str(item.get("message") or "") for item in profile_results.values() if isinstance(item, dict))
            if not summary and checked.get("profile_restored"):
                summary = "账号资料恢复成功：检测到资料变化并已恢复"
            elif not summary and isinstance(profile, dict) and profile:
                summary = "账号资料检测完成：旧驱动未返回逐字段结果"
            messages.append(summary or "账号资料检测未完成：驱动未返回四项资料结果")
        if not messages and not task_info.get("enable_auto_update_password"):
            messages.append("账号登录状态检查成功")

        outcomes = task_info.get('_feature_outcomes')
        feature_messages = task_info.get('_feature_messages')
        if isinstance(outcomes, dict):
            outcomes.update({'password_check': 'success', 'two_factor': 'success'})
            if task_info.get('enable_auto_restore_profile'):
                states = [str(item.get('status') or '') for item in (checked.get('profile_results') or {}).values()
                          if isinstance(item, dict)]
                outcomes['profile_restore'] = (
                    'uncertain' if 'uncertain' in states else
                    'failed' if any(state in {'detect_failed', 'restore_failed', 'manual_required'} for state in states) else
                    'success' if states or checked.get('profile') else 'failed'
                )

        if task_info.get("enable_delete_devices"):
            before_device_cleanup = task_info.get('_before_device_cleanup')
            if before_device_cleanup is not None and not await before_device_cleanup():
                device_message = '自动移除设备已取消：计划已关闭或调整'
                if isinstance(outcomes, dict):
                    outcomes['device_cleanup'] = 'cancelled'
                if isinstance(feature_messages, dict):
                    feature_messages['device_cleanup'] = device_message
                messages.append(device_message)
            else:
                try:
                    cleaned = await self.client.run("delete_devices", payload)
                except Exception:
                    # A transport timeout cannot prove whether the final remove
                    # confirmation reached Apple. Fail closed and require an
                    # operator to review/restart this destructive feature. Do
                    # not bubble the exception: later independent features in
                    # this merged run must remain normally schedulable.
                    task_info['_device_mutation_started'] = True
                    if isinstance(outcomes, dict):
                        outcomes['device_cleanup'] = 'uncertain'
                    device_message = '自动移除设备结果待确认：驱动通信中断，已停止自动重试'
                    if isinstance(feature_messages, dict):
                        feature_messages['device_cleanup'] = device_message
                    return False, device_message, None, 'device_removal_unconfirmed', None
                task_info['_device_mutation_started'] = bool(cleaned.get('device_removal_started'))
                if not cleaned["status"]:
                    result = 'uncertain' if task_info['_device_mutation_started'] else 'failed'
                    device_message = cleaned.get("message", "自动移除设备失败")
                    if isinstance(outcomes, dict):
                        outcomes['device_cleanup'] = result
                    return False, device_message, None, cleaned.get("error_code"), None
                removed_count = max(int(cleaned.get("removed_count") or 0), 0)
                device_message = (
                    "自动移除设备成功：账号当前没有可移除设备"
                    if removed_count == 0 else
                    f"自动移除设备成功：已从账户移除 {removed_count} 台设备"
                )
                messages.append(device_message)
                if isinstance(outcomes, dict):
                    outcomes['device_cleanup'] = 'success'
                if isinstance(feature_messages, dict):
                    feature_messages['device_cleanup'] = device_message

        if task_info.get("enable_auto_update_password") and not new_password:
            before_update = task_info.get('_before_password_update')
            if before_update is not None and not await before_update():
                if isinstance(outcomes, dict):
                    outcomes['password_update'] = 'cancelled'
                return True, '检查已完成；定期改密计划已取消或调整', None, None, checked.get('profile') if task_info.get('enable_auto_restore_profile') else None
            prepare_password = task_info.get('_prepare_password')
            if prepare_password is not None:
                payload['new_password'] = prepare_password()
            try:
                updated = await self.client.run("update_password", payload)
            except Exception:
                # When the driver connection itself is lost there is no result
                # telling us how far Apple progressed. Keep the conservative
                # uncertainty guard so a possibly submitted change is not
                # replayed automatically.
                task_info['_mutation_started'] = True
                raise
            # Entering the update-password action is not a mutation. Login,
            # captcha and security-question failures are still safely
            # retryable. Only the driver's password_submitted stage proves the
            # new password form was sent to Apple.
            task_info['_mutation_started'] = bool(
                updated.get("password_change_confirmed")
                or updated.get("password_change_started")
                or updated.get("stage") == "password_submitted"
            )
            if not updated["status"]:
                return False, updated.get("message", "密码更新失败"), None, updated.get("error_code"), None
            new_password = updated.get("password")
            if not new_password:
                return False, "密码更新完成，但账号驱动未返回新密码", None, "invalid_result", None
            if prepare_password is not None and new_password != payload['new_password']:
                return False, '改密返回值与预存凭据不一致，需人工复核', None, 'invalid_result', None
            payload["password"] = new_password
            messages.append("自动更新密码成功：新密码已同步到账号资产页面")
            if isinstance(outcomes, dict):
                outcomes['password_update'] = 'success'

        # 资料观测回传：仅当账号开启“自动恢复账号资料”时，把驱动检测/改回后的
        # 账号资料回传主站（供其记录"首次"基线与最近观测）。
        profile_payload = None
        if task_info.get("enable_auto_restore_profile"):
            observed = checked.get("profile")
            if isinstance(observed, dict) and any(observed.get(key) for key in ("name", "dob", "country", "language")):
                profile_payload = dict(observed)
                if isinstance(checked.get("profile_results"), dict):
                    profile_payload["results"] = checked["profile_results"]
                if checked.get("profile_restored"):
                    profile_payload["restored"] = True

        return True, "；".join(messages), new_password, None, profile_payload

    async def _run_workflow(self, task_info: dict):
        missing = [key for key in ("username", "password") if not task_info.get(key)]
        if missing:
            return False, "管理站缺少字段：" + ", ".join(missing), None, "invalid_payload", None
        due = list(task_info.get('_due_features') or [])
        payload = self._payload(task_info)
        payload.update({
            'run_id': str(task_info.get('_run_id') or ''),
            'config_revision': str(task_info.get('config_revision') or ''),
            'features': due,
        })
        payload['_telegram'] = {
            'bot_token': str(task_info.get('tg_bot_token') or ''),
            'chat_id': str(task_info.get('tg_chat_id') or ''),
            'account': str(task_info.get('username') or ''),
        }
        if 'profile_restore' in due and isinstance(task_info.get('profile_restore'), dict):
            payload['profile_restore'] = {'enabled': True, **task_info['profile_restore']}
        if 'password_update' in due:
            prepare_password = task_info.get('_prepare_password')
            if prepare_password is not None:
                payload['new_password'] = prepare_password()

        result = await self.client.run_workflow(
            payload,
            authorization_probe=task_info.get('_authorize_feature'),
            progress_callback=task_info.get('_workflow_progress'),
        )
        outcomes = task_info.get('_feature_outcomes')
        feature_messages = task_info.get('_feature_messages')
        steps = result.get('step_results') if isinstance(result.get('step_results'), dict) else {}
        messages = []
        for feature in due:
            step = steps.get(feature) if isinstance(steps.get(feature), dict) else None
            if not step:
                continue
            status = str(step.get('status') or 'failed')
            if isinstance(outcomes, dict):
                outcomes[feature] = status
            detail = str(step.get('message') or '')
            if feature == 'device_cleanup' and status == 'success':
                count = max(int(step.get('removed_count') or 0), 0)
                detail = ('自动移除设备成功：账号当前没有可移除设备' if count == 0
                          else f'自动移除设备成功：已从账户移除 {count} 台设备')
            elif feature == 'password_update' and status == 'success':
                detail = '自动更新密码成功：新密码已同步到账号资产页面'
            elif feature == 'password_check' and status == 'success':
                detail = '账号密码正确性检测成功：账号登录状态正常'
            elif feature == 'two_factor' and status == 'success':
                detail = '双重认证检测成功：未发现需要解除的双重认证'
            elif feature == 'profile_restore' and not detail:
                detail = '账号资料检测完成'
            elif status == 'cancelled':
                detail = detail or '计划已关闭或调整'
            if detail:
                messages.append(detail)
                if isinstance(feature_messages, dict):
                    feature_messages[feature] = detail

        task_info['_device_mutation_started'] = bool(result.get('device_removal_started'))
        task_info['_mutation_started'] = bool(task_info.get('_mutation_started') or result.get('password_submitted') or result.get('password_change_confirmed'))
        task_info['_workflow_mutation_ever_started'] = bool(result.get('workflow_mutation_ever_started'))
        uncertain_feature = str(result.get('workflow_mutation_feature') or '')
        if (result.get('workflow_mutation_started') and uncertain_feature in {
                'profile_restore', 'device_cleanup', 'password_update'}):
            if isinstance(outcomes, dict):
                outcomes[uncertain_feature] = 'uncertain'
            task_info['_workflow_mutation_feature'] = uncertain_feature
            if uncertain_feature == 'device_cleanup':
                task_info['_device_mutation_started'] = True
            elif uncertain_feature == 'password_update':
                task_info['_mutation_started'] = True
        if any(step.get('status') == 'uncertain' for step in steps.values() if isinstance(step, dict)):
            task_info['_workflow_uncertain'] = True
        if not result.get('status'):
            return False, result.get('message', '复合自动化任务失败'), None, result.get('error_code'), result.get('profile')
        state = str(result.get('state') or 'unknown')
        if state != 'normal':
            codes = {
                'locked': ('账号已锁定，自动恢复已禁用', 'locked'),
                'two_factor': ('账号需要二步验证，自动恢复已禁用', 'two_factor'),
                'password_invalid': ('账号密码校验失败', 'password_invalid'),
                'authentication_challenge_unrendered': ('Apple 登录认证未建立，安全挑战页面未显示', state),
                'identity_verification_failed': ('Apple 无法验证身份（密码错误或已触发限流，请稍后重试或更换代理）', state),
            }
            message, code = codes.get(state, (f'未知账号状态：{state}', state))
            return False, message, None, code, None

        new_password = result.get('password')
        if ('password_update' in due and isinstance(outcomes, dict)
                and outcomes.get('password_update') == 'success' and not new_password):
            return False, '密码更新完成，但账号驱动未返回新密码', None, 'invalid_result', result.get('profile')
        return True, '；'.join(messages) or '账号登录状态检查成功', new_password, None, result.get('profile')

    async def _check_with_retries(self, payload: dict) -> dict:
        """Retry only pre-submit technical failures with a bounded backoff."""
        attempts = max(int(settings.max_retries), 1)
        delays = settings.technical_retry_delays
        for attempt in range(attempts):
            checked = await self.client.run("check", payload)
            # Retrying here reuses the identical endpoint. Let FeatureRunner
            # discard/rotate a failed proxy immediately instead of launching
            # up to three more browser jobs through the same dead connection.
            if checked.get("error_code") in {
                "proxy_error", "proxy_connect_failed", "proxy_handshake_failed",
            }:
                return checked
            if (
                checked.get("status")
                or not checked.get("retryable")
                or checked.get("password_submitted")
                or attempt + 1 >= attempts
            ):
                return checked
            delay = delays[min(attempt, len(delays) - 1)] if delays else 1
            await asyncio.sleep(delay)
        return {"status": False, "error_code": "driver_timeout", "message": "账号检查失败"}

    @staticmethod
    def _payload(task_info: dict) -> dict:
        fields = (
            "username", "password", "dob", "q1", "q2", "q3", "a1", "a2", "a3",
            "webdriver", "proxy_id", "proxy_protocol", "proxy_content", "fixture_state",
        )
        return {key: task_info[key] for key in fields if task_info.get(key) not in (None, "")}
