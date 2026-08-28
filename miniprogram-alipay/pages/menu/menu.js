const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const prefUtil = require('../../utils/preferences')
const app = getApp()

Page({
  data: {
    categories: [],
    tableName: '',
    cartCount: 0,
    cartTotal: '0.00',
    isWide: false,
    prefSummary: ''
  },

  onShow() {
    this.refreshCart()
    this.loadPrefs()
    this.loadMenu()
    this.ensureTable()
  },

  onPullDownRefresh() {
    Promise.all([this.loadPrefs(true), this.loadMenu()])
      .finally(() => my.stopPullDownRefresh())
  },

  async loadPrefs(force = false) {
    const view = await prefUtil.loadCustomerPrefs(app, api, { force })
    this.setData({ prefSummary: view.prefSummary || '' })
  },

  async ensureTable() {
    let table = app.globalData.table
    const token = app.globalData.tableToken || (my.getStorageSync({ key: 'table_token' }).data)
    if ((!table || !table.table_name) && token) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'alipay' }, false)
        if (entry && entry.table) {
          table = entry.table
          app.globalData.table = table
          app.globalData.tableToken = table.qr_token
          my.setStorageSync({ key: 'table_token', data: table.qr_token })
        }
      } catch (_) {}
    }
    if (table) this.setData({ tableName: table.table_name || table.table_no || '' })
  },

  async loadMenu() {
    try {
      const metrics = media.layoutMetrics()
      const sid = app.globalData.table && app.globalData.table.store_id
      const cats = await api.get('/categories', sid ? { store_id: sid } : {}, false)
      const products = await api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
      const items = (products && products.items) || []
      const categories = (cats || []).map(c => ({
        ...c,
        products: items
          .filter(p => p.category_id === c.id)
          .map(p => media.decorateProduct(p, metrics))
      }))
      this.setData({ categories, isWide: !!metrics.isWide })
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },

  refreshCart() {
    const { count, total } = cartUtil.cartSummary(auth.getCart())
    this.setData({ cartCount: count, cartTotal: total })
  },

  openProduct(e) {
    const id = e.currentTarget.dataset.id
    my.navigateTo({ url: `/pages/product/product?id=${id}` })
  },

  goCart() { my.navigateTo({ url: '/pages/cart/cart' }) },
  goMember() { my.navigateTo({ url: '/pages/member/member' }) }
})
