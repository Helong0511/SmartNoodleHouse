/**
 * 菜单超过单页（100/200）时：分页必须拉齐；某一页失败不得把未拉到的菜判成下架。
 * 运行：node tests/测试_菜单超量.js
 */
const assert = require('assert')
const { fetchAllProducts } = require('../miniprogram-wechat/utils/products')
const { reconcileWithProducts } = require('../miniprogram-wechat/utils/cart')

function pageApi(total) {
  return {
    async get(_path, query) {
      const page = Number(query.page)
      const size = Number(query.page_size)
      const start = (page - 1) * size
      const items = []
      for (let id = start + 1; id <= Math.min(total, start + size); id++) {
        items.push({ id, name: '菜' + id, status: 'ON_SALE', price: '10.00', stock: 9, category_id: 1 })
      }
      return {
        items,
        total,
        page,
        page_size: size,
        total_pages: Math.max(1, Math.ceil(total / size))
      }
    }
  }
}

async function main() {
  const all = await fetchAllProducts(pageApi(250), { store_id: 1 }, false)
  assert.strictEqual(all.complete, true)
  assert.strictEqual(all.items.length, 250)
  assert.strictEqual(all.items[0].name, '菜1')
  assert.strictEqual(all.items[249].id, 250)

  const flaky = {
    async get(_path, query) {
      if (Number(query.page) > 1) throw new Error('timeout')
      const items = []
      for (let id = 1; id <= 200; id++) items.push({ id, name: '菜' + id, status: 'ON_SALE' })
      return { items, total: 250, total_pages: 2, page: 1, page_size: 200 }
    }
  }
  const partial = await fetchAllProducts(flaky, { store_id: 1 }, false)
  assert.strictEqual(partial.items.length, 200)
  assert.strictEqual(partial.complete, false)

  const cart = [{ product_id: 201, name: '鸡杂面', quantity: 1, unit_price: 12, subtotal: '12.00' }]
  const kept = reconcileWithProducts(cart, partial.items, [], { complete: false })
  assert.strictEqual(kept.invalidCount, 0)
  assert.strictEqual(kept.list[0].invalid, false)
  assert.strictEqual(kept.list[0].name, '鸡杂面')

  const gone = reconcileWithProducts(cart, all.items.filter((p) => p.id !== 201), [], { complete: true })
  assert.strictEqual(gone.invalidCount, 1)
  assert.strictEqual(gone.list[0].invalidReason, '已下架或不在本店菜单')

  const dup = {
    async get() {
      const items = []
      for (let id = 1; id <= 100; id++) items.push({ id, status: 'ON_SALE' })
      return { items, total: 300, total_pages: 3 }
    }
  }
  const stuck = await fetchAllProducts(dup, {}, false)
  assert.strictEqual(stuck.items.length, 100)
  assert.strictEqual(stuck.complete, false)

  console.log('菜单超量：分页拉齐、失败不误判下架')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
