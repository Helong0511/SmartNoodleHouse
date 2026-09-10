Page({
  onLoad() {
    // 旧分包购物车 → Tab 购物车
    wx.switchTab({
      url: '/pages/cart/cart',
      fail: () => wx.reLaunch({ url: '/pages/index/index' })
    })
  }
})
