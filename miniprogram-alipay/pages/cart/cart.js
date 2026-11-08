Page({
  onLoad() {
    try {
      const app = getApp()
      if (app && app.globalData) app.globalData._openCartSheet = true
    } catch (_) {}
    my.switchTab({
      url: '/pages/menu/menu',
      fail: () => my.reLaunch({ url: '/pages/menu/menu' })
    })
  }
})
