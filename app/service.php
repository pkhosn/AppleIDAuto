<?php

use app\AppService;
use app\service\AccountService;
use app\service\ApiService;
use app\service\AuthService;
use app\service\BackendService;
use app\service\UnlockService;
use app\service\ProxyService;
use app\service\ShareService;
use app\service\UserService;
use app\service\PlanService;
use app\service\OrderService;
use app\service\PaymentService;
use app\service\RedeemCodeService;
use app\service\EntitlementService;
use app\service\AutomationTaskService;
use app\service\RegistrationService;
use app\service\SiteConfigService;
use app\service\TurnstileService;
use app\service\MailboxCredentialService;
use app\service\ManualRecoveryService;
use app\service\HtmlSanitizerService;
use app\service\SecurityRateLimitService;
use app\service\ShareAccessService;
use app\service\CredentialCipherService;
use app\service\NetworkAddressPolicy;

// 系统服务定义文件
// 服务在完成全局初始化之后执行
return [
    AppService::class,
    AuthService::class,
    AccountService::class,
    BackendService::class,
    ProxyService::class,
    ShareService::class,
    UserService::class,
    UnlockService::class,
    ApiService::class,
    PlanService::class,
    OrderService::class,
    PaymentService::class,
    RedeemCodeService::class,
    EntitlementService::class,
    AutomationTaskService::class,
    SiteConfigService::class,
    TurnstileService::class,
    RegistrationService::class,
    MailboxCredentialService::class,
    ManualRecoveryService::class,
    HtmlSanitizerService::class,
    SecurityRateLimitService::class,
    ShareAccessService::class,
    CredentialCipherService::class,
    NetworkAddressPolicy::class
];
