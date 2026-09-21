/** 兼容旧入口：转到订单 Tab 并选中「充值订单」 */
Page({
  onShow() {
    try {
      my.setStorageSync({ key: 'orders_scope_pending', data: 'recharge' })
    } catch (e) {}
    my.switchTab({ url: '/pages/orders/orders' })
  }
})
