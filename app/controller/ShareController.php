<?php
declare (strict_types=1);

namespace app\controller;

use app\BaseController;
use think\Request;
use think\response\View;

class ShareController extends BaseController
{
    public function index(Request $request): View
    {
        $share = $request->share;
        if (!$share) {
            return view('share/error', ['errorTitle' => '操作有误', 'errorMsg' => '非法请求']);
        }
        $accountList = $share->account_list;
        $accounts = [];
        foreach ($accountList as $accountID) {
            $account = $this->app->accountService->fetchInShare($accountID);
            if (!$account) {
                continue;
            } else {
                $account->status = $this->app->shareService->isAccountHealthy($account->toArray());
                $account->frontend_remark = $this->app->htmlSanitizerService->sanitize((string)$account->frontend_remark);
                $accounts[] = $account;
            }
        }
        // 账号随机排序
        if (env("share_random")) {
            shuffle($accounts);
        }
        return view('share/result', [
            'accounts' => $accounts,
            'customHtml' => $this->app->htmlSanitizerService->sanitize((string)$share->html),
            'noticeHtml' => $this->app->htmlSanitizerService->sanitize((string)$share->notice_html !== '' ? (string)$share->notice_html : $this->app->shareService->defaultNoticeHtml()),
            'link' => $share->share_link,
            'expiresAt' => $share->expire,
            'durationMinutes' => $share->duration_minutes,
            'firstAccessAt' => $share->first_access_at,
            'accountTotal' => count($accounts),
            'shareActionToken' => $this->app->shareAccessService->actionToken((string)$share->share_link),
        ]);
    }

    public function embed(Request $request): View
    {
        $link = strtolower(trim((string)$request->route('link')));
        $share = (new \app\model\SharePage())->fetchByLink($link);
        if (!$share || !$share->embed_enabled) {
            return view('share/embed', ['accounts' => [], 'needsPassword' => false, 'error' => '此分享未开放嵌入'])->code(404);
        }
        // SecurityHeaders applies this policy to the gate and errors as well.
        $request->shareEmbedAncestors = \app\service\ShareEmbedPolicy::ancestors((string)$share->embed_origins);
        if ($share->password && !$request->isPost()) {
            return view('share/embed', ['accounts' => [], 'needsPassword' => true, 'error' => '']);
        }
        $result = $this->app->shareAccessService->resolve(
            $link, $request->isPost() ? (string)$request->post('password', '') : '', getUserIP(), true, false
        );
        if (!$result['status']) {
            return view('share/embed', ['accounts' => [], 'needsPassword' => in_array($result['http_status'], [400, 403], true), 'error' => $result['msg']])->code($result['http_status']);
        }
        $accounts = [];
        foreach ($result['share']->account_list as $id) {
            $account = $this->app->accountService->fetchInShare($id);
            if ($account) $accounts[] = $account;
        }
        return view('share/embed', ['accounts' => $accounts, 'needsPassword' => false, 'error' => '']);
    }

    public function manualUnlock(Request $request): string
    {
        $id = $request->id;
        $link = $request->link;
        $share = $request->share;
        if (!$this->app->shareAccessService->validActionToken((string)$link, (string)$request->param('_token', ''))) {
            return alert('error', '页面令牌已失效，请刷新后重试', 2000, '/share/' . $link);
        }
        if (!in_array((int)$id, array_map('intval', $share->account_list), true)) {
            return alert('error', '账号不属于此分享页', 2000, '/share/' . $link);
        }
        $account = (new \app\model\Account())->fetch((int)$id);
        if (!$account || !$account->hasRecoveryMethod('account_login')) {
            return alert('error', '该账号未启用账号登录解锁方式', 2000, '/share/' . $link);
        }
        $check = $this->app->entitlementService->canManualUnlock((int)$share->owner, (int)$id);
        if (!$check['status']) {
            return alert('error', $check['msg'], 2000, '/share/' . $link);
        }
        $result = $this->app->automationTaskService->restartTask(
            (int)$id, (int)$share->owner, 'share', false,
            ['mode' => 'feature', 'feature' => 'password_check']
        );
        if ($result['status']) {
            return alert('success', '任务已提交，稍后将会自动解锁', 2000, '/share/' . $link);
        } else {
            return alert('error', $result['msg'], 2000, '/share/' . $link);
        }
    }
}
