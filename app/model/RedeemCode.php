<?php
declare (strict_types=1);

namespace app\model;

use think\Model;

class RedeemCode extends Model
{
    protected $table = 'redeem_code';
    protected $pk = 'id';
}
