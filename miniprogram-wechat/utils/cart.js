/** 购物车计价与展示辅助 */

/** 金额 → 分（整数），避免 float 累加误差 */
function toFen(v) {
  if (v == null || v === '') return 0
  const s = String(v).trim().replace(/,/g, '')
  const m = s.match(/^([+-]?)(\d+)(?:\.(\d*))?$/)
  if (!m) {
    const n = Number(s)
    return Number.isFinite(n) ? Math.round(n * 100) : 0
  }
  const sign = m[1] === '-' ? -1 : 1
  const yuan = parseInt(m[2], 10) || 0
  const fraction = m[3] || ''
  const cents = parseInt(fraction.padEnd(2, '0').slice(0, 2), 10) || 0
  // 字符串与数字统一按第三位小数四舍五入，避免 "0.005" 被截断成 0 分。
  const roundUp = fraction.length > 2 && fraction.charAt(2) >= '5' ? 1 : 0
  return sign * (yuan * 100 + cents + roundUp)
}

function fromFen(fen) {
  const n = Math.trunc(Number(fen) || 0)
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

/** 兼容展示：返回元（两位小数数值，由分还原） */
function parseMoney(v) {
  return toFen(v) / 100
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

/** 根据所选规格计算单价（基础价 + 加价，分整数再还原） */
function calcUnitPrice(product, selected, multi) {
  let fen = toFen(product.price)
  ;(product.option_groups || []).forEach(g => {
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      const opt = sid != null ? findOption(g, sid) : null
      if (opt) fen += toFen(opt.extra_price)
    }
    if (g.selection_type === 'MULTIPLE') {
      ;(pickSelected(multi, g.id) || []).forEach(oid => {
        const opt = findOption(g, oid)
        if (opt) fen += toFen(opt.extra_price)
      })
    }
  })
  return Number(fromFen(fen))
}

const OPTION_TEXT_SEP = '、'

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

/** 根据已存 option_ids 还原规格名称（购物车刷新时与微信展示一致） */
function buildOptionLabelsFromIds(product, optionIds) {
  if (!product || !optionIds || !optionIds.length) return []
  const selectedSet = new Set(optionIds.map((id) => String(id)))
  const labels = []
  ;(product.option_groups || []).forEach((g) => {
    if (g.selection_type === 'SINGLE') {
      const opt = (g.options || []).find((o) => selectedSet.has(String(o.id)))
      if (opt && opt.code !== 'NORMAL') labels.push(opt.name)
    }
    if (g.selection_type === 'MULTIPLE') {
      ;(g.options || []).forEach((o) => {
        if (selectedSet.has(String(o.id))) labels.push(o.name)
      })
    }
  })
  return labels
}

function normalizeOptionText(text) {
  if (!text) return ''
  return String(text)
    .replace(/[,，;；]\s*/g, OPTION_TEXT_SEP)
    .replace(new RegExp(`${OPTION_TEXT_SEP}+`, 'g'), OPTION_TEXT_SEP)
    .replace(new RegExp(`^${OPTION_TEXT_SEP}|${OPTION_TEXT_SEP}$`, 'g'), '')
}

/** 统一购物车规格展示字段（option_labels + option_text） */
function resolveOptionDisplay(item, product) {
  const rebuilt = product ? buildOptionLabelsFromIds(product, item.option_ids) : []
  let labels = rebuilt.length
    ? rebuilt
    : (Array.isArray(item.option_labels) ? item.option_labels.filter(Boolean) : [])
  if (!labels.length && item.option_text) {
    labels = normalizeOptionText(item.option_text).split(OPTION_TEXT_SEP).filter(Boolean)
  }
  const option_text = labels.length
    ? labels.join(OPTION_TEXT_SEP)
    : normalizeOptionText(item.option_text || '')
  return { option_labels: labels, option_text }
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
  // 购物车持久化数据可能被篡改；数量统一限制为 1..999 的整数。
  const parsedQty = Number(item.quantity)
  const qty = Number.isFinite(parsedQty) ? Math.max(1, Math.min(999, Math.floor(parsedQty))) : 1
  if (item.unit_price == null) {
    const unitFen = qty > 0 ? Math.trunc(toFen(item.subtotal) / qty) : 0
    item.unit_price = Number(fromFen(unitFen))
  }
  item.quantity = qty
  item.subtotal = fromFen(toFen(item.unit_price) * qty)
  return item
}

function cartSummary(cart) {
  const list = (cart || []).map(normalizeItem)
  const count = list.reduce((s, i) => s + (i.quantity || 1), 0)
  const totalFen = list.reduce((s, i) => s + toFen(i.subtotal), 0)
  return { count, total: fromFen(totalFen), list }
}

function itemKey(item) {
  const ids = (item.option_ids || []).slice().sort((a, b) => a - b).join(',')
  return `${item.product_id}:${ids}`
}

/** 合并同餐品同规格，返回新数组 */
function mergeCartItem(cart, entry) {
  const list = [...(cart || [])]
  const key = itemKey(entry)
  const idx = list.findIndex((x) => itemKey(x) === key)
  if (idx >= 0) {
    const cur = normalizeItem(list[idx])
    const parsedAdd = Number(entry.quantity)
    const add = Number.isFinite(parsedAdd) ? Math.max(1, Math.min(999, Math.floor(parsedAdd))) : 1
    cur.quantity = Math.min(999, (cur.quantity || 1) + add)
    cur.subtotal = fromFen(toFen(cur.unit_price) * cur.quantity)
    if (entry.no_member_discount != null) cur.no_member_discount = !!entry.no_member_discount
    if (!cur.imageUrl && entry.imageUrl) cur.imageUrl = entry.imageUrl
    if (!cur.main_image && entry.main_image) cur.main_image = entry.main_image
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
function reconcileWithProducts(cart, products, categories, options) {
  const complete = !options || options.complete !== false
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
      // 菜单没拉全时不能把「这页没有」当成下架，否则超过分页后整单变 0 元
      if (complete) invalidReason = '已下架或不在本店菜单'
    } else if (p.status && p.status !== 'ON_SALE') {
      invalidReason = '暂不可售'
    } else {
      const stock = parseInt(p.stock, 10)
      const qty = item.quantity || 1
      if (Number.isFinite(stock) && stock >= 0 && stock < qty) {
        invalidReason = stock === 0 ? '已售罄' : `仅剩 ${stock} 份`
      }
    }

    if (!invalidReason && p) {
      const req = checkCategoryRequire(p, cart, products, categories)
      if (!req.ok) invalidReason = req.message
    }

    if (invalidReason) invalidCount += 1
    const display = resolveOptionDisplay(item, p)
    return {
      ...item,
      category_id: (p && p.category_id) || item.category_id,
      require_category_ids: (p && p.require_category_ids) || item.require_category_ids || [],
      require_product_ids: (p && p.require_product_ids) || item.require_product_ids || [],
      option_labels: display.option_labels,
      option_text: display.option_text,
      main_image: (p && p.main_image) || item.main_image || '',
      imageUrl: item.imageUrl || '',
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
  const fen = (cart || []).reduce((s, raw) => {
    const item = normalizeItem({ ...raw })
    if (item.no_member_discount) return s
    return s + toFen(item.subtotal)
  }, 0)
  return Number(fromFen(fen))
}

/** 会员折后可用于优惠券的餐品小计（不含特价） */
function calcCouponEligibleSubtotal(cart, memberDiscount) {
  const eligibleFen = Math.max(0, toFen(calcDiscountableSubtotal(cart)) - toFen(memberDiscount))
  return Number(fromFen(eligibleFen))
}

/** 可用于代金券门槛/面额判断的基数（不含特价餐品小计，可选含打包费） */
function calcVoucherEligiblePayable(couponEligible, couponDiscount, packingFee, needPack) {
  let fen = Math.max(0, toFen(couponEligible) - toFen(couponDiscount))
  if (needPack) fen += toFen(packingFee)
  return Number(fromFen(fen))
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
    if (p) {
      item.no_member_discount = !!p.no_member_discount
      if (p.main_image) item.main_image = p.main_image
      // 优先用餐品主图路径（稳定），避免购物车里过期的临时本地路径
      if (p.main_image) item._productUpdatedAt = p.updated_at || ''
    }
    return item
  })
}

/** 列表/详情：是否可售（下架或库存 0 为不可售；999 等正库存视为可售） */
function productAvailability(p) {
  if (!p) return { available: false, soldOut: false, label: '暂不可售' }
  if (p.status && String(p.status) !== 'ON_SALE') {
    return { available: false, soldOut: false, label: '暂不可售' }
  }
  const stock = parseInt(p.stock, 10)
  if (Number.isFinite(stock) && stock === 0) {
    return { available: false, soldOut: true, label: '已售罄' }
  }
  return { available: true, soldOut: false, label: '' }
}

/** 详情页选规格：返回尚未选齐的必选规格组名称 */
function missingRequiredOptionGroups(product, selected, multi) {
  const missing = []
  const groups = (product && product.option_groups) || []
  groups.forEach((g) => {
    if (!g || !g.is_required) return
    if (g.selection_type === 'SINGLE') {
      const sid = pickSelected(selected, g.id)
      if (sid == null || sid === '') missing.push(g.name || '规格')
      return
    }
    const ids = pickSelected(multi, g.id) || []
    if (!ids.length) missing.push(g.name || '规格')
  })
  return missing
}

/** 检查购物车是否满足餐品必选规格（与后端下单校验一致） */
function validateRequiredOptions(cart, products) {
  const map = {}
  ;(products || []).forEach((p) => {
    map[p.id] = p
    map[String(p.id)] = p
  })
  const issues = []
  ;(cart || []).forEach((raw) => {
    const item = normalizeItem({ ...raw })
    const p = map[item.product_id]
    if (!p) return
    const selected = new Set((item.option_ids || []).map((id) => String(id)))
    const groups = p.option_groups || []
    if (!groups.length) {
      // 菜单聚合/列表接口只有 has_options，无规格组明细：有规格却未选时仍应拦截
      if (p.has_options && !selected.size) {
        issues.push({ name: p.name, group: '规格' })
      }
      return
    }
    groups.forEach((g) => {
      if (!g.is_required) return
      const ok = (g.options || []).some((o) => selected.has(String(o.id)))
      if (!ok) {
        issues.push({ name: p.name, group: g.name || '规格' })
      }
    })
  })
  return issues
}

function normalizeRequireCategoryIds(raw) {
  if (!raw) return []
  if (!Array.isArray(raw)) return []
  const out = []
  const seen = {}
  raw.forEach((x) => {
    const id = Number(x)
    if (!Number.isFinite(id) || id <= 0 || seen[id]) return
    seen[id] = true
    out.push(id)
  })
  return out
}

function productMap(products) {
  const map = {}
  ;(products || []).forEach((p) => {
    if (!p) return
    map[p.id] = p
    map[String(p.id)] = p
  })
  return map
}

function categoryMap(categories) {
  const map = {}
  ;(categories || []).forEach((c) => {
    if (!c) return
    map[c.id] = c
    map[String(c.id)] = c
  })
  return map
}

/** 有效依赖：餐品已带 effective 字段；若皆空则回退分类默认 */
function resolveProductRequires(product, categories) {
  if (!product) return { cats: [], prods: [] }
  let cats = normalizeRequireCategoryIds(product.require_category_ids)
  let prods = normalizeRequireCategoryIds(product.require_product_ids)
  if (!cats.length && !prods.length) {
    const cat = categoryMap(categories)[product.category_id]
    cats = normalizeRequireCategoryIds(cat && cat.require_category_ids)
  }
  return { cats, prods }
}

function cartCategoryIds(cart, products) {
  const map = productMap(products)
  const ids = {}
  ;(cart || []).forEach((raw) => {
    const item = normalizeItem({ ...raw })
    let cid = item.category_id != null ? Number(item.category_id) : NaN
    if (!Number.isFinite(cid) || cid <= 0) {
      const p = map[item.product_id]
      cid = p && p.category_id != null ? Number(p.category_id) : NaN
    }
    if (Number.isFinite(cid) && cid > 0) ids[cid] = true
  })
  return ids
}

function cartProductIds(cart) {
  const ids = {}
  ;(cart || []).forEach((raw) => {
    const pid = Number(raw && raw.product_id)
    if (Number.isFinite(pid) && pid > 0) ids[pid] = true
  })
  return ids
}

/**
 * 检查加入餐品是否满足购买依赖（分类 OR 指定餐品）。
 * @returns {{ ok: boolean, message: string }}
 */
function checkCategoryRequire(product, cart, products, categories) {
  const { cats: requireCats, prods: requireProds } = resolveProductRequires(product, categories)
  if (!requireCats.length && !requireProds.length) return { ok: true, message: '' }
  const haveCats = cartCategoryIds(cart, products)
  const haveProds = cartProductIds(cart)
  const pmap = productMap(products)
  if (requireCats.some((id) => haveCats[id])) return { ok: true, message: '' }
  if (requireProds.some((id) => haveProds[id])) return { ok: true, message: '' }
  const cmap = categoryMap(categories)
  const labels = []
  requireCats.forEach((id) => labels.push(`「${(cmap[id] && cmap[id].name) || ('分类#' + id)}」类`))
  requireProds.forEach((id) => {
    const p = pmap[id]
    labels.push(`「${(p && p.name) || ('餐品#' + id)}」`)
  })
  const name = (product && product.name) || '该餐品'
  return {
    ok: false,
    message: `请先点${labels.join('或')}，再点「${name}」`
  }
}

/** 整单校验购买依赖（结算前） */
function validateCategoryRequires(cart, products, categories) {
  const map = productMap(products)
  const issues = []
  ;(cart || []).forEach((raw) => {
    const item = normalizeItem({ ...raw })
    const p = map[item.product_id] || {
      id: item.product_id,
      name: item.name,
      category_id: item.category_id,
      require_category_ids: item.require_category_ids,
      require_product_ids: item.require_product_ids
    }
    const r = checkCategoryRequire(p, cart, products, categories)
    if (!r.ok) {
      issues.push({ name: p.name || item.name, message: r.message })
    }
  })
  return issues
}

module.exports = {
  calcUnitPrice,
  buildOptionLabels,
  buildOptionLabelsFromIds,
  resolveOptionDisplay,
  collectOptionIds,
  normalizeItem,
  cartSummary,
  itemKey,
  mergeCartItem,
  reconcileWithProducts,
  calcDiscountableSubtotal,
  calcCouponEligibleSubtotal,
  calcVoucherEligiblePayable,
  enrichCartFromProducts,
  productAvailability,
  missingRequiredOptionGroups,
  validateRequiredOptions,
  checkCategoryRequire,
  validateCategoryRequires
}
