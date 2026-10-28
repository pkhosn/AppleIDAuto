<?php
// +----------------------------------------------------------------------
// | 控制台配置
// +----------------------------------------------------------------------
return [
    // 指令定义
    'commands' => [
        'dbclean' => 'app\command\DbClean',
        'register' => 'app\command\Register',
        'mailbox:key' => 'app\command\MailboxKey',
        'admin:path:reset' => 'app\command\AdminPathReset',
        'credentials:key' => 'app\command\CredentialsKey',
        'security:harden' => 'app\command\SecurityHarden',
        'security:rotate-tokens' => 'app\command\SecurityRotateTokens',
        'security:rotate-share-integration' => 'app\command\RotateShareIntegration',
        'security:rotate-db-password' => 'app\command\RotateDatabasePassword',
    ],
];
