const api = require('../../utils/api')

Page({
  data: { order: {}, hasPacking: false },
  async onLoad(query) {
    try {
      const order = await api.get(`/orders/${query.order_id}`)
      const fee = Number(order && order.packing_fee)
      this.setData({ order, hasPacking: fee > 0 })
    } catch (e) {
      my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
    }
  },
  goMenu() {
    my.redirectTo({ url: '/pages/menu/menu' })
  }
})
