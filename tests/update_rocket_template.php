<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
// Optional template example: select the target and a private backup directory explicitly.
$id = filter_var(getenv('SHARE_TEMPLATE_ID'), FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
$directory = rtrim(trim((string)getenv('TEMPLATE_BACKUP_DIR')), '/');
if (!$id || $directory === '' || $directory[0] !== '/') {
    fwrite(STDERR, "Set SHARE_TEMPLATE_ID and an absolute, private TEMPLATE_BACKUP_DIR before updating a template.\n");
    exit(1);
}
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__)); $app->initialize();
use think\facade\Db;

$template = Db::name('share_template')->where('id', $id)->find();
if (!$template || mb_strpos((string)$template['name'], '小火箭') === false) {
    throw new RuntimeException('Target template not found or name mismatch');
}

$html = <<<'HTML'
<section class="share-welcome">
  <h2>小火箭（Shadowrocket）下载教程</h2>
  <p>请仅使用本页提供的账号登录 <strong>App Store</strong> 下载应用。下载完成后，请立即退出该账号；不要在“设置”或 iCloud 中登录。</p>

  <h3>下载前请注意</h3>
  <ul>
    <li>开始前先确认自己原有的 App Store 账号和密码，避免退出后无法重新登录。</li>
    <li>共享账号仅用于下载已授权的应用，请勿修改账号资料、绑定手机号、添加付款方式或开启双重认证。</li>
    <li>如果系统要求验证付款信息、修改安全设置或出现其他异常提示，请停止操作并联系分享人。</li>
  </ul>

  <h3>操作步骤</h3>
  <ol>
    <li>打开手机上的蓝色 <strong>App Store</strong>。</li>
    <li>点击页面右上角的头像，进入账户页面。</li>
    <li>滑动到页面底部，点击“退出登录”。</li>
    <li>点击“登录”，如页面显示设备原账号，请选择“不是此账号”或切换到其他账号。</li>
    <li>输入本分享页提供的 Apple 账号和密码完成登录。</li>
    <li>如果出现“Apple ID 安全性”升级提示，请依次选择“其他选项”和“不升级”。</li>
    <li>登录成功后搜索 <strong>Shadowrocket</strong>，点击下载按钮完成安装。</li>
  </ol>


  <h3>下载完成后</h3>
  <p>请返回 App Store 账户页面退出共享账号，再登录你自己的账号。已经安装的应用通常可以继续使用；后续更新若再次要求账号验证，请按分享方提供的说明处理。</p>
</section>
HTML;

$notice = <<<'HTML'
<section class="share-notice"><strong>重要安全提醒</strong><p>本账号仅用于 App Store 下载应用。请勿登录“设置”或 iCloud，请勿修改密码、密保、手机号、付款方式或双重认证。</p></section>
<section class="share-guidance" aria-label="使用提醒">
  <div class="share-guidance-row"><span class="share-tag share-tag-blue">登录</span><p>只在 App Store 内登录；看到安全升级提示时选择“其他选项”与“不升级”。</p></div>
  <div class="share-guidance-row"><span class="share-tag share-tag-gray">完成</span><p>应用下载完成后立即退出共享账号，并重新登录你自己的 App Store 账号。</p></div>
  <div class="share-guidance-row"><span class="share-tag share-tag-gray">异常</span><p>遇到锁定、付款验证或资料修改提示时不要继续操作，请联系分享人。</p></div>
</section>
HTML;

umask(0077);
if (!is_dir($directory)) mkdir($directory, 0700, true);
$backup = $directory . '/rocket-template-' . date('Ymd-His') . '-' . bin2hex(random_bytes(4)) . '.json';
$snapshot = ['template' => $template, 'linked_shares' => Db::name('share')->where('template_id', $id)->field('id,template_id,html,notice_html')->select()->toArray()];
if (file_put_contents($backup, json_encode($snapshot, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)) === false) throw new RuntimeException('Backup failed');

Db::transaction(function () use ($id, $html, $notice): void {
    Db::name('share_template')->where('id', $id)->update(['name' => '小火箭下载教程', 'html' => $html, 'notice_html' => $notice, 'update_time' => date('Y-m-d H:i:s')]);
    Db::name('share')->where('template_id', $id)->update(['html' => $html, 'notice_html' => $notice]);
});

echo "Updated template {$id}; backup: {$backup}\n";
