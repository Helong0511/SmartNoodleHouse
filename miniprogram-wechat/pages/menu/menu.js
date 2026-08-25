const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: {
    categories: [],
    tableName: '',
    cartCount: 0,
    cartTotal: '0.00'
  },

  onShow() {
    this.refreshCart()
    this.loadMenu()
    const table = app.globalData.table
    if (table) this.setData({ tableName: table.table_name })
  },

  async loadMenu() {
    try {
      const cats = await api.get('/categories')
      const products = await api.get('/products', { page_size: 100 })
      const items = (products && products.items) || []
      const categories = (cats || []).map(c => ({
        ...c,
        products: items.filter(p => p.category_id === c.id)
      }))
      this.setData({ categories })
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  refreshCart() {
    const cart = auth.getCart()
    const cartCount = cart.reduce((s, i) => s + (i.quantity || 1), 0)
    const cartTotal = cart.reduce((s, i) => s + parseFloat(i.subtotal || 0), 0).toFixed(2)
    this.setData({ cartCount, cartTotal })
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
