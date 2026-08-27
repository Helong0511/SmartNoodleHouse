const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const app = getApp()

Page({
  data: {
    categories: [],
    tableName: '',
    cartCount: 0,
    cartTotal: '0.00',
    isWide: false,
    prefs: [],
    prefSummary: ''
  },

  onShow() {
    this.refreshCart()
    this.loadMenu()
    this.ensureTable()
    this.loadPrefs()
  },

  async ensureTable() {
    let table = app.globalData.table
    const token = app.globalData.tableToken || wx.getStorageSync('table_token')
    if ((!table || !table.table_name) && token) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
        if (entry && entry.table) {
          table = entry.table
          app.globalData.table = table
          app.globalData.tableToken = table.qr_token
          wx.setStorageSync('table_token', table.qr_token)
        }
      } catch (_) {}
    }
    if (table) this.setData({ tableName: table.table_name || table.table_no || '' })
  },

  async loadPrefs() {
    const cid = app.globalData.customerId
    if (!cid || !app.globalData.customerToken) {
      this.setData({ prefs: [], prefSummary: '' })
      return
    }
    try {
      const p = await api.get(`/customers/${cid}/preferences`)
      const prefs = (p && p.preferences) || []
      const summary = prefs
        .slice(0, 3)
        .map((x) => `${x.category}·${x.option_name || x.option_code}`)
        .join('、')
      this.setData({
        prefs,
        prefSummary: summary ? `智能口味：${summary}` : ''
      })
      app.globalData.prefs = prefs
    } catch (_) {
      this.setData({ prefs: [], prefSummary: '' })
    }
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
      this.setData({
        categories,
        isWide: !!metrics.isWide
      })
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  refreshCart() {
    const { count, total } = cartUtil.cartSummary(auth.getCart())
    this.setData({ cartCount: count, cartTotal: total })
  },

  openProduct(e) {
    wx.navigateTo({ url: `/pages/product/product?id=${e.currentTarget.dataset.id}` })
  },

  goCart() {
    wx.navigateTo({ url: '/pages/cart/cart' })
  },

  goMember() {
    wx.navigateTo({ url: '/pages/member/member' })
  }
})
