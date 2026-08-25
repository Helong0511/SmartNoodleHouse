const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: { remark: '', loading: false },
  onRemark(e) { this.setData({ remark: e.detail.value }) },
  async submit() {
    const cart = auth.getCart()
    if (!cart.length) {
      my.showToast({ content: '购物车为空', type: 'fail' })
      return
    }
    this.setData({ loading: true })
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: (app.globalData.table && app.globalData.table.store_id) || 1,
        table_token: app.globalData.tableToken || my.getStorageSync({ key: 'table_token' }).data,
        channel: 'ALIPAY',
        remark: this.data.remark,
        idempotency_key: `ali_${app.globalData.customerId}_${Date.now()}`,
        items: cart.map(c => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      await api.post(`/payments/orders/${order.id}/pay`, {
        provider: 'ALIPAY',
        channel_type: 'miniprogram'
      })
      auth.saveCart([])
      my.redirectTo({ url: `/pages/result/result?order_id=${order.id}` })
    } catch (e) {
      my.showToast({ content: e.message || '下单失败', type: 'fail' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
