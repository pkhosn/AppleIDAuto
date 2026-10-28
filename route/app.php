<?php
// +----------------------------------------------------------------------
// | ThinkPHP [ WE CAN DO IT JUST THINK ]
// +----------------------------------------------------------------------
// | Copyright (c) 2006~2018 http://thinkphp.cn All rights reserved.
// +----------------------------------------------------------------------
// | Licensed ( http://www.apache.org/licenses/LICENSE-2.0 )
// +----------------------------------------------------------------------
// | Author: liu21st <liu21st@gmail.com>
// +----------------------------------------------------------------------
use app\middleware\Api;
use app\middleware\FrontendCsrf;
use app\middleware\Share;
use app\middleware\UserIndex;
use think\facade\Route;

// 首次安装向导
Route::get('install', 'install/index');
Route::post('install/test', 'install/testConnection');
Route::post('install', 'install/install');

// 注册index
Route::rule('/', 'index/index');
Route::get('index', 'index/index');

// React control panel JSON API. Keep this namespace isolated from the
// automation-facing /api contract.
Route::group('frontend-api/v1', function () {
    Route::get('bootstrap', 'frontendApi/bootstrap');
    Route::post('auth/login', 'frontendApi/login');
    Route::post('auth/register', 'frontendApi/register');
});

Route::group('frontend-api/v1', function () {
    Route::post('auth/logout', 'frontendApi/logout');
    Route::get('dashboard', 'frontendApi/dashboard');
    Route::get('profile', 'frontendApi/profile');
    Route::post('profile', 'frontendApi/updateProfile');
    Route::get('notifications', 'frontendApi/notifications');
    Route::post('notifications', 'frontendApi/updateNotifications');
    // Parameterized CRUD routes must precede collection routes. ThinkPHP's
    // matcher otherwise treats /accounts/:id as the /accounts collection.
    Route::get('accounts/:id', 'frontendApi/account')->completeMatch();
    Route::post('accounts/:id/reveal-password', 'frontendApi/revealAccountPassword')->completeMatch();
    Route::get('accounts', 'frontendApi/accounts')->completeMatch();
    Route::post('admin/accounts/:id/recovery/email-password', 'frontendApi/startEmailPasswordRecovery');
    Route::post('admin/accounts/:id/recovery/security-rollback', 'frontendApi/startSecurityRollback');
    Route::get('admin/accounts/:id/recovery-history', 'frontendApi/recoveryHistory');
    Route::get('admin/recoveries/:runId', 'frontendApi/recoveryStatus');
    Route::post('mailbox/test', 'frontendApi/mailboxTest');
    Route::post('accounts/:id', 'frontendApi/saveAccount')->completeMatch();
    Route::post('accounts', 'frontendApi/saveAccount')->completeMatch();
    Route::delete('accounts/:id', 'frontendApi/deleteAccount');
    Route::post('accounts/:id/enable', 'frontendApi/enableAccount');
    Route::get('automation-defaults', 'frontendApi/automationDefaults');
    Route::get('accounts/:id/schedule', 'frontendApi/accountSchedule')->completeMatch();
    Route::get('tasks', 'frontendApi/tasks');
    Route::post('tasks/:id/restart', 'frontendApi/restartTask');
    Route::get('records', 'frontendApi/records');
    Route::post('shares/batch-create', 'frontendApi/shareBatchCreate');
    Route::post('shares/batch-delete', 'frontendApi/shareBatchDelete');
    Route::get('share-groups', 'frontendApi/shareGroups')->completeMatch();
    Route::post('share-groups/:id', 'frontendApi/saveShareGroup')->completeMatch();
    Route::post('share-groups', 'frontendApi/saveShareGroup')->completeMatch();
    Route::delete('share-groups/:id', 'frontendApi/deleteShareGroup');
    Route::get('shares/:id', 'frontendApi/share');
    Route::get('shares', 'frontendApi/shares');
    Route::post('shares/:id', 'frontendApi/saveShare');
    Route::post('shares', 'frontendApi/saveShare');
    Route::delete('shares/:id', 'frontendApi/deleteShare');
    Route::get('templates', 'frontendApi/templates');
    Route::post('templates/:id/default', 'frontendApi/setDefaultTemplate');
    Route::post('templates/:id', 'frontendApi/saveTemplate');
    Route::post('templates', 'frontendApi/saveTemplate');
    Route::delete('templates/:id', 'frontendApi/deleteTemplate');
    Route::post('plans/:id/balance-buy', 'frontendApi/balancePurchase');
    Route::get('proxies', 'frontendApi/proxies');
    Route::post('proxies/:id/test', 'frontendApi/testProxy')->completeMatch();
    Route::post('proxies/:id', 'frontendApi/saveProxy')->completeMatch();
    Route::post('proxies', 'frontendApi/saveProxy');
    Route::delete('proxies/:id', 'frontendApi/deleteProxy');
    Route::post('proxy-sources', 'frontendApi/saveProxySource');
    Route::post('proxy-sources/:id', 'frontendApi/saveProxySource');
    Route::delete('proxy-sources/:id', 'frontendApi/deleteProxySource');
    Route::get('plans', 'frontendApi/plans');
    Route::get('orders', 'frontendApi/orders');
    Route::post('orders/:id/cancel', 'frontendApi/cancelOrder');
    Route::post('redeem', 'frontendApi/redeem');
    Route::get('admin/users', 'frontendApi/adminUsers');
    Route::post('admin/users/:id', 'frontendApi/saveAdminUser');
    Route::post('admin/users', 'frontendApi/saveAdminUser');
    Route::delete('admin/users/:id', 'frontendApi/deleteAdminUser');
    Route::get('admin/redeem-codes', 'frontendApi/adminRedeemCodes');
    Route::post('admin/redeem-codes/generate', 'frontendApi/generateRedeemCodes');
    Route::post('admin/plans/:id', 'frontendApi/savePlan');
    Route::post('admin/plans', 'frontendApi/savePlan');
    Route::delete('admin/plans/:id', 'frontendApi/deletePlan');
    Route::get('admin/settings', 'frontendApi/settings');
    Route::post('admin/settings/:section', 'frontendApi/updateSettings');
})->middleware([UserIndex::class, FrontendCsrf::class]);

// 支付
Route::group('pay', function () {
    Route::post('create', 'pay/create');
    Route::post('recharge', 'pay/recharge');
    Route::post('repay/:orderNo', 'pay/repayOrder');
})->middleware([UserIndex::class, FrontendCsrf::class]);
Route::group('pay', function () {
    Route::get('return', 'pay/returnUrl');
    Route::get('notify', 'pay/notify');
});


// 注册分享页API
Route::post('shareapi/v2/resolve', 'api/resolveSharepage');
Route::rule('shareapi/:link/[:password]', 'api/getSharepage');
// 注册分享页
Route::rule('share/', 'share/index')->middleware(Share::class);
Route::group('share', function () {
    Route::get('/', 'share/index');
    Route::post('/:link/:id/unlock', 'share/manualUnlock');
    Route::rule('/:link', 'share/index');
})->middleware(Share::class);

// 注册API
Route::group('api', function () {
    Route::rule('get_task_list', 'api/getTaskList');
    Route::rule('get_task_info', 'api/getTaskInfo');
    Route::rule('get_automation_schedules', 'api/getAutomationSchedules');
    Route::rule('update_automation_states', 'api/updateAutomationStates');
    Route::post('get_mailbox_config', 'api/getMailboxConfig');
    Route::rule('update_account', 'api/updateAccount');
    Route::post('finalize_recovery', 'api/finalizeRecovery');
    Route::post('reconcile_confirmed_recovery', 'api/reconcileConfirmedRecovery');
    Route::rule('get_password', 'api/getPassword');
    Route::rule('check_api', 'api/checkApi');
    Route::rule('random_sharepage_password', 'api/randomSharePagePassword');
    Route::rule('report_proxy_error', 'api/reportProxyError');
    Route::rule('get_proxy_sources', 'api/getProxySources');
    Route::rule('sync_proxy_source', 'api/syncProxySource');
    Route::rule('get_proxy_health_tasks', 'api/getProxyHealthTasks');
    Route::post('get_proxy_health_target', 'api/getProxyHealthTarget');
    Route::rule('update_proxy_health', 'api/updateProxyHealth');
    Route::rule('get_backend_api', 'api/getBackendApi');
    Route::rule('disable_account', 'api/disableAccount');
})->middleware(Api::class);
