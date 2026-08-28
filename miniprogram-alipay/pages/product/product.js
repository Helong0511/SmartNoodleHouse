const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const app = getApp()

Page({
  data: {
    product: null,
    selected: {},
    multi: {},
    displayPrice: '0.00',
    gallery: [],
    heroStyle: '',
    prefHint: ''
  },

  async onLoad(query) {
    const metrics = media.layoutMetrics()
    this.setData({ heroStyle: metrics.heroStyle })
    try {
      const product = await api.get(`/products/${query.id}`, {}, false)
      const { selected, multi } = prefUtil.defaultSelections(product)
      const gallery = media.galleryUrls(product)
      this.setData({ product, selected, multi, gallery })
      this.updateDisplayPrice()
      await this.applyPreferences(product)
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
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
    this.setData({ displayPrice: unit.toFixed(2) })
  },

  selectSingle(e) {
    const { gid, oid } = e.currentTarget.dataset
    this.setData({ [`selected.${gid}`]: oid }, () => this.updateDisplayPrice())
  },

  selectMulti(e) {
    const { gid, oid } = e.currentTarget.dataset
    const list = [...(this.data.multi[gid] || [])]
    const idx = list.findIndex(x => String(x) === String(oid))
    if (idx >= 0) list.splice(idx, 1)
    else list.push(oid)
    this.setData({ [`multi.${gid}`]: list }, () => this.updateDisplayPrice())
  },

  previewImage(e) {
    const url = e.currentTarget.dataset.url
    const urls = this.data.gallery
    if (!url || !urls.length) return
    my.previewImage({ current: url, urls })
  },

  addCart() {
    const p = this.data.product
    if (!p) return
    const optionIds = cartUtil.collectOptionIds(p, this.data.selected, this.data.multi)
    const unitPrice = cartUtil.calcUnitPrice(p, this.data.selected, this.data.multi)
    const optionLabels = cartUtil.buildOptionLabels(p, this.data.selected, this.data.multi)
    const imageUrl = media.resolveUrl(p.main_image)
    const cart = auth.getCart()
    cart.push({
      product_id: p.id,
      name: p.name,
      quantity: 1,
      option_ids: optionIds,
      option_labels: optionLabels,
      option_text: optionLabels.join('、'),
      unit_price: unitPrice,
      subtotal: unitPrice.toFixed(2),
      imageUrl,
      no_member_discount: !!p.no_member_discount
    })
    auth.saveCart(cart)
    my.showToast({ content: '已加入' })
    setTimeout(() => my.navigateBack(), 400)
  }
})
