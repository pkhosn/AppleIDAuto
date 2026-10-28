<?php
declare (strict_types=1);

namespace app\middleware;

use Closure;

class Api
{
    public function handle($request, Closure $next)
    {
        if (!$request->isPost()) {
            return json(["code" => 405, "msg" => "请求方式错误", "status" => false], 405)->header(['Allow' => 'POST']);
        }
        $key = $request->header('key');
        if (!$key || $key == "") {
            return json(['code' => 401, 'msg' => '未提供密钥', "status" => false], 401);
        } else {
            $apikey = trim((string)env('API_KEY', ''));
            if ($apikey === '' || strlen($apikey) < 32 || !hash_equals($apikey, (string)$key)) {
                return json(['code' => 403, 'msg' => '密钥错误', "status" => false], 403);
            } else {
                return $next($request);
            }
        }
    }
}
