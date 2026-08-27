/** 购物车计价与展示辅助 */

function parseMoney(v) {
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : 0
}

function findOption(group, optionId) {
  // 小程序 dataset 常把 id 变成字符串，不能用 ===
  return (group.options || []).find(o => String(o.id) === String(optionId))
}

function pickSelected(map, gid) {
  if (!map) return null
  if (map[gid] != null) return map[gid]
  return map[String(gid)]
}

/** 根据所选规格计算单价（基础价 + 加价） */
function calcUnitPrice(product, selected, multi) {
  let total = parseMoney(product.price)
  ;(product.option_groups || []).forEach(g => {
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      const opt = sid != null ? findOption(g, sid) : null
      if (opt) total += parseMoney(opt.extra_price)
    }
    if (g.selection_type === 'MULTIPLE') {
      ;(pickSelected(multi, g.id) || []).forEach(oid => {
        const opt = findOption(g, oid)
        if (opt) total += parseMoney(opt.extra_price)
      })
    }
  })
  return Math.round(total * 100) / 100
}

/** 已选规格名称（用于购物车展示） */
function buildOptionLabels(product, selected, multi) {
  const labels = []
  ;(product.option_groups || []).forEach(g => {
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      const opt = sid != null ? findOption(g, sid) : null
      if (opt && opt.code !== 'NORMAL') labels.push(opt.name)
    }
    if (g.selection_type === 'MULTIPLE') {
      ;(pickSelected(multi, g.id) || []).forEach(oid => {
        const opt = findOption(g, oid)
        if (opt) labels.push(opt.name)
      })
    }
  })
  return labels
}

function collectOptionIds(product, selected, multi) {
  const ids = []
  ;(product.option_groups || []).forEach(g => {
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      if (sid != null && sid !== '') ids.push(Number(sid))
    }
    if (g.selection_type === 'MULTIPLE') {
      ;(pickSelected(multi, g.id) || []).forEach(oid => ids.push(Number(oid)))
    }
  })
  return ids.filter(id => Number.isFinite(id) && id > 0)
}

/** 补齐旧购物车条目字段 */
function normalizeItem(item) {
  const qty = item.quantity || 1
  if (item.unit_price == null) {
    item.unit_price = parseMoney(item.subtotal) / qty || 0
  }
  item.quantity = qty
  item.subtotal = (parseMoney(item.unit_price) * qty).toFixed(2)
  return item
}

function cartSummary(cart) {
  const list = (cart || []).map(normalizeItem)
  const count = list.reduce((s, i) => s + (i.quantity || 1), 0)
  const total = list.reduce((s, i) => s + parseMoney(i.subtotal), 0)
  return { count, total: total.toFixed(2), list }
}

module.exports = {
  calcUnitPrice,
  buildOptionLabels,
  collectOptionIds,
  normalizeItem,
  cartSummary
}
