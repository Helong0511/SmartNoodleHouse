const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const tracker = require('../../utils/tracker')
const { loadStoreCatalog } = require('../../utils/products')
const app = getApp()

Page({
  data: {
    loading: true,
    product: null,
    selected: {},
    multi: {},
    quantity: 1,
    displayPrice: '0.00',
    lineTotal: '0.00',
    gallery: [],
    heroStyle: '',
    prefHint: '',
    canAdd: true,
    addBtnLabel: '加入点餐',
    unavailableLabel: ''
  },

  async onLoad(query) {
    this.productId = query.id
    const metrics = media.layoutMetrics()
    this.setData({ heroStyle: metrics.heroStyle, loading: true })
    try {
      const loginP = auth.ensureLogin().catch(() => {})
      const product = await api.get(`/products/${query.id}`, {}, false)
      const { selected, multi } = prefUtil.defaultSelections(product)
      // 先出餐品与规格，图库/登录并行，不挡首屏
      this.setData({ product, selected, multi, loading: false })
      this.updateDisplayPrice()
      this.refreshAddState()
      const galleryP = media.hydrateUrlList(media.galleryUrls(product)).then((gallery) => {
        this.setData({ gallery })
      }).catch(() => {})
      await loginP
      await this.applyPreferences(product)
      await galleryP
    } catch (e) {
      this.setData({ loading: false })
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  async applyPreferences(product) {
    let prefs = app.globalData.prefs || []
    if (!prefs.length) {
      const view = await prefUtil.loadCustomerPrefs(app, api)
      prefs = view.prefs || []
    }
    if (!prefs.length) return

    const applied = prefUtil.applyPrefsToProduct(product, prefs)
    if (applied.applied) {
      this.setData({
        selected: applied.selected,
        multi: applied.multi,
        prefHint: applied.prefHint
      })
      this.updateDisplayPrice()
      this.refreshAddState()
    }
  },

  updateDisplayPrice() {
    const p = this.data.product
    if (!p) return
    const unit = cartUtil.calcUnitPrice(p, this.data.selected, this.data.multi)
    const qty = this.data.quantity || 1
    this.setData({
      displayPrice: unit.toFixed(2),
      lineTotal: (unit * qty).toFixed(2)
    })
  },

  refreshAddState() {
    const p = this.data.product
    if (!p) {
      this.setData({ canAdd: false, addBtnLabel: '加入点餐', unavailableLabel: '' })
      return
    }
    const avail = cartUtil.productAvailability(p)
    if (!avail.available) {
      this.setData({
        canAdd: false,
        unavailableLabel: avail.label || '暂不可售',
        addBtnLabel: avail.label || '暂不可售'
      })
      return
    }
    const missing = cartUtil.missingRequiredOptionGroups(p, this.data.selected, this.data.multi)
    if (missing.length) {
      this.setData({
        canAdd: false,
        unavailableLabel: '',
        addBtnLabel: `请选择${missing[0]}`
      })
      return
    }
    this.setData({ canAdd: true, unavailableLabel: '', addBtnLabel: '加入点餐' })
  },

  changeQty(e) {
    const delta = parseInt(e.currentTarget.dataset.delta, 10)
    let qty = (this.data.quantity || 1) + delta
    if (qty < 1) qty = 1
    if (qty > 999) qty = 999
    this.setData({ quantity: qty }, () => this.updateDisplayPrice())
  },

  selectSingle(e) {
    const { gid, oid } = e.currentTarget.dataset
    this.setData({ [`selected.${gid}`]: oid }, () => {
      this.updateDisplayPrice()
      this.refreshAddState()
    })
  },

  selectMulti(e) {
    const { gid, oid } = e.currentTarget.dataset
    const list = [...(this.data.multi[gid] || [])]
    const idx = list.findIndex((x) => String(x) === String(oid))
    if (idx >= 0) list.splice(idx, 1)
    else list.push(oid)
    this.setData({ [`multi.${gid}`]: list }, () => {
      this.updateDisplayPrice()
      this.refreshAddState()
    })
  },

  resetOptions() {
    const p = this.data.product
    if (!p) return
    const { selected, multi } = prefUtil.defaultSelections(p)
    this.setData({ selected, multi, quantity: 1, prefHint: '' }, () => {
      this.updateDisplayPrice()
      this.refreshAddState()
    })
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url
    const urls = this.data.gallery
    if (!url || !urls.length) return
    wx.previewImage({ current: url, urls })
  },

  async addCart() {
    const p = this.data.product
    if (!p) return
    const avail = cartUtil.productAvailability(p)
    if (!avail.available) {
      wx.showToast({ title: avail.label || '暂不可售', icon: 'none' })
      return
    }
    const missing = cartUtil.missingRequiredOptionGroups(p, this.data.selected, this.data.multi)
    if (missing.length) {
      wx.showToast({ title: `请选择${missing[0]}`, icon: 'none' })
      return
    }
    let categories = []
    let products = [p]
    if ((p.require_category_ids || []).length || (p.require_product_ids || []).length) {
      try {
        const sid = p.store_id
          || (app.globalData.table && app.globalData.table.store_id)
          || (app.globalData.store && app.globalData.store.id)
          || null
        const catalog = sid
          ? await loadStoreCatalog(api, sid)
          : { items: [p], categories: [] }
        categories = (catalog && catalog.categories) || []
        products = (catalog && catalog.items && catalog.items.length) ? catalog.items : [p]
      } catch (_) {}
    }
    const check = cartUtil.checkCategoryRequire(p, auth.getCart(), products, categories)
    if (!check.ok) {
      wx.showToast({ title: check.message, icon: 'none', duration: 2500 })
      return
    }
    const qty = this.data.quantity || 1
    const optionIds = cartUtil.collectOptionIds(p, this.data.selected, this.data.multi)
    const unitPrice = cartUtil.calcUnitPrice(p, this.data.selected, this.data.multi)
    const optionLabels = cartUtil.buildOptionLabels(p, this.data.selected, this.data.multi)
    const imageUrl = media.resolveUrl(p.main_image, media.imageVersion(p))
    const cart = cartUtil.mergeCartItem(auth.getCart(), {
      product_id: p.id,
      name: p.name,
      quantity: qty,
      option_ids: optionIds,
      option_labels: optionLabels,
      option_text: optionLabels.join('、'),
      unit_price: unitPrice,
      subtotal: (unitPrice * qty).toFixed(2),
      main_image: p.main_image || '',
      imageUrl,
      no_member_discount: !!p.no_member_discount,
      category_id: p.category_id,
      require_category_ids: p.require_category_ids || [],
      require_product_ids: p.require_product_ids || []
    })
    auth.saveCart(cart)
    try {
      tracker.cartAdd(Number(p.id), {
        scene: 'product_detail',
        category_id: p.category_id || null,
        payload: {
          qty,
          options: optionLabels,
          source: this.data.fromRecommend ? 'recommend' : 'menu'
        }
      })
    } catch (_) {}
    wx.showToast({ title: '已加入', icon: 'success' })
    setTimeout(() => {
      wx.navigateBack()
    }, 500)
  }
})
