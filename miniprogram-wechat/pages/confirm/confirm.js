const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: { remark: '', loading: false },

  onRemark(e) {
    this.setData({ remark: e.detail.value })
  },

  async submit() {
    const cart = auth.getCart()
    if (!cart.length) {
      wx.showToast({ title: '购物车为空', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: (app.globalData.table && app.globalData.table.store_id) || 1,
        table_token: app.globalData.tableToken || wx.getStorageSync('table_token'),
        channel: 'WECHAT',
        remark: this.data.remark,
        idempotency_key: `wx_${app.globalData.customerId}_${Date.now()}`,
        items: cart.map(c => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      await api.post(`/payments/orders/${order.id}/pay`, {
        provider: 'WECHAT',
        channel_type: 'miniprogram'
      })
      auth.saveCart([])
      wx.redirectTo({ url: `/pages/result/result?order_id=${order.id}` })
    } catch (e) {
      wx.showToast({ title: e.message || '下单失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
