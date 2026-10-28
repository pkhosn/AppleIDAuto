<?php
declare (strict_types=1);

namespace app\model;

use think\Model;

class Order extends Model
{
    protected $table = 'order';
    protected $pk = 'id';
}
