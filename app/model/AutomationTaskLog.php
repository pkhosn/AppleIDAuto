<?php
declare (strict_types=1);

namespace app\model;

use think\Model;

class AutomationTaskLog extends Model
{
    protected $table = 'automation_task_log';
    protected $pk = 'id';
}
