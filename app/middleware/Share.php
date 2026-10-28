<?php
declare (strict_types=1);

namespace app\middleware;

use Closure;

class Share
{
    public function handle($request, Closure $next)
    {
        $shareLink = $request->param('link');
        if (!$shareLink) {
            return $this->unavailable();
        }
        $stored = (new \app\model\SharePage())->fetchByLink($shareLink);
        if ($stored && $stored->password && !$request->param('password') && !$request->param('id')
            && !app()->shareAccessService->browserAuthorized((string)$shareLink)) {
            return view('share/password', ['link' => $shareLink]);
        }
        $result = app()->shareAccessService->resolve(
            $shareLink, (string)$request->param('password', ''), getUserIP(), true, true
        );
        if (empty($result['status'])) {
            if ((int)$result['http_status'] === 403 || (int)$result['http_status'] === 400) {
                return view('share/error', ['errorTitle' => '密码错误', 'errorMsg' => $result['msg']]);
            }
            return $this->unavailable($result['msg']);
        }
        $request->id = $request->param('id');
        $request->link = $shareLink;
        $request->share = $result['share'];
        return $next($request);
    }

    private function unavailable(string $message = '')
    {
        return view('share/error', [
            'errorTitle' => '分享页已失效',
            'errorMsg' => $message !== '' ? $message : '可能原因：已过期 / 已达到访问次数上限 / 链接已被删除。',
            'errorHelp' => '如需继续使用，请联系提供方重新生成分享链接。',
        ]);
    }
}
