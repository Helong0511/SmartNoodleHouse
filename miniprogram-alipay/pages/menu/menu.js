const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const dineCtx = require('../../utils/context')
const app = getApp()

const MENU_CACHE_MS = 5 * 60 * 1000

function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

Page({
  data: {
    categories: [],
    cartCount: 0,
    cartTotal: '0.00',
    isWide: false,
    prefSummary: '',
    activeCat: null,
    scrollInto: '',
    keyword: '',
    loading: true,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: '',
    hasTable: false
  },

  onShow() {
    this.refreshCart()
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    this.loadPrefs()
    this.loadMenu()
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onPullDownRefresh() {
    this._menuCache = null
    Promise.all([
      dineCtx.ensureDineContext({ force: true }).then((ctx) => this.applyDineContext(ctx)),
      this.loadPrefs(true),
      this.loadMenu()
    ]).finally(() => my.stopPullDownRefresh())
  },

  scanTable() {
    dineCtx.scanAndBindTable().then((ctx) => {
      if (ctx) {
        this.applyDineContext(ctx)
        this._menuCache = null
        this.loadMenu()
      }
    })
  },

  async loadPrefs(force = false) {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
    } catch (_) {}
    try {
      const view = await prefUtil.loadCustomerPrefs(app, api, { force })
      this.setData({ prefSummary: (view && view.prefSummary) || '' })
    } catch (_) {}
  },

  buildCategories(cats, items, metrics, keyword) {
    const m = metrics || media.layoutMetrics()
    const kw = (keyword || '').trim().toLowerCase()
    const catList = normalizeList(cats)
    const productList = normalizeList(items)
    return catList
      .map((c) => {
        if (!c) return null
        const products = productList
          .filter((p) => p && p.category_id === c.id)
          .filter((p) => {
            if (!kw) return true
            return (
              String(p.name || '').toLowerCase().includes(kw)
              || String(p.description || '').toLowerCase().includes(kw)
            )
          })
          .map((p) => media.decorateProduct(p, m))
        return { ...c, products }
      })
      .filter(Boolean)
      .filter((c) => c.products.length > 0 || !kw)
  },

  async loadMenu() {
    const sid = (app.globalData.table && app.globalData.table.store_id)
      || (app.globalData.store && app.globalData.store.id)
      || null
    const cacheKey = String(sid || 'all')
    const now = Date.now()
    this.setData({ loading: true })
    try {
      if (
        this._menuCache
        && this._menuCache.key === cacheKey
        && now - this._menuCache.at < MENU_CACHE_MS
        && !this.data.keyword
      ) {
        const { cats, items, metrics } = this._menuCache
        const categories = this.buildCategories(cats, items, metrics, '')
        this.applyCategories(categories, metrics)
        return
      }
      const metrics = media.layoutMetrics()
      const [cats, products] = await Promise.all([
        api.get('/categories', sid ? { store_id: sid } : {}, false),
        api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
      ])
      const items = normalizeList(products)
      const catList = normalizeList(cats)
      const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
      this._menuCache = { key: cacheKey, at: now, cats: catList, items, metrics }
      this.applyCategories(categories, metrics)
    } catch (e) {
      my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
      this.setData({ loading: false })
    }
  },

  applyCategories(categories, metrics) {
    const m = metrics || media.layoutMetrics()
    const activeCat = (categories[0] && categories[0].id) || null
    this.setData({
      categories: categories || [],
      isWide: !!m.isWide,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : '',
      loading: false
    })
  },

  onSearch(e) {
    const keyword = (e.detail && e.detail.value) || ''
    this.setData({ keyword })
    if (!this._menuCache) {
      this.loadMenu()
      return
    }
    const { cats, items, metrics } = this._menuCache
    const rebuilt = this.buildCategories(cats, items, metrics, keyword)
    const activeCat = (rebuilt[0] && rebuilt[0].id) || null
    this.setData({
      categories: rebuilt,
      activeCat,
      scrollInto: activeCat ? `cat-${activeCat}` : ''
    })
  },

  onCatTap(e) {
    const id = e.currentTarget.dataset.id
    this.setData({ activeCat: id, scrollInto: `cat-${id}` })
  },

  refreshCart() {
    const { count, total } = cartUtil.cartSummary(auth.getCart())
    this.setData({ cartCount: count, cartTotal: total })
  },

  openProduct(e) {
    my.navigateTo({ url: `/pages/product/product?id=${e.currentTarget.dataset.id}` })
  },

  quickAdd(e) {
    const { id, name, price, image, hasOptions, noMemberDiscount } = e.currentTarget.dataset
    if (hasOptions === true || hasOptions === 'true' || hasOptions === 1 || hasOptions === '1') {
      my.navigateTo({ url: `/pages/product/product?id=${id}` })
      return
    }
    const unit = parseFloat(price) || 0
    const cart = cartUtil.mergeCartItem(auth.getCart(), {
      product_id: Number(id),
      name,
      quantity: 1,
      option_ids: [],
      option_labels: [],
      option_text: '',
      unit_price: unit,
      subtotal: unit.toFixed(2),
      imageUrl: image || '',
      no_member_discount: noMemberDiscount === true || noMemberDiscount === 'true' || noMemberDiscount === 1 || noMemberDiscount === '1'
    })
    auth.saveCart(cart)
    this.refreshCart()
    my.showToast({ content: '已加入', type: 'success' })
  },

  goCart() {
    my.navigateTo({ url: '/pages/cart/cart' })
  },

  goMember() {
    my.switchTab({ url: '/pages/member/member' })
  }
})
