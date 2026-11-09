/** H5 购物车规格计价（与小程序 cart.js 逻辑一致） */

/** 金额 → 分（整数），避免 float 累加误差 */
function toFen(v: unknown): number {
  if (v == null || v === '') return 0
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return 0
    return Math.round(v * 100)
  }
  const s = String(v).trim().replace(/,/g, '')
  const m = s.match(/^([+-]?)(\d+)(?:\.(\d*))?$/)
  if (!m) {
    const n = Number(s)
    return Number.isFinite(n) ? Math.round(n * 100) : 0
  }
  const sign = m[1] === '-' ? -1 : 1
  const yuan = parseInt(m[2], 10) || 0
  const frac = (m[3] || '').padEnd(2, '0').slice(0, 2)
  return sign * (yuan * 100 + (parseInt(frac, 10) || 0))
}

function fromFen(fen: number): string {
  const n = Math.trunc(Number(fen) || 0)
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

function parseMoney(v: unknown): number {
  return toFen(v) / 100
}

function findOption(group: any, optionId: number | string) {
  return (group.options || []).find((o: any) => String(o.id) === String(optionId))
}

export function calcUnitPrice(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): number {
  let fen = toFen(product.price)
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      const opt = sid != null ? findOption(g, sid) : null
      if (opt) fen += toFen(opt.extra_price)
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) {
        const opt = findOption(g, oid)
        if (opt) fen += toFen(opt.extra_price)
      }
    }
  }
  return Number(fromFen(fen))
}

export function buildOptionLabels(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): string[] {
  const labels: string[] = []
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      const opt = sid != null ? findOption(g, sid) : null
      if (opt && opt.code !== 'NORMAL') labels.push(opt.name)
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) {
        const opt = findOption(g, oid)
        if (opt) labels.push(opt.name)
      }
    }
  }
  return labels
}

export function collectOptionIds(
  product: any,
  selected: Record<number, number>,
  multi: Record<number, number[]>,
): number[] {
  const ids: number[] = []
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'SINGLE') {
      const sid = selected[g.id]
      if (sid != null && sid !== ('' as unknown as number)) ids.push(Number(sid))
    }
    if (g.selection_type === 'MULTIPLE') {
      for (const oid of multi[g.id] || []) ids.push(Number(oid))
    }
  }
  return ids.filter((id) => Number.isFinite(id) && id > 0)
}

function itemKey(item: any) {
  const ids = (item.option_ids || []).slice().sort((a: number, b: number) => a - b).join(',')
  return `${item.product_id}:${ids}`
}

export function mergeCartItem(cart: any[], entry: any) {
  const list = [...(cart || [])]
  const key = itemKey(entry)
  const parsedAdd = Number(entry?.quantity)
  const add = Number.isFinite(parsedAdd) ? Math.max(1, Math.min(999, Math.floor(parsedAdd))) : 1
  const idx = list.findIndex((x) => itemKey(x) === key)
  if (idx >= 0) {
    const cur = { ...list[idx] }
    const parsedCurrent = Number(cur.quantity)
    const current = Number.isFinite(parsedCurrent) ? Math.max(1, Math.floor(parsedCurrent)) : 1
    cur.quantity = Math.min(999, current + add)
    cur.subtotal = fromFen(toFen(cur.unit_price) * cur.quantity)
    list[idx] = cur
  } else {
    list.push({ ...entry, quantity: add })
  }
  return list
}

/** 安全读取 H5 购物车；损坏或非数组数据会清理，避免页面初始化白屏。 */
export function readCartStorage(): any[] {
  try {
    const raw = localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]'
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new TypeError('购物车数据格式错误')
    return parsed
  } catch {
    try {
      localStorage.removeItem('snh_cart')
      localStorage.removeItem('cart')
    } catch {
      /* ignore storage failures */
    }
    return []
  }
}

/** 退出登录时清理顾客购物车与桌台上下文，避免跨账号残留。 */
export function clearCartStorage() {
  for (const key of [
    'snh_cart', 'cart', 'snh_cart_store_id', 'snh_table_token', 'table_token',
    'snh_table', 'snh_store_id', 'snh_channel', 'channel', 'member_prompt_done',
    'h5_login_code', 'nickname',
  ]) {
    try { localStorage.removeItem(key) } catch { /* ignore */ }
  }
}

export function sortPrefsForApply(prefs: any[]) {
  return [...(prefs || [])].sort((a, b) => (b.order_count || 0) - (a.order_count || 0))
}

function bestPrefForGroup(prefs: any[], group: any) {
  const matches = (prefs || []).filter(
    (pr) => pr.category === group.name || pr.category === group.code,
  )
  if (!matches.length) return null
  return matches.sort((a, b) => (b.order_count || 0) - (a.order_count || 0))[0]
}

export function applyPreferencesToProduct(
  product: any,
  prefs: any[],
  selected: Record<number, number>,
  multi: Record<number, number[]>,
) {
  let applied = 0
  for (const g of product.option_groups || []) {
    if (g.selection_type === 'MULTIPLE') {
      const matches = (prefs || []).filter(
        (pr) => pr.category === g.name || pr.category === g.code,
      )
      const ids: number[] = []
      matches.forEach((match) => {
        const opt = (g.options || []).find((o: any) => o.code === match.option_code)
        if (opt) ids.push(opt.id)
      })
      if (ids.length) {
        multi[g.id] = ids
        applied += ids.length
      }
      continue
    }
    const match = bestPrefForGroup(prefs, g)
    if (!match) continue
    const opt = (g.options || []).find((o: any) => o.code === match.option_code)
    if (!opt) continue
    selected[g.id] = opt.id
    applied += 1
  }
  return applied
}

function normalizeRequireCategoryIds(raw: unknown): number[] {
  if (!Array.isArray(raw)) return []
  const out: number[] = []
  const seen = new Set<number>()
  for (const x of raw) {
    const id = Number(x)
    if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue
    seen.add(id)
    out.push(id)
  }
  return out
}

function productMap(products: any[]) {
  const map: Record<string, any> = {}
  for (const p of products || []) {
    if (!p) continue
    map[p.id] = p
    map[String(p.id)] = p
  }
  return map
}

function categoryMap(categories: any[]) {
  const map: Record<string, any> = {}
  for (const c of categories || []) {
    if (!c) continue
    map[c.id] = c
    map[String(c.id)] = c
  }
  return map
}

function resolveProductRequires(product: any, categories?: any[]) {
  if (!product) return { cats: [] as number[], prods: [] as number[] }
  let cats = normalizeRequireCategoryIds(product.require_category_ids)
  let prods = normalizeRequireCategoryIds(product.require_product_ids)
  if (!cats.length && !prods.length) {
    const cat = categoryMap(categories || [])[product.category_id]
    cats = normalizeRequireCategoryIds(cat?.require_category_ids)
  }
  return { cats, prods }
}

function cartCategoryIds(cart: any[], products?: any[]) {
  const map = productMap(products || [])
  const ids = new Set<number>()
  for (const raw of cart || []) {
    let cid = raw?.category_id != null ? Number(raw.category_id) : NaN
    if (!Number.isFinite(cid) || cid <= 0) {
      const p = map[raw?.product_id]
      cid = p?.category_id != null ? Number(p.category_id) : NaN
    }
    if (Number.isFinite(cid) && cid > 0) ids.add(cid)
  }
  return ids
}

function cartProductIds(cart: any[]) {
  const ids = new Set<number>()
  for (const raw of cart || []) {
    const pid = Number(raw?.product_id)
    if (Number.isFinite(pid) && pid > 0) ids.add(pid)
  }
  return ids
}

/** 检查加入餐品是否满足购买依赖（分类 OR 指定餐品） */
export function checkCategoryRequire(
  product: any,
  cart: any[],
  products?: any[],
  categories?: any[],
): { ok: boolean; message: string } {
  const { cats: requireCats, prods: requireProds } = resolveProductRequires(product, categories)
  if (!requireCats.length && !requireProds.length) return { ok: true, message: '' }
  const haveCats = cartCategoryIds(cart, products)
  const haveProds = cartProductIds(cart)
  if (requireCats.some((id) => haveCats.has(id))) return { ok: true, message: '' }
  if (requireProds.some((id) => haveProds.has(id))) return { ok: true, message: '' }
  const cats = categoryMap(categories || [])
  const pmap = productMap(products || [])
  const labels: string[] = []
  requireCats.forEach((id) => labels.push(`「${cats[id]?.name || `分类#${id}`}」类`))
  requireProds.forEach((id) => labels.push(`「${pmap[id]?.name || `餐品#${id}`}」`))
  const name = product?.name || '该餐品'
  return {
    ok: false,
    message: `请先点${labels.join('或')}，再点「${name}」`,
  }
}

/** 整单校验购买依赖（结算前） */
export function validateCategoryRequires(cart: any[], products?: any[], categories?: any[]) {
  const map = productMap(products || [])
  const issues: { name: string; message: string }[] = []
  for (const raw of cart || []) {
    const p = map[raw?.product_id] || {
      id: raw?.product_id,
      name: raw?.name,
      category_id: raw?.category_id,
      require_category_ids: raw?.require_category_ids,
      require_product_ids: raw?.require_product_ids,
    }
    const r = checkCategoryRequire(p, cart, products, categories)
    if (!r.ok) issues.push({ name: p.name || raw?.name, message: r.message })
  }
  return issues
}
