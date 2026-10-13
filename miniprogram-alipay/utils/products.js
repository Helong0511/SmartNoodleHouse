function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

/**
 * 业务「今日」日历日 YYYY-MM-DD：固定北京时间（Asia/Shanghai），
 * 与后端 visibility_day / now_local 对齐，不跟手机系统时区。
 */
function localVisibilityDay() {
  try {
    // en-CA → YYYY-MM-DD
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).format(new Date())
  } catch (_) {
    const d = new Date(Date.now() + 8 * 3600 * 1000)
    return d.toISOString().slice(0, 10)
  }
}

/**
 * 分页拉齐门店在售商品。
 * 单页最多 200；某一页失败或重复时返回已拿到的部分，并标 complete:false，
 * 调用方不得把「没拉到」当成下架。
 */
async function fetchAllProducts(api, query, needAuth) {
  const q = query && typeof query === 'object' ? { ...query } : {}
  const pageSize = Math.min(Math.max(1, Number(q.page_size) || 200), 200)
  delete q.page
  q.page_size = pageSize
  let page = 1
  let items = []
  let total = 0
  let totalPages = 1
  let complete = true
  const seen = {}
  const auth = needAuth === undefined ? false : needAuth
  do {
    let data
    try {
      data = await api.get('/products', { ...q, page }, auth)
    } catch (_) {
      complete = false
      break
    }
    const batch = (data && Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : [])) || []
    const reported = data && data.total != null ? Number(data.total) : NaN
    total = Number.isFinite(reported) ? reported : (items.length + batch.length)
    totalPages = Math.max(1, Number((data && data.total_pages) || Math.ceil((total || 0) / pageSize) || 1))
    if (!batch.length) {
      if (items.length < total) complete = false
      break
    }
    let fresh = 0
    batch.forEach((p) => {
      const key = p && p.id != null ? String(p.id) : ''
      if (!key || seen[key]) return
      seen[key] = true
      fresh += 1
      items.push(p)
    })
    if (!fresh) {
      complete = items.length >= total
      break
    }
    page += 1
  } while (page <= totalPages && page <= 50 && items.length < total)
  if (total > items.length) complete = false
  return {
    items,
    total: total || items.length,
    page_size: pageSize,
    total_pages: totalPages,
    complete
  }
}

const MENU_CACHE_PREFIX = 'menu_bundle:'

function menuCacheKey(storeId) {
  return `${MENU_CACHE_PREFIX}${storeId}`
}

/** 读本地菜单缓存（弱网兜底渲染）。跨日或无缓存返回 null。 */
function readCachedMenu(storeId) {
  try {
    const res = my.getStorageSync({ key: menuCacheKey(storeId) })
    const cached = (res && res.data) || null
    if (!cached) return null
    const day = cached.visibility_day || ''
    if (day && day !== localVisibilityDay()) return null
    return cached
  } catch (_) {
    return null
  }
}

function writeCachedMenu(storeId, bundle) {
  try {
    my.setStorageSync({
      key: menuCacheKey(storeId),
      data: {
        version: bundle.version,
        visibility_day: bundle.visibility_day || localVisibilityDay(),
        categories: bundle.categories,
        products: bundle.products,
        cached_at: Date.now()
      }
    })
  } catch (_) {}
}

/**
 * 菜单聚合 + 版本戳校验（Task 12）。
 * 返回 { changed, version, categories?, products? }；
 * changed=false 表示服务端菜单未变化，调用方应保留本地缓存不重绘。
 */
async function fetchMenuBundle(api, storeId, version) {
  const params = { store_id: storeId }
  if (version) params.version = version
  // 有顾客 token 时带上，便于下发生日专属菜；无 token 仍可匿名拉菜单
  let needAuth = false
  try {
    const auth = require('./auth')
    needAuth = !!(auth.getToken && auth.getToken())
  } catch (_) { }
  const data = await api.get('/miniprogram/menu', params, needAuth)
  return data || { changed: true }
}

/**
 * 点餐用的完整菜单。优先一次拿聚合菜单（不分页），失败再分页，再失败用本地缓存。
 * complete=false 表示这份列表不能用来判断「不在菜单」。
 * options.preferCache=true 时若本地有商品则直接返回，不打网。
 */
async function loadStoreCatalog(api, storeId, options) {
  const sid = Number(storeId)
  if (!sid) return { items: [], categories: [], complete: false }
  const cached = readCachedMenu(sid)
  const cachedItems = normalizeList(cached && cached.products)
  const cachedCategories = normalizeList(cached && cached.categories)
  if (options && options.preferCache && cachedItems.length) {
    return { items: cachedItems, categories: cachedCategories, complete: true }
  }
  try {
    // 跨日缓存已在 readCachedMenu 作废，此处仅同日才带 version 短路
    let bundle = await fetchMenuBundle(api, sid, cached && cached.version)
    if (bundle && bundle.changed === false) {
      const sameDay =
        !bundle.visibility_day ||
        bundle.visibility_day === localVisibilityDay()
      if (cachedItems.length && sameDay) {
        return { items: cachedItems, categories: cachedCategories, complete: true }
      }
      // changed:false 但跨日/无本地：强制全量，勿直接掉到分页兜底
      bundle = await fetchMenuBundle(api, sid, null)
    }
    if (bundle && bundle.changed !== false && bundle.products != null) {
      // 后端 products 为 { items, total }，不是数组；须用 normalizeList
      const items = normalizeList(bundle.products)
      const categories = normalizeList(bundle.categories)
      writeCachedMenu(sid, {
        version: bundle.version,
        visibility_day: bundle.visibility_day || localVisibilityDay(),
        categories,
        products: items
      })
      if (items.length) {
        return { items, categories, complete: true }
      }
    }
  } catch (_) {}
  try {
    let needAuth = false
    try {
      const auth = require('./auth')
      needAuth = !!(auth.getToken && auth.getToken())
    } catch (_) { }
    const prod = await fetchAllProducts(api, { store_id: sid }, needAuth)
    let categories = []
    try {
      const catRes = await api.get('/categories', { store_id: sid }, false)
      categories = normalizeList(catRes)
    } catch (_) {}
    const items = (prod && prod.items) || []
    if (items.length) {
      // 有商品列表即可用于规格校验；complete 仅作参考
      return { items, categories, complete: true }
    }
  } catch (_) {}
  const fallback = readCachedMenu(sid)
  const fallbackItems = normalizeList(fallback && fallback.products)
  if (fallbackItems.length) {
    return { items: fallbackItems, categories: normalizeList(fallback.categories), complete: true }
  }
  return { items: [], categories: [], complete: false }
}

module.exports = {
  fetchAllProducts,
  fetchMenuBundle,
  loadStoreCatalog,
  readCachedMenu,
  writeCachedMenu,
  menuCacheKey,
  localVisibilityDay
}
