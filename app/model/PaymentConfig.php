<?php
declare (strict_types=1);

namespace app\model;

use think\Model;

class PaymentConfig extends Model
{
    protected $table = 'payment_config';
    protected $pk = 'id';
}
