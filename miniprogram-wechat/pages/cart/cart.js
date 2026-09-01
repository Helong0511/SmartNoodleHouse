const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const tabCartBadge = require('../../utils/tabCartBadge')
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
    tableName: '',
    tableNo: ''
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    const metrics = media.layoutMetrics()
    const px = Math.round(Math.max(56, (metrics.thumbPx || 80) * 0.85))
    this.setData({ thumbStyle: `width:${px}px;height:${px}px;` })
    this.loadDineContext()
    this.refresh(true)
  },

  onPullDownRefresh() {
    this.refresh(true).finally(() => wx.stopPullDownRefresh())
  },

  async loadDineContext() {
    const ctx = await dineCtx.ensureDineContext()
    this.setData({
      storeName: ctx.storeName,
      storeNo: ctx.storeNo,
      tableName: ctx.tableName,
      tableNo: ctx.tableNo
    })
  },

  storeId() {
    return (app.globalData.table && app.globalData.table.store_id)
      || (app.globalData.store && app.globalData.store.id)
      || null
  },

  async refresh(showLoading) {
    if (showLoading) this.setData({ loading: true })
    const rawCart = auth.getCart()
    let products = []
    try {
      const sid = this.storeId()
      const res = await api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
      products = (res && res.items) || []
    } catch (_) {}

    const { list, invalidCount } = cartUtil.reconcileWithProducts(rawCart, products)
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
  },

  changeQty(e) {
    const { index, delta } = e.currentTarget.dataset
    const cart = auth.getCart()
    const item = cart[index]
    if (!item || item.invalid) return
    const qty = (item.quantity || 1) + parseInt(delta, 10)
    if (qty <= 0) {
      cart.splice(index, 1)
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
    cart.splice(index, 1)
    auth.saveCart(cart)
    this.refresh(false)
  },

  clearInvalid() {
    const cart = auth.getCart().filter((i) => !i.invalid)
    auth.saveCart(cart)
    wx.showToast({ title: '已清除失效商品', icon: 'success' })
    this.refresh(false)
  },

  goConfirm() {
    if (this.data.invalidCount > 0) {
      wx.showModal({
        title: '有商品不可下单',
        content: `${this.data.invalidCount} 件商品已失效或库存不足，请先清除后再结算`,
        confirmText: '清除失效',
        cancelText: '返回',
        success: (res) => {
          if (res.confirm) this.clearInvalid()
        }
      })
      return
    }
    if (!this.data.cart.length || this.data.count === 0) {
      wx.showToast({ title: '购物车为空', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/packageOrder/confirm/confirm' })
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  }
})
