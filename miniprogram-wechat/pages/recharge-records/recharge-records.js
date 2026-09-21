/** 兼容旧入口：转到订单 Tab 并选中「充值订单」 */
Page({
  onShow() {
    try {
      wx.setStorageSync('orders_scope_pending', 'recharge')
    } catch (e) {}
    wx.switchTab({ url: '/pages/orders/orders' })
  }
})
