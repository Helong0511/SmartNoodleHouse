/**
 * 小程序购物车纯函数：单价、合并、下架判定、分类依赖
 * 运行：node tests/测试_小程序购物车计价.js
 */
const path = require('path')
const assert = require('assert')

const mods = [
  ['wechat', require(path.join(__dirname, '../miniprogram-wechat/utils/cart.js'))],
  ['alipay', require(path.join(__dirname, '../miniprogram-alipay/utils/cart.js'))]
]

function run(name, cart) {
  // calcUnitPrice：基础价 + 单选加价
  {
    const product = {
      price: '12.00',
      option_groups: [
        { id: 1, selection_type: 'SINGLE', options: [{ id: 11, extra_price: '2.00' }] }
      ]
    }
    assert.strictEqual(cart.calcUnitPrice(product, { 1: 11 }, {}), 14, `${name} 单选加价`)
  }

  // 多选加价累加
  {
    const product = {
      price: 10,
      option_groups: [
        {
          id: 2,
          selection_type: 'MULTIPLE',
          options: [
            { id: 21, extra_price: 1 },
            { id: 22, extra_price: 2 }
          ]
        }
      ]
    }
    assert.strictEqual(cart.calcUnitPrice(product, {}, { 2: [21, 22] }), 13, `${name} 多选加价`)
  }

  // dataset 把 id 变字符串时仍能匹配
  {
    const product = {
      price: 10,
      option_groups: [
        { id: '1', selection_type: 'SINGLE', options: [{ id: '11', extra_price: 3 }] }
      ]
    }
    assert.strictEqual(cart.calcUnitPrice(product, { 1: '11' }, {}), 13, `${name} 字符串 id`)
  }

  // complete=false 时不得把「没拉到」当失效
  {
    const r = cart.reconcileWithProducts(
      [{ product_id: 999, quantity: 1, unit_price: 10, subtotal: '10.00' }],
      [],
      [],
      { complete: false }
    )
    assert.strictEqual(r.list[0].invalid, false, `${name} 分页未完不判下架`)
  }

  // complete=true 且商品不存在才判失效
  {
    const r = cart.reconcileWithProducts(
      [{ product_id: 999, quantity: 1, unit_price: 10, subtotal: '10.00' }],
      [],
      [],
      { complete: true }
    )
    assert.strictEqual(r.list[0].invalid, true, `${name} 完整菜单判下架`)
    assert.strictEqual(r.invalidCount, 1, `${name} invalidCount`)
  }

  // 库存不足时给出剩余数量
  {
    const r = cart.reconcileWithProducts(
      [{ product_id: 1, quantity: 5, unit_price: 10, subtotal: '50.00' }],
      [{ id: 1, status: 'ON_SALE', stock: 2 }],
      [],
      { complete: true }
    )
    assert.strictEqual(r.list[0].invalidReason, '仅剩 2 份', `${name} 库存文案`)
  }

  // mergeCartItem：同商品同规格合并数量
  {
    const list = cart.mergeCartItem(
      [{ product_id: 1, option_ids: [2, 3], quantity: 1, unit_price: 10 }],
      { product_id: 1, option_ids: [3, 2], quantity: 2 }
    )
    assert.strictEqual(list.length, 1, `${name} 合并条数`)
    assert.strictEqual(list[0].quantity, 3, `${name} 合并数量`)
  }

  // 规格顺序不同视为同一项
  {
    const k1 = cart.itemKey({ product_id: 1, option_ids: [3, 2] })
    const k2 = cart.itemKey({ product_id: 1, option_ids: [2, 3] })
    assert.strictEqual(k1, k2, `${name} itemKey 排序`)
  }

  // 数量上限 999
  {
    const list = cart.mergeCartItem(
      [{ product_id: 1, option_ids: [], quantity: 998, unit_price: 1 }],
      { product_id: 1, option_ids: [], quantity: 5 }
    )
    assert.strictEqual(list[0].quantity, 999, `${name} 数量上限`)
  }

  // checkCategoryRequire
  {
    const categories = [{ id: 10, name: '主食' }]
    const products = [{ id: 1, category_id: 10, name: '面' }]
    const blocked = cart.checkCategoryRequire(
      { id: 2, name: '加料', require_category_ids: [10] },
      [],
      products,
      categories
    )
    assert.strictEqual(blocked.ok, false, `${name} 缺必点分类`)
    assert.ok(String(blocked.message).includes('主食'), `${name} 提示含主食`)

    const ok = cart.checkCategoryRequire(
      { id: 2, name: '加料', require_category_ids: [10] },
      [{ product_id: 1, option_ids: [], quantity: 1 }],
      products,
      categories
    )
    assert.strictEqual(ok.ok, true, `${name} 已含分类放行`)
  }
}

for (const [name, cart] of mods) run(name, cart)
console.log('ok 小程序购物车计价', mods.map((x) => x[0]).join('+'))
