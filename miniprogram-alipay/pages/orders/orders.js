const api = require('../../utils/api')
const auth = require('../../utils/auth')
const media = require('../../utils/media')
const dineCtx = require('../../utils/context')
const pickupUtil = require('../../utils/pickup')
const navBar = require('../../utils/navBar')

const ORDERS_CACHE_MS = 30 * 1000
const SCOPE_STORAGE_KEY = 'orders_scope_pending'

const DINE_TABS = [
  { key: '', label: '全部' },
  { key: 'PENDING_PAYMENT', label: '待支付' },
  { key: 'PAID,CONFIRMED,PREPARING,COOKING,READY', label: '进行中' },
  { key: 'COMPLETED', label: '已完成' },
  { key: 'CANCELLED,REFUND_PENDING,REFUNDED', label: '取消/退款' }
]

const RECHARGE_TABS = [
  { key: '', label: '全部' },
  { key: 'PAID', label: '已到账' },
  { key: 'PENDING', label: '待支付' },
  { key: 'CANCELLED,EXPIRED,FAILED', label: '取消/失败' }
]

Page({
  data: {
    orderScope: 'dine',
    tabs: DINE_TABS,
    activeTab: '',
    list: [],
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
    storeLogo: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    hasTable: false,
    isTakeawayChannel: false
  },

  onLoad() {
    this._unsubDine = dineCtx.subscribeDineContext((ctx) => {
      this.applyDineContext(ctx)
    })
  },

  onUnload() {
    if (typeof this._unsubDine === 'function') {
      this._unsubDine()
      this._unsubDine = null
    }
  },

  onShow() {
    navBar.pinAlipayNavBar()
    let pending = null
    try {
      const res = my.getStorageSync({ key: SCOPE_STORAGE_KEY })
      pending = res && res.data
    } catch (_) {}
    if (pending === 'recharge' || pending === 'dine') {
      try { my.removeStorageSync({ key: SCOPE_STORAGE_KEY }) } catch (_) {}
      if (pending !== this.data.orderScope) {
        this.applyScope(pending, { reload: false })
      }
    }
    const ctx = dineCtx.currentDisplay()
    this.applyDineContext(ctx)
    const silent = this.data.list.length > 0
    this.reloadCurrent(true, silent, ctx.storeId)
    dineCtx.ensureDineContext().then((fresh) => {
      if ((fresh.storeId || null) !== (this.data.storeId || null)) {
        this.applyDineContext(fresh)
        this.reloadCurrent(true, !!this.data.list.length, fresh.storeId)
      }
    })
  },

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    this.reloadCurrent(true, false, ctx.storeId)
  },

  onPullDownRefresh() {
    this._listLoadedAt = 0
    dineCtx.ensureDineContext({ force: true })
      .then((ctx) => {
        this.applyDineContext(ctx)
        return this.reloadCurrent(true, false, ctx.storeId)
      })
      .finally(() => my.stopPullDownRefresh())
  },

  onReachBottom() {
    this.reloadCurrent(false)
  },

  onScopeChange(e) {
    const scope = (e.currentTarget.dataset && e.currentTarget.dataset.scope) || 'dine'
    if (scope === this.data.orderScope) return
    this.applyScope(scope, { reload: true })
  },

  applyScope(scope, { reload = true } = {}) {
    const next = scope === 'recharge' ? 'recharge' : 'dine'
    this._listLoadedAt = 0
    this.setData({
      orderScope: next,
      tabs: next === 'recharge' ? RECHARGE_TABS : DINE_TABS,
      activeTab: '',
      list: [],
      page: 1,
      noMore: false,
      loading: false,
      loadingMore: false
    })
    if (reload) this.reloadCurrent(true, false)
  },

  onTabChange(e) {
    const key = (e.currentTarget.dataset && e.currentTarget.dataset.key) || ''
    if (key === this.data.activeTab) return
    this.setData({ activeTab: key })
    this._listLoadedAt = 0
    this.reloadCurrent(true, false)
  },

  reloadCurrent(reset = true, silent = false, storeId = null) {
    if (this.data.orderScope === 'recharge') {
      return this.loadRecharges(reset, silent)
    }
    return this.loadOrders(reset, silent, storeId)
  },

  async loadOrders(reset = true, silent = false, storeId = null) {
    if (reset) {
      if (this.data.loading) return
      const sid = storeId != null ? storeId : this.data.storeId
      const cacheKey = `dine::${sid || 'all'}::${this.data.activeTab || 'all'}`
      const now = Date.now()
      if (
        silent
        && this._listCacheKey === cacheKey
        && this._listLoadedAt
        && now - this._listLoadedAt < ORDERS_CACHE_MS
      ) {
        return
      }
      if (!silent && !this.data.list.length) {
        this.setData({ loading: true, page: 1, noMore: false, list: [] })
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
      if (this.data.activeTab) params.status = this.data.activeTab
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
      const list = reset ? batch : this.data.list.concat(batch)
      this.setData({
        list,
        page,
        totalPages,
        noMore: page >= totalPages || batch.length < this.data.pageSize
      })
      if (reset) {
        this._listLoadedAt = Date.now()
        this._listCacheKey = `dine::${sid || 'all'}::${this.data.activeTab || 'all'}`
      }
    } catch (e) {
      if (reset) this.setData({ list: [] })
      if (e && e.message) my.showToast({ content: e.message, type: 'none' })
    } finally {
      this.setData({ loading: false, loadingMore: false })
    }
  },

  async loadRecharges(reset = true, silent = false) {
    if (reset) {
      if (this.data.loading) return
      const cacheKey = `recharge::${this.data.activeTab || 'all'}`
      const now = Date.now()
      if (
        silent
        && this._listCacheKey === cacheKey
        && this._listLoadedAt
        && now - this._listLoadedAt < ORDERS_CACHE_MS
      ) {
        return
      }
      if (!silent && !this.data.list.length) {
        this.setData({ loading: true, page: 1, noMore: false, list: [] })
      } else {
        this.setData({ page: 1, noMore: false })
      }
    } else {
      if (this.data.loading || this.data.loadingMore || this.data.noMore) return
      this.setData({ loadingMore: true })
    }
    const page = reset ? 1 : this.data.page + 1
    try {
      await auth.ensureLogin()
      const params = { page, page_size: this.data.pageSize }
      if (this.data.activeTab) params.status = this.data.activeTab
      const res = await api.get('/wallet/recharge/mine', params)
      const batch = ((res && res.items) || []).map((r) => ({
        ...r,
        credit_gift: Number(r.credit_gift || 0),
        timeText: this.formatTime(r.paid_at || r.created_at)
      }))
      const totalPages = (res && res.total_pages) || 0
      const list = reset ? batch : this.data.list.concat(batch)
      this.setData({
        list,
        page,
        totalPages,
        noMore: page >= totalPages || batch.length < this.data.pageSize
      })
      if (reset) {
        this._listLoadedAt = Date.now()
        this._listCacheKey = `recharge::${this.data.activeTab || 'all'}`
      }
    } catch (e) {
      if (reset) this.setData({ list: [] })
      if (e && e.message) my.showToast({ content: e.message, type: 'none' })
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
    my.navigateTo({ url: `/packageOrder/result/result?order_id=${id}` })
  },

  async reorderOrder(e) {
    const id = e.currentTarget.dataset.id
    if (!id || this.data.reorderingId) return
    const item = this.data.list.find((order) => String(order.id) === String(id))
    if (item && !item.can_reorder) return
    this.setData({ reorderingId: id })
    try {
      my.showLoading({ content: '生成订单中' })
      const res = await api.post(`/orders/${id}/reorder`, {})
      const items = (res && res.cart_items) || []
      if (!items.length) throw new Error((res && res.message) || '没有可复购商品')
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
      const reorderStoreId = (res && res.store_id)
        || (item && item.store_id)
        || auth.currentStoreId()
        || null
      const currentSid = auth.currentStoreId()
      if (
        reorderStoreId != null
        && currentSid != null
        && Number(reorderStoreId) !== Number(currentSid)
      ) {
        my.hideLoading()
        const goOn = await new Promise((resolve) => {
          my.confirm({
            title: '门店不一致',
            content: '该订单属于其他门店，当前已绑定本店桌台。继续加入后商品可能无法在本桌结算，是否仍要加入？',
            confirmButtonText: '仍要加入',
            cancelButtonText: '取消',
            success: (r) => resolve(!!(r && r.confirm)),
            fail: () => resolve(false)
          })
        })
        if (!goOn) return
        my.showLoading({ content: '生成订单中' })
      }
      auth.saveCart(cart, reorderStoreId != null ? { store_id: reorderStoreId } : {})
      my.hideLoading()
      const skipped = (res && res.skipped) || []
      const goMenuSheet = () => {
        try {
          const app = getApp()
          if (app && app.globalData) app.globalData._openCartSheet = true
        } catch (_) {}
        my.switchTab({ url: '/pages/menu/menu' })
      }
      my.alert({
        title: '已加入点餐',
        content: skipped.length
          ? `已加入 ${cart.length} 件；${skipped.length} 件不可用已跳过`
          : `已按历史订单加入 ${cart.length} 件`,
        success: goMenuSheet
      })
    } catch (err) {
      my.hideLoading()
      my.showToast({ content: err.message || '复购失败', type: 'fail' })
    } finally {
      this.setData({ reorderingId: null })
    }
  },

  goMenu() {
    my.switchTab({ url: '/pages/menu/menu' })
  },

  goRecharge() {
    my.navigateTo({ url: '/pages/recharge/recharge' })
  }
})
