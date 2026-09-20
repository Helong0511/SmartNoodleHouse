/** 分页拉齐门店在售商品，避免单页 page_size 截断菜单。 */
async function fetchAllProducts(api, query, needAuth) {
  const q = query && typeof query === 'object' ? { ...query } : {}
  const pageSize = Math.min(Math.max(1, Number(q.page_size) || 100), 200)
  delete q.page
  q.page_size = pageSize
  let page = 1
  let items = []
  let total = 0
  let totalPages = 1
  const auth = needAuth === undefined ? false : needAuth
  do {
    const data = await api.get('/products', { ...q, page }, auth)
    const batch = (data && Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : [])) || []
    total = Number((data && data.total) != null ? data.total : batch.length) || 0
    totalPages = Math.max(1, Number((data && data.total_pages) || Math.ceil(total / pageSize) || 1))
    items = items.concat(batch)
    page += 1
  } while (page <= totalPages && page <= 20 && items.length < total)
  return { items, total: total || items.length, page_size: pageSize, total_pages: totalPages }
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

module.exports = {
  fetchAllProducts,
  fetchMenuBundle,
  readCachedMenu,
  writeCachedMenu,
  menuCacheKey
}
