/** Tab 页导航栏色：以 page.json / app.json 为准，仅在 onShow 轻量补一次 */

const NAV_BG = '#ff6b35'
const NAV_FRONT = '#ffffff'

function pinAlipayNavBar() {
  try {
    if (typeof my !== 'undefined' && typeof my.setNavigationBar === 'function') {
      my.setNavigationBar({
        backgroundColor: NAV_BG,
        frontColor: NAV_FRONT
      })
    }
  } catch (_) {}
}

module.exports = {
  NAV_BG,
  NAV_FRONT,
  pinAlipayNavBar
}
