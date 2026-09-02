const api = require('../../utils/api')
const auth = require('../../utils/auth')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const pickupUtil = require('../../utils/pickup')
const tabCartBadge = require('../../utils/tabCartBadge')
const app = getApp()

const ORDERS_CACHE_MS = 30 * 1000

Page({
  data: {
    orders: [],
    page: 1,
    pageSize: 15,
    totalPages: 0,
    loading: false,
    loadingMore: false,
    noMore: false,
    reorderingId: null,
    storeId: null,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: ''
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    const ctx = dineCtx.currentDisplay()
    this.applyDineContext(ctx)
    const silent = this.data.orders.length > 0
    this.loadOrders(true, silent, ctx.storeId)
    dineCtx.ensureDineContext().then((fresh) => {
      if ((fresh.storeId || null) !== (this.data.storeId || null)) {
        this.applyDineContext(fresh)
        this.loadOrders(true, !!this.data.orders.length, fresh.storeId)
      }
    })
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onTableChange(e) {
    const ctx = e.detail
    if (!ctx) return
    this.applyDineContext(ctx)
  },

  onPullDownRefresh() {
    this._ordersLoadedAt = 0
    dineCtx.ensureDineContext({ force: true })
      .then((ctx) => {
        this.applyDineContext(ctx)
        return this.loadOrders(true, false, ctx.storeId)
      })
      .finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    this.loadOrders(false)
  },

  async loadOrders(reset = true, silent = false, storeId = null) {
    if (reset) {
      if (this.data.loading) return
      const sid = storeId != null ? storeId : this.data.storeId
      const cacheKey = `${sid || 'all'}`
      const now = Date.now()
      if (
        silent
        && this._ordersCacheKey === cacheKey
        && this._ordersLoadedAt
        && now - this._ordersLoadedAt < ORDERS_CACHE_MS
      ) {
        return
      }
      if (!silent && !this.data.orders.length) {
        this.setData({ loading: true, page: 1, noMore: false, orders: [] })
      } else {
        this.setData({ page: 1, noMore: false })
      }
    } else {
      if (this.data.loading || this.data.loadingMore || this.data.noMore) return
      this.setData({ loadingMore: true })
    }
    const page = reset ? 1 : this.data.page + 1
    const sid = storeId != null ? storeId : this.data.storeId
    try {
      await auth.ensureLogin()
      const params = { page, page_size: this.data.pageSize }
      if (sid) params.store_id = sid
      const res = await api.get('/orders/mine', params)
      const batch = ((res && res.items) || []).map((o) => ({
        ...o,
        ...pickupUtil.decorate(o),
        discount_amount: Number(o.discount_amount || 0),
        timeText: this.formatTime(o.created_at),
        payLabel: this.payLabel(o.payment_status),
        placeText: [o.store_name, o.table && (o.table.table_name || o.table.table_no)].filter(Boolean).join(' · ')
      }))
      const totalPages = (res && res.total_pages) || 0
      const orders = reset ? batch : this.data.orders.concat(batch)
      this.setData({
        orders,
        page,
        totalPages,
        noMore: page >= totalPages || batch.length < this.data.pageSize
      })
      if (reset) {
        this._ordersLoadedAt = Date.now()
        this._ordersCacheKey = `${sid || 'all'}`
      }
    } catch (e) {
      if (reset) this.setData({ orders: [] })
      if (e && e.message) wx.showToast({ title: e.message, icon: 'none' })
    } finally {
      this.setData({ loading: false, loadingMore: false })
    }
  },

  payLabel(status) {
    const map = {
      PAID: '已支付',
      UNPAID: '待支付',
      PENDING: '待支付',
      REFUNDED: '已退款',
      PARTIAL_REFUND: '部分退款'
    }
    return map[status] || status || ''
  },

  formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
    const pad = (n) => (n < 10 ? `0${n}` : `${n}`)
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  },

  openOrder(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: `/packageOrder/result/result?order_id=${id}` })
  },

  async reorderOrder(e) {
    const id = e.currentTarget.dataset.id
    if (!id || this.data.reorderingId) return
    this.setData({ reorderingId: id })
    try {
      wx.showLoading({ title: '生成订单中' })
      const res = await api.post(`/orders/${id}/reorder`, {})
      const items = (res && res.cart_items) || []
      if (!items.length) {
        throw new Error((res && res.message) || '没有可复购商品')
      }
      const cart = items.map((it) => ({
        product_id: it.product_id,
        name: it.name,
        quantity: it.quantity || 1,
        option_ids: it.option_ids || [],
        option_labels: it.option_labels || [],
        option_text: it.option_text || (it.option_labels || []).join('、'),
        unit_price: it.unit_price,
        subtotal: it.subtotal,
        imageUrl: media.resolveUrl(it.imageUrl || it.main_image || ''),
        main_image: it.main_image || '',
        no_member_discount: !!it.no_member_discount
      }))
      auth.saveCart(cart)
      wx.hideLoading()
      const skipped = (res && res.skipped) || []
      if (skipped.length) {
        wx.showModal({
          title: '已加入购物车',
          content: `已加入 ${cart.length} 件；${skipped.length} 件不可用已跳过`,
          showCancel: false,
          success: () => wx.switchTab({ url: '/pages/cart/cart' })
        })
      } else {
        wx.showToast({ title: '已加入购物车', icon: 'success' })
        setTimeout(() => wx.switchTab({ url: '/pages/cart/cart' }), 500)
      }
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: err.message || '复购失败', icon: 'none' })
    } finally {
      this.setData({ reorderingId: null })
    }
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  }
})
