const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: { categories: [], tableName: '', cartCount: 0, cartTotal: '0.00' },
  onShow() {
    this.refreshCart()
    this.loadMenu()
    if (app.globalData.table) this.setData({ tableName: app.globalData.table.table_name })
  },
  async loadMenu() {
    try {
      const cats = await api.get('/categories')
      const products = await api.get('/products', { page_size: 100 })
      const items = (products && products.items) || []
      this.setData({
        categories: (cats || []).map(c => ({ ...c, products: items.filter(p => p.category_id === c.id) }))
      })
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },
  refreshCart() {
    const cart = auth.getCart()
    this.setData({
      cartCount: cart.reduce((s, i) => s + (i.quantity || 1), 0),
      cartTotal: cart.reduce((s, i) => s + parseFloat(i.subtotal || 0), 0).toFixed(2)
    })
  },
  openProduct(e) { my.navigateTo({ url: `/pages/product/product?id=${e.target.dataset.id}` }) },
  goCart() { my.navigateTo({ url: '/pages/cart/cart' }) },
  goMember() { my.navigateTo({ url: '/pages/member/member' }) }
})
