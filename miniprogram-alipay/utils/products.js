function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
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

/** 读本地菜单缓存（弱网兜底渲染）。无缓存返回 null。 */
function readCachedMenu(storeId) {
  try {
    const res = my.getStorageSync({ key: menuCacheKey(storeId) })
    return (res && res.data) || null
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
  const data = await api.get('/miniprogram/menu', params, false)
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
    const bundle = await fetchMenuBundle(api, sid, cached && cached.version)
    if (bundle && bundle.changed === false) {
      if (cachedItems.length) {
        return { items: cachedItems, categories: cachedCategories, complete: true }
      }
      // changed:false 但本地无缓存：继续走下方完整拉取（勿当失败）
    } else if (bundle && bundle.products != null) {
      // 后端 products 为 { items, total }，不是数组；须用 normalizeList
      const items = normalizeList(bundle.products)
      const categories = normalizeList(bundle.categories)
      writeCachedMenu(sid, {
        version: bundle.version,
        categories,
        products: items
      })
      if (items.length) {
        return { items, categories, complete: true }
      }
    }
  } catch (_) {}
  try {
    const prod = await fetchAllProducts(api, { store_id: sid }, false)
    let categories = []
    try {
      const catRes = await api.get('/categories', { store_id: sid }, false)
      categories = normalizeList(catRes)
    } catch (_) {}
    const items = (prod && prod.items) || []
    if (items.length) {
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
  menuCacheKey
}
