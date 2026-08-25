const api = require('../../utils/api')

Page({
  data: { order: {} },
  async onLoad(query) {
    try {
      const order = await api.get(`/orders/${query.order_id}`)
      this.setData({ order })
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },
  goMenu() {
    wx.redirectTo({ url: '/pages/menu/menu' })
  }
})
