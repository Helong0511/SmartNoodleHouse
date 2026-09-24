/** Tab 页切换时锁定导航栏色，避免 iOS/微信回落默认白底闪一下 */

const NAV_BG = '#ff6b35'
const NAV_FRONT = '#ffffff'
const PAGE_BG = '#f7f7f7'

function pinWechatNavBar() {
  try {
    wx.setNavigationBarColor({
      frontColor: NAV_FRONT,
      backgroundColor: NAV_BG,
      animation: { duration: 0, timingFunc: 'linear' }
    })
  } catch (_) {}
  try {
    if (typeof wx.setBackgroundColor === 'function') {
      wx.setBackgroundColor({
        backgroundColor: PAGE_BG,
        backgroundColorTop: NAV_BG,
        backgroundColorBottom: PAGE_BG
      })
    }
  } catch (_) {}
}

module.exports = {
  NAV_BG,
  NAV_FRONT,
  pinWechatNavBar
}
