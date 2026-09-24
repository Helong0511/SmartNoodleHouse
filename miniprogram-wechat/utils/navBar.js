/** Tab 页导航栏色：以 page.json / app.json 为准，仅在 onShow 轻量补一次，避免切 Tab 白屏 */

const NAV_BG = '#ff6b35'
const NAV_FRONT = '#ffffff'

function pinWechatNavBar() {
  try {
    wx.setNavigationBarColor({
      frontColor: NAV_FRONT,
      backgroundColor: NAV_BG,
      animation: { duration: 0, timingFunc: 'linear' }
    })
  } catch (_) {}
}

module.exports = {
  NAV_BG,
  NAV_FRONT,
  pinWechatNavBar
}
