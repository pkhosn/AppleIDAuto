<?php
declare (strict_types=1);

namespace app\controller;

use app\BaseController;
class IndexController extends BaseController
{
    /**
     * 显示资源列表
     *
     * @return Response
     */
    public function index()
    {
        // Nginx serves the React application directly. Keep this route as a
        // safe fallback for PHP/dev-server access without reviving legacy UI.
        return redirect('/control-app/');
    }
}
