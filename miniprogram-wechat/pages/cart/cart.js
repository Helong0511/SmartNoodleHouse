Page({
  onLoad() {
    // 已取消独立购物车页 → 回点餐页直接结算
    try {
      const app = getApp()
      if (app && app.globalData) app.globalData._openCartSheet = true
    } catch (_) {}
    wx.switchTab({
      url: '/pages/menu/menu',
      fail: () => wx.reLaunch({ url: '/pages/menu/menu' })
    })
  }
})
