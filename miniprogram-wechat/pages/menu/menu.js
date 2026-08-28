const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const app = getApp()

const MENU_CACHE_MS = 5 * 60 * 1000
const PREFS_CACHE_MS = 60 * 1000

Page({
  data: {
    categories: [],
    cartCount: 0,
    cartTotal: '0.00',
    isWide: false,
    prefs: [],
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
    ]).finally(() => wx.stopPullDownRefresh())
  },

  async scanTable() {
    const ctx = await dineCtx.scanAndBindTable()
    if (ctx) {
      this.applyDineContext(ctx)
      this._menuCache = null
      this.loadMenu()
    }
  },

  async loadPrefs(force = false) {
    const cid = app.globalData.customerId
    if (!cid || !app.globalData.customerToken) {
      this.setData({ prefs: [], prefSummary: '' })
      return
    }
    const now = Date.now()
    if (
      !force
      && app.globalData.prefs
      && app.globalData.prefsAt
      && now - app.globalData.prefsAt < PREFS_CACHE_MS
    ) {
      const prefs = app.globalData.prefs
      const summary = prefs
        .slice(0, 3)
        .map((x) => `${x.category}·${x.option_name || x.option_code}`)
        .join('、')
      this.setData({ prefs, prefSummary: summary ? `智能口味：${summary}` : '' })
      return
    }
    try {
      const p = await api.get(`/customers/${cid}/preferences`)
      const prefs = (p && p.preferences) || []
      const summary = prefs
        .slice(0, 3)
        .map((x) => `${x.category}·${x.option_name || x.option_code}`)
        .join('、')
      app.globalData.prefs = prefs
      app.globalData.prefsAt = now
      this.setData({
        prefs,
        prefSummary: summary ? `智能口味：${summary}` : ''
      })
    } catch (_) {
      this.setData({ prefs: [], prefSummary: '' })
    }
  },

  buildCategories(cats, items, metrics, keyword) {
    const kw = (keyword || '').trim().toLowerCase()
    return (cats || [])
      .map((c) => {
        const products = items
          .filter((p) => p.category_id === c.id)
          .filter((p) => {
            if (!kw) return true
            return (
              String(p.name || '').toLowerCase().includes(kw)
              || String(p.description || '').toLowerCase().includes(kw)
            )
          })
          .map((p) => media.decorateProduct(p, metrics))
        return { ...c, products }
      })
      .filter((c) => c.products.length > 0 || !kw)
  },

  async loadMenu() {
    const sid = app.globalData.table && app.globalData.table.store_id
    const cacheKey = String(sid || 'all')
    const now = Date.now()
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
    this.setData({ loading: true })
    try {
      const metrics = media.layoutMetrics()
      const [cats, products] = await Promise.all([
        api.get('/categories', sid ? { store_id: sid } : {}, false),
        api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
      ])
      const items = (products && products.items) || []
      const categories = this.buildCategories(cats, items, metrics, this.data.keyword)
      this._menuCache = { key: cacheKey, at: now, cats, items, metrics }
      this.applyCategories(categories, metrics)
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
      this.setData({ loading: false })
    }
  },

  applyCategories(categories, metrics) {
    const activeCat = (categories[0] && categories[0].id) || null
    this.setData({
      categories,
      isWide: !!metrics.isWide,
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
    wx.navigateTo({ url: `/packageOrder/product/product?id=${e.currentTarget.dataset.id}` })
  },

  quickAdd(e) {
    const { id, name, price, image, hasOptions } = e.currentTarget.dataset
    if (hasOptions === true || hasOptions === 'true' || hasOptions === 1 || hasOptions === '1') {
      wx.navigateTo({ url: `/packageOrder/product/product?id=${id}` })
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
      imageUrl: image || ''
    })
    auth.saveCart(cart)
    this.refreshCart()
    wx.showToast({ title: '已加入', icon: 'success' })
  },

  goCart() {
    wx.navigateTo({ url: '/packageOrder/cart/cart' })
  },

  goMember() {
    wx.switchTab({ url: '/pages/member/member' })
  }
})
