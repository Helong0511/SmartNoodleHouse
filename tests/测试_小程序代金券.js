/**
 * 小程序代金券纯函数：门槛、面额、抵扣上限
 * 运行：node tests/测试_小程序代金券.js
 */
const path = require('path')
const assert = require('assert')

const mods = [
  ['wechat', require(path.join(__dirname, '../miniprogram-wechat/utils/voucher.js'))],
  ['alipay', require(path.join(__dirname, '../miniprogram-alipay/utils/voucher.js'))]
]

function run(name, voucher) {
  const v10 = { id: 1, discount_value: '10', min_order_amount: '20', template_name: '满20减10' }

  assert.strictEqual(voucher.voucherFaceValue(v10), 10, `${name} 面额`)
  assert.strictEqual(voucher.holdDisplayName(v10), '满20减10', `${name} 展示名`)
  assert.strictEqual(voucher.holdDisplayName({ name: '别名券' }), '别名券', `${name} name 兜底`)

  // 未达门槛不可用
  assert.strictEqual(voucher.isVoucherApplicable(15, v10), false, `${name} 未达门槛`)
  // 达门槛但应付 < 面额仍不可用（避免浪费）
  assert.strictEqual(
    voucher.isVoucherApplicable(9, { id: 2, discount_value: 10, min_order_amount: 0 }),
    false,
    `${name} 应付小于面额`
  )
  assert.strictEqual(voucher.isVoucherApplicable(25, v10), true, `${name} 可用`)

  // 抵扣不超过 eligible 基数
  assert.strictEqual(voucher.calcVoucherDiscount(30, v10, 8), 0, `${name} eligible 未达面额`)
  assert.strictEqual(voucher.calcVoucherDiscount(30, v10, 25), 10, `${name} 正常抵扣`)
  assert.strictEqual(
    voucher.calcVoucherDiscount(50, { id: 3, discount_value: 20, min_order_amount: 0 }, 15),
    0,
    `${name} eligible < 面额`
  )
  assert.strictEqual(
    voucher.calcVoucherDiscount(50, { id: 4, discount_value: 20, min_order_amount: 0 }, 18),
    0,
    `${name} eligible 仍不足面额`
  )
  // payable 达门槛且 eligible >= 面额时，抵扣取 min(面额, eligible)
  assert.strictEqual(
    voucher.calcVoucherDiscount(50, { id: 5, discount_value: 20, min_order_amount: 0 }, 18.5),
    0,
    `${name} 18.5 < 20`
  )
  assert.strictEqual(
    voucher.calcVoucherDiscount(50, { id: 6, discount_value: 20, min_order_amount: 0 }, 22),
    20,
    `${name} 取面额`
  )
  assert.strictEqual(
    voucher.calcVoucherDiscount(50, { id: 7, discount_value: 30, min_order_amount: 0 }, 25),
    0,
    `${name} eligible < 面额不核销`
  )
  // eligible 介于面额与应付之间：面额 20、eligible 25 → 抵 20
  assert.strictEqual(
    voucher.calcVoucherDiscount(100, { id: 8, discount_value: 20, min_order_amount: 0 }, 25),
    20,
    `${name} min(面额, eligible)`
  )
}

for (const [name, mod] of mods) run(name, mod)
console.log('ok 小程序代金券', mods.map((x) => x[0]).join('+'))
