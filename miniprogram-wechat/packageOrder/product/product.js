const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
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
    prefHint: ''
  },

  async onLoad(query) {
    this.productId = query.id
    const metrics = media.layoutMetrics()
    this.setData({ heroStyle: metrics.heroStyle, loading: true })
    try {
      const product = await api.get(`/products/${query.id}`, {}, false)
      const { selected, multi } = prefUtil.defaultSelections(product)
      const gallery = await media.hydrateUrlList(media.galleryUrls(product))
      this.setData({ product, selected, multi, gallery, loading: false })
      this.updateDisplayPrice()
      await this.applyPreferences(product)
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

  changeQty(e) {
    const delta = parseInt(e.currentTarget.dataset.delta, 10)
    let qty = (this.data.quantity || 1) + delta
    if (qty < 1) qty = 1
    if (qty > 99) qty = 99
    this.setData({ quantity: qty }, () => this.updateDisplayPrice())
  },

  selectSingle(e) {
    const { gid, oid } = e.currentTarget.dataset
    this.setData({ [`selected.${gid}`]: oid }, () => this.updateDisplayPrice())
  },

  selectMulti(e) {
    const { gid, oid } = e.currentTarget.dataset
    const list = [...(this.data.multi[gid] || [])]
    const idx = list.findIndex((x) => String(x) === String(oid))
    if (idx >= 0) list.splice(idx, 1)
    else list.push(oid)
    this.setData({ [`multi.${gid}`]: list }, () => this.updateDisplayPrice())
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url
    const urls = this.data.gallery
    if (!url || !urls.length) return
    wx.previewImage({ current: url, urls })
  },

  addCart() {
    const p = this.data.product
    if (!p) return
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
      imageUrl,
      no_member_discount: !!p.no_member_discount
    })
    auth.saveCart(cart)
    wx.showToast({ title: '已加入', icon: 'success' })
    setTimeout(() => wx.navigateBack(), 500)
  }
})
