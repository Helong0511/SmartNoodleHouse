const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const tabCartBadge = require('../../utils/tabCartBadge')
const tracker = require('../../utils/tracker')
const { loadStoreCatalog } = require('../../utils/products')
const app = getApp()

Page({
  data: {
    cart: [],
    total: '0.00',
    count: 0,
    loading: true,
    invalidCount: 0,
    thumbStyle: '',
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号'
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    const metrics = media.layoutMetrics()
    const px = Math.round(Math.max(56, (metrics.thumbPx || 80) * 0.85))
    this.setData({ thumbStyle: `width:${px}px;height:${px}px;` })
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    this.refresh(true)
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    this.refresh(true)
  },

  goMenu() {
    my.switchTab({ url: '/pages/menu/menu' })
  },

  onPullDownRefresh() {
    this.refresh(true).finally(() => my.stopPullDownRefresh())
  },

  storeId() {
    try {
      const ctx = dineCtx.currentDisplay()
      if (ctx && ctx.storeId) return ctx.storeId
    } catch (_) {}
    const g = app && app.globalData
    if (!g) return null
    return (g.table && g.table.store_id)
      || (g.store && (g.store.id || g.store.store_id))
      || null
  },

  async refresh(showLoading) {
    if (showLoading) this.setData({ loading: true })
    const rawCart = auth.getCart()
    let products = []
    let categories = []
    let complete = false
    let loadError = ''
    try {
      const sid = this.storeId()
      if (!sid) {
        loadError = '请先扫桌码选择门店'
        throw new Error(loadError)
      }
      const catalog = await loadStoreCatalog(api, sid)
      products = (catalog && catalog.items) || []
      categories = (catalog && catalog.categories) || []
      complete = !!(catalog && catalog.complete)
      if (!complete) loadError = '菜单未加载完整，请下拉重试'
    } catch (e) {
      if (!loadError) loadError = (e && e.message) || '菜单加载失败，请下拉重试'
    }

    const { list, invalidCount } = cartUtil.reconcileWithProducts(rawCart, products, categories, { complete })
    const cart = list.map((raw) => {
      const item = cartUtil.normalizeItem({ ...raw })
      return {
        ...item,
        imageUrl: media.resolveUrl(item.main_image || item.imageUrl || '')
      }
    })
    const validItems = cart.filter((i) => !i.invalid)
    const { total, count } = cartUtil.cartSummary(validItems)
    auth.saveCart(cart)
    this.setData({
      cart,
      total,
      count,
      invalidCount,
      loading: false
    })
    if (showLoading && !complete && rawCart.length) {
      my.showToast({ content: loadError || '菜单未加载完整，请下拉重试', type: 'none' })
    }
  },

  changeQty(e) {
    const { index, delta } = e.currentTarget.dataset
    const cart = auth.getCart()
    const item = cart[index]
    if (!item || item.invalid) return
    const qty = (item.quantity || 1) + parseInt(delta, 10)
    if (qty <= 0) {
      cart.splice(index, 1)
      try { tracker.cartRemove(Number(item.product_id), { scene: 'cart', category_id: item.category_id || null }) } catch (_) {}
    } else {
      cartUtil.normalizeItem(item)
      item.quantity = qty
      item.subtotal = (parseFloat(item.unit_price) * qty).toFixed(2)
    }
    auth.saveCart(cart)
    this.refresh(false)
  },

  removeItem(e) {
    const index = e.currentTarget.dataset.index
    const cart = auth.getCart()
    const removed = cart[index]
    cart.splice(index, 1)
    auth.saveCart(cart)
    if (removed) {
      try { tracker.cartRemove(Number(removed.product_id), { scene: 'cart', category_id: removed.category_id || null }) } catch (_) {}
    }
    this.refresh(false)
  },

  clearInvalid() {
    const cart = auth.getCart().filter((i) => !i.invalid)
    auth.saveCart(cart)
    my.showToast({ content: '已清除失效商品', type: 'success' })
    this.refresh(false)
  },

  goConfirm() {
    if (this.data.invalidCount > 0) {
      my.confirm({
        title: '有商品不可下单',
        content: `${this.data.invalidCount} 件商品已失效或库存不足，请先清除后再结算`,
        confirmButtonText: '清除失效',
        cancelButtonText: '返回',
        success: (res) => {
          if (res.confirm) this.clearInvalid()
        }
      })
      return
    }
    if (!this.data.cart.length || this.data.count === 0) {
      my.showToast({ content: '购物车为空', type: 'fail' })
      return
    }
    my.navigateTo({ url: '/packageOrder/confirm/confirm' })
  }
})
