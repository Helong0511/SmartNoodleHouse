/**
 * 小程序口味偏好纯函数：默认规格、爱好覆盖、多选合并
 * 运行：node tests/测试_小程序口味偏好.js
 */
const path = require('path')
const assert = require('assert')

const mods = [
  ['wechat', require(path.join(__dirname, '../miniprogram-wechat/utils/preferences.js'))],
  ['alipay', require(path.join(__dirname, '../miniprogram-alipay/utils/preferences.js'))]
]

function sampleProduct() {
  return {
    id: 1,
    name: '牛肉面',
    option_groups: [
      {
        id: 10,
        name: '辣度',
        code: 'SPICY',
        selection_type: 'SINGLE',
        is_required: true,
        options: [
          { id: 101, code: 'NORMAL', name: '不辣', is_default: true, status: 1 },
          { id: 102, code: 'HOT', name: '中辣', is_default: false, status: 1 },
          { id: 103, code: 'EXTRA_HOT', name: '特辣', is_default: false, status: 1 }
        ]
      },
      {
        id: 20,
        name: '加料',
        code: 'TOPPING',
        selection_type: 'MULTIPLE',
        is_required: false,
        options: [
          { id: 201, code: 'EGG', name: '鸡蛋', is_default: true, status: 1 },
          { id: 202, code: 'TOFU', name: '豆腐', is_default: false, status: 1 },
          { id: 203, code: 'OFF', name: '下架加料', is_default: true, status: 0 }
        ]
      }
    ]
  }
}

function run(name, prefs) {
  const product = sampleProduct()

  // defaultSelections：单选优先 is_default，多选取全部 is_default（忽略 status=0）
  {
    const d = prefs.defaultSelections(product)
    assert.strictEqual(d.selected[10], 101, `${name} 默认单选`)
    assert.deepStrictEqual(d.multi[20], [201], `${name} 默认多选过滤下架`)
  }

  // 无 is_default 时单选回退 NORMAL，再回退第一项
  {
    const p = {
      option_groups: [
        {
          id: 1,
          selection_type: 'SINGLE',
          options: [
            { id: 11, code: 'A', name: 'A' },
            { id: 12, code: 'NORMAL', name: '标准' }
          ]
        }
      ]
    }
    assert.strictEqual(prefs.defaultSelections(p).selected[1], 12, `${name} NORMAL 回退`)
  }

  // applyPrefsToProduct：单选按爱好覆盖，多选合并匹配项
  {
    const applied = prefs.applyPrefsToProduct(product, [
      { category: '辣度', option_code: 'HOT', order_count: 3 },
      { category: '加料', option_code: 'TOFU', order_count: 2 },
      { category: '加料', option_code: 'EGG', order_count: 1 }
    ])
    assert.strictEqual(applied.selected[10], 102, `${name} 爱好覆盖辣度`)
    assert.deepStrictEqual(applied.multi[20].slice().sort(), [201, 202], `${name} 多选合并`)
    assert.ok(applied.applied >= 2, `${name} applied 计数`)
    assert.ok(applied.prefHint, `${name} 有提示文案`)
  }

  // 无匹配爱好时保留默认
  {
    const applied = prefs.applyPrefsToProduct(product, [
      { category: '甜度', option_code: 'SWEET', order_count: 9 }
    ])
    assert.strictEqual(applied.selected[10], 101, `${name} 无匹配保留默认`)
    assert.strictEqual(applied.applied, 0, `${name} applied=0`)
  }

  // selectionsComplete
  {
    const d = prefs.defaultSelections(product)
    assert.strictEqual(prefs.selectionsComplete(product, d.selected, d.multi), true, `${name} 默认完整`)
    assert.strictEqual(prefs.selectionsComplete(product, {}, {}), false, `${name} 缺必选`)
  }

  // productPrefMatches / prefLabelsForProduct
  {
    const matchPrefs = [{ category: '辣度', option_code: 'HOT', order_count: 1 }]
    assert.strictEqual(prefs.productPrefMatches(product, matchPrefs), true, `${name} 可匹配`)
    assert.strictEqual(prefs.productPrefMatches(product, []), false, `${name} 无爱好`)
    const labels = prefs.prefLabelsForProduct(product, [
      { category: '加料', option_code: 'EGG' },
      { category: '加料', option_code: 'TOFU' }
    ])
    assert.ok(labels.some((x) => String(x).includes('加料')), `${name} 多选标签`)
  }
}

for (const [name, mod] of mods) run(name, mod)
console.log('ok 小程序口味偏好', mods.map((x) => x[0]).join('+'))
