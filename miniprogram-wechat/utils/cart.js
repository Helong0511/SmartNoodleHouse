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

function itemKey(item) {
  const ids = (item.option_ids || []).slice().sort((a, b) => a - b).join(',')
  return `${item.product_id}:${ids}`
}

/** 合并同商品同规格，返回新数组 */
function mergeCartItem(cart, entry) {
  const list = [...(cart || [])]
  const key = itemKey(entry)
  const idx = list.findIndex((x) => itemKey(x) === key)
  if (idx >= 0) {
    const cur = normalizeItem(list[idx])
    const add = entry.quantity || 1
    cur.quantity = (cur.quantity || 1) + add
    cur.subtotal = (parseMoney(cur.unit_price) * cur.quantity).toFixed(2)
    if (entry.no_member_discount != null) cur.no_member_discount = !!entry.no_member_discount
    list[idx] = cur
  } else {
    list.push(normalizeItem({ ...entry }))
  }
  return list
}

/**
 * 对照当前门店菜单校验购物车条目。
 * @returns {{ list, invalidCount, validCount }}
 */
function reconcileWithProducts(cart, products) {
  const map = {}
  ;(products || []).forEach((p) => {
    map[p.id] = p
    map[String(p.id)] = p
  })

  let invalidCount = 0
  const list = (cart || []).map((raw) => {
    const item = normalizeItem({ ...raw })
    const p = map[item.product_id]
    let invalidReason = ''

    if (!p) {
      invalidReason = '已下架或不在本店菜单'
    } else if (p.status && p.status !== 'ON_SALE') {
      invalidReason = '暂不可售'
    } else {
      const stock = parseInt(p.stock, 10)
      const qty = item.quantity || 1
      if (Number.isFinite(stock) && stock >= 0 && stock < qty) {
        invalidReason = stock === 0 ? '已售罄' : `仅剩 ${stock} 份`
      }
    }

    if (invalidReason) invalidCount += 1
    return {
      ...item,
      no_member_discount: p ? !!p.no_member_discount : !!item.no_member_discount,
      invalid: !!invalidReason,
      invalidReason
    }
  })

  return {
    list,
    invalidCount,
    validCount: list.length - invalidCount
  }
}

function calcDiscountableSubtotal(cart) {
  return (cart || []).reduce((s, raw) => {
    const item = normalizeItem({ ...raw })
    if (item.no_member_discount) return s
    return s + parseMoney(item.subtotal)
  }, 0)
}

/** 会员折后可用于优惠券的商品小计（不含特价） */
function calcCouponEligibleSubtotal(cart, memberDiscount) {
  const discountable = calcDiscountableSubtotal(cart)
  const eligible = discountable - parseMoney(memberDiscount)
  return Math.max(0, Math.round(eligible * 100) / 100)
}

/** 可用于代金券门槛/面额判断的基数（不含特价商品小计，可选含打包费） */
function calcVoucherEligiblePayable(couponEligible, couponDiscount, packingFee, needPack) {
  let base = Math.max(0, parseMoney(couponEligible) - parseMoney(couponDiscount))
  if (needPack) base += parseMoney(packingFee)
  return Math.round(base * 100) / 100
}

function enrichCartFromProducts(list, products) {
  const map = {}
  ;(products || []).forEach((p) => {
    map[p.id] = p
    map[String(p.id)] = p
  })
  return (list || []).map((raw) => {
    const item = normalizeItem({ ...raw })
    const p = map[item.product_id]
    if (p) item.no_member_discount = !!p.no_member_discount
    return item
  })
}

module.exports = {
  calcUnitPrice,
  buildOptionLabels,
  collectOptionIds,
  normalizeItem,
  cartSummary,
  itemKey,
  mergeCartItem,
  reconcileWithProducts,
  calcDiscountableSubtotal,
  calcCouponEligibleSubtotal,
  calcVoucherEligiblePayable,
  enrichCartFromProducts
}
