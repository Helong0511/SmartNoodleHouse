Page({
  onLoad() {
    // 旧分包购物车 → 点餐页
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
