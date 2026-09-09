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

module.exports = { fetchAllProducts }
