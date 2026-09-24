/** Tab 页切换时锁定导航栏色，避免回落默认白底闪一下 */

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
