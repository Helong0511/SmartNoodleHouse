const api = require('../../utils/api')
Page({
  data: { order: {} },
  async onLoad(query) {
    try {
      this.setData({ order: await api.get(`/orders/${query.order_id}`) })
    } catch (e) {
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },
  goMenu() { my.redirectTo({ url: '/pages/menu/menu' }) }
})
