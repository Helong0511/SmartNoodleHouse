const api = require('../../utils/api')
const prefUtil = require('../../utils/preferences')
const app = getApp()

Page({
  data: { order: {}, hasPacking: false },
  async onLoad(query) {
    try {
      const order = await api.get(`/orders/${query.order_id}`)
      const fee = Number(order && order.packing_fee)
      this.setData({ order, hasPacking: fee > 0 })
      if (order && order.status === 'COMPLETED') {
        prefUtil.clearPrefsCache(app)
      }
    } catch (e) {
      my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
    }
  },
  goMenu() {
    my.redirectTo({ url: '/pages/menu/menu' })
  }
})
