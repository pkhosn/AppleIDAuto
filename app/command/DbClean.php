<?php
declare (strict_types=1);

namespace app\command;

use app\model\UnlockRecord;
use think\console\Command;
use think\console\Input;
use think\console\input\Argument;
use think\console\Output;
use think\facade\Db;

class DbClean extends Command
{
    protected function configure()
    {
        // 指令配置
        $this->setName('dbclean')
            ->addArgument('action', Argument::OPTIONAL, "清理动作")
            ->addArgument('limit', Argument::OPTIONAL, "每个账号保留条数（PruneRecords 使用，默认 10）", 0)
            ->setDescription('用于清理数据库中的无用数据');
    }

    protected function execute(Input $input, Output $output)
    {
        $action = $input->getArgument('action');
        switch ($action) {
            case 'ExpireShare':
                // 清理过期分享页
                $output->writeln('开始清理过期分享页……');
                Db::name('share')->where('expire', '<', date('Y-m-d H:i:s'))->delete();
                $output->writeln('清理完成');
                break;
            case 'UnlockRecord':
                // 清空解锁记录
                $output->writeln('开始清空解锁记录……');
                $unlockRecord = new UnlockRecord();
                $unlockRecord->truncate();
                break;
            case 'PruneRecords':
                $limit = max(1, (int)($input->getArgument('limit') ?: UnlockRecord::RETENTION_LIMIT));
                $output->writeln("开始清理执行记录，每个 Apple 账号保留最新 {$limit} 条……");
                $deleted = (new UnlockRecord())->pruneAll($limit);
                $output->writeln("清理完成，共删除 {$deleted} 条记录");
                break;
            default:
                $output->writeln('未知的清理动作' . PHP_EOL);
                $output->writeln('可用的清理动作：');
                $output->writeln('ExpireShare - 清理过期分享页');
                $output->writeln('UnlockRecord - 清空解锁记录');
                $output->writeln('PruneRecords - 按账号清理执行记录（可跟保留条数，默认 10）');
                break;
        }
    }
}
