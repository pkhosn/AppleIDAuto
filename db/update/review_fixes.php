<?php
declare(strict_types=1);
// CLI only: php db/update/review_fixes.php. Does not change keys or tenant data.
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__, 2) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__, 2));
$app->initialize();
$connection = think\facade\Db::connect();
$connection->query('SELECT 1');
app\service\ShareSchemaMigration::apply($connection->getPdo());
echo "Share schema upgrade completed.\n";
