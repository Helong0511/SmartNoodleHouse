/** TabBar 角标：已取消独立购物车 Tab，点餐页底部直接结算，角标不再展示 */

function cartBadgeCount(cart) {
  return (cart || []).filter((i) => !i.invalid).length
}

function updateTabCartBadge() {
  /* no-op：无购物车 Tab */
}

function syncTabCartBadge() {
  /* no-op */
}

module.exports = {
  CART_TAB_INDEX: -1,
  cartBadgeCount,
  updateTabCartBadge,
  syncTabCartBadge
}
