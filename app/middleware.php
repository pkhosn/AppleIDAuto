<?php
// 全局中间件定义文件
use think\middleware\SessionInit;
use app\middleware\Install;
use app\middleware\SecurityHeaders;

return [
    // 全局请求缓存
    // \think\middleware\CheckRequestCache::class,
    // 多语言加载
    // \think\middleware\LoadLangPack::class,
    // Session初始化
    SessionInit::class,
    // 首次安装检查
    Install::class,
    SecurityHeaders::class,
];
