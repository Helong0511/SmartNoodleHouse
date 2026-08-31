const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const tabCartBadge = require('../../utils/tabCartBadge')
const app = getApp()

const MENU_CACHE_MS = 5 * 60 * 1000

function normalizeList(data) {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

function askUsePrefsOnQuickAdd(prompt) {
  return new Promise((resolve) => {
    wx.showModal({
      title: prompt.title,
      content: prompt.content,
      cancelText: '否',
      confirmText: '是',
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false)
    })
  })
}

Page({
  data: {
    categories: [],
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
    tabCartBadge.syncTabCartBadge()
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
    this._catsCache = null
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
      this._catsCache = null
      this.loadMenu()
    }
  },

  async loadPrefs(force = false) {
    try {
      await auth.ensureLogin()
    } catch (_) {}
    try {
      const view = await prefUtil.loadCustomerPrefs(app, api, { force })
      this.setData({
        prefs: view.prefs || [],
        prefSummary: view.prefSummary || ''
      })
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
    const sid = app.globalData.table && app.globalData.table.store_id
    const cacheKey = String(sid || 'all')
    const now = Date.now()
    this.setData({ loading: true })
    try {
      const metrics = media.layoutMetrics()
      let catList = null
      if (
        this._catsCache
        && this._catsCache.key === cacheKey
        && now - this._catsCache.at < MENU_CACHE_MS
      ) {
        catList = this._catsCache.cats
      }
      const [cats, products] = await Promise.all([
        catList ? Promise.resolve(catList) : api.get('/categories', sid ? { store_id: sid } : {}, false),
        api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
      ])
      if (!catList) {
        catList = normalizeList(cats)
        this._catsCache = { key: cacheKey, at: now, cats: catList, metrics }
      } else if (this._catsCache) {
        this._catsCache.metrics = metrics
      }
      const items = normalizeList(products)
      this._lastProductItems = items
      const categories = this.buildCategories(catList, items, metrics, this.data.keyword)
      await this.applyCategories(categories, metrics)
    } catch (e) {
      wx.showToast({ title: (e && e.message) || '加载失败', icon: 'none' })
      this.setData({ loading: false })
    }
  },

  async applyCategories(categories, metrics) {
    const m = metrics || media.layoutMetrics()
    const flat = []
    ;(categories || []).forEach((c) => {
      ;(c.products || []).forEach((p) => flat.push(p))
    })
    await media.hydrateImageUrls(flat, 'imageUrl')
    await media.hydrateImageUrls(flat, 'coverUrl')
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
    if (!this._catsCache || !this._lastProductItems) {
      this.loadMenu()
      return
    }
    const { cats, metrics } = this._catsCache
    const items = this._lastProductItems
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

  openProduct(e) {
    wx.navigateTo({ url: `/packageOrder/product/product?id=${e.currentTarget.dataset.id}` })
  },

  fetchProductDetail(id) {
    if (!this._productDetailCache) this._productDetailCache = {}
    const key = String(id)
    if (this._productDetailCache[key]) return Promise.resolve(this._productDetailCache[key])
    return api.get(`/products/${id}`, {}, false).then((product) => {
      this._productDetailCache[key] = product
      return product
    })
  },

  addSimpleToCart(entry) {
    const cart = cartUtil.mergeCartItem(auth.getCart(), entry)
    auth.saveCart(cart)
  },

  async quickAdd(e) {
    const ds = e.currentTarget.dataset
    const id = Number(ds.id)
    const hasOptions = ds.hasOptions === true || ds.hasOptions === 'true' || ds.hasOptions === 1 || ds.hasOptions === '1'
    const noMemberDiscount = ds.noMemberDiscount === true || ds.noMemberDiscount === 'true' || ds.noMemberDiscount === 1 || ds.noMemberDiscount === '1'

    if (!hasOptions) {
      const unit = parseFloat(ds.price) || 0
      this.addSimpleToCart({
        product_id: id,
        name: ds.name,
        quantity: 1,
        option_ids: [],
        option_labels: [],
        option_text: '',
        unit_price: unit,
        subtotal: unit.toFixed(2),
        imageUrl: ds.image || '',
        no_member_discount: noMemberDiscount
      })
      wx.showToast({ title: '已加入', icon: 'success' })
      return
    }

    try {
      wx.showLoading({ title: '加入中', mask: true })
      const product = await this.fetchProductDetail(id)
      const prefs = this.data.prefs || app.globalData.prefs || []
      const prefPrompt = prefUtil.buildQuickAddPrefPrompt(product, prefs)
      let usePrefs = false
      if (prefPrompt) {
        wx.hideLoading()
        usePrefs = await askUsePrefsOnQuickAdd(prefPrompt)
        wx.showLoading({ title: '加入中', mask: true })
      }
      const { selected, multi } = prefUtil.resolveQuickAddSelections(product, prefs, usePrefs)
      if (!prefUtil.selectionsComplete(product, selected, multi)) {
        wx.hideLoading()
        wx.navigateTo({ url: `/packageOrder/product/product?id=${id}` })
        return
      }
      const unitPrice = cartUtil.calcUnitPrice(product, selected, multi)
      const optionLabels = cartUtil.buildOptionLabels(product, selected, multi)
      this.addSimpleToCart({
        product_id: id,
        name: product.name || ds.name,
        quantity: 1,
        option_ids: cartUtil.collectOptionIds(product, selected, multi),
        option_labels: optionLabels,
        option_text: optionLabels.join('、'),
        unit_price: unitPrice,
        subtotal: unitPrice.toFixed(2),
        imageUrl: ds.image || media.resolveUrl(product.main_image, media.imageVersion(product)),
        no_member_discount: noMemberDiscount || !!product.no_member_discount
      })
      wx.hideLoading()
      wx.showToast({ title: usePrefs ? '已按爱好加入' : '已加入', icon: 'success' })
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: (err && err.message) || '加入失败', icon: 'none' })
    }
  },

  goMember() {
    wx.switchTab({ url: '/pages/member/member' })
  }
})
