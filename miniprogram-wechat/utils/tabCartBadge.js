/** TabBar 购物车角标：显示有效商品样数（行数，非总件数） */

const CART_TAB_INDEX = 1

function cartBadgeCount(cart) {
  return (cart || []).filter((i) => !i.invalid).length
}

function readCart() {
  try {
    const app = getApp()
    if (app && app.globalData && Array.isArray(app.globalData.cart)) {
      return app.globalData.cart
    }
  } catch (_) {}
  try {
    return wx.getStorageSync('cart') || []
  } catch (_) {
    return []
  }
}

function applyBadge(count) {
  if (count > 0) {
    wx.setTabBarBadge({
      index: CART_TAB_INDEX,
      text: count > 99 ? '99+' : String(count),
    })
  } else {
    wx.removeTabBarBadge({ index: CART_TAB_INDEX })
  }
}

/**
 * 更新角标。非 Tab 页调用会失败（微信限制），需在各 Tab 页 onShow 再刷一次。
 */
function updateTabCartBadge(cart) {
  const list = cart != null ? cart : readCart()
  const count = cartBadgeCount(list)
  try {
    applyBadge(count)
  } catch (_) {
    /* 同步抛错时忽略 */
  }
  // 从商品详情返回等时机，延后一帧再试，提高成功率
  setTimeout(() => {
    try {
      applyBadge(count)
    } catch (_) {}
  }, 50)
}

/** Tab 页 onShow 使用：按当前购物车强制同步角标 */
function syncTabCartBadge() {
  updateTabCartBadge(readCart())
}

module.exports = {
  CART_TAB_INDEX,
  cartBadgeCount,
  updateTabCartBadge,
  syncTabCartBadge,
}
