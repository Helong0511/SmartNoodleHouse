const api = require('../../utils/api')
const payUtil = require('../../utils/pay')
const subscribe = require('../../utils/subscribe')
const prefUtil = require('../../utils/preferences')
const pickupUtil = require('../../utils/pickup')
const dineCtx = require('../../utils/context')
const app = getApp()

function seatFieldsFromOrder(order) {
  const table = (order && order.table) || null
  const dine = dineCtx.displayPayload(null, table)
  const takeaway =
    dine.isTakeawayChannel ||
    String((order && order.dining_type) || '').toUpperCase() === 'TAKEAWAY'
  if (takeaway) {
    return {
      tableName: dine.tableName || '外带自取',
      tableNo: '',
      seatLabel: '用餐'
    }
  }
  return {
    tableName: dine.tableName || '',
    tableNo: dine.tableNo || '',
    seatLabel: dine.seatLabel || '桌号'
  }
}

const PAY_LABEL = {
  PAID: '已支付',
  UNPAID: '待支付',
  PENDING: '待支付',
  REFUNDED: '已退款',
  PARTIAL_REFUND: '部分退款'
}

const PAGE_TITLE = {
  PENDING_PAYMENT: '待支付',
  PAID: '已支付',
  CONFIRMED: '已接单',
  PREPARING: '备餐中',
  COOKING: '制作中',
  READY: '餐品已备好',
  COMPLETED: '订单已完成',
  CANCELLED: '订单已取消',
  REFUNDED: '已退款'
}

const STATUS_STEPS = [
  { key: 'PENDING_PAYMENT', label: '待支付' },
  { key: 'PAID', label: '已支付' },
  { key: 'CONFIRMED', label: '已接单' },
  { key: 'PREPARING', label: '备餐中' },
  { key: 'COOKING', label: '制作中' },
  { key: 'READY', label: '待取餐' },
  { key: 'COMPLETED', label: '已完成' }
]

function headerIcon(status) {
  if (status === 'CANCELLED' || status === 'REFUNDED') return '✕'
  if (status === 'READY' || status === 'COMPLETED') return '✓'
  return '…'
}

Page({
  data: {
    loading: true,
    order: {},
    orderItems: [],
    hasPacking: false,
    hasDiscount: false,
    discountAmount: '0.00',
    hasCouponDiscount: false,
    couponDiscountAmount: '0.00',
    hasVoucherDiscount: false,
    voucherDiscountAmount: '0.00',
    pointsEarned: 0,
    memberPoints: 0,
    pageTitle: '订单详情',
    headerIcon: '…',
    statusSteps: [],
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    showPickupBanner: false,
    pickupCode: '',
    pickupLabel: '',
    pickupSub: '',
    customerStatusLabel: '',
    pickupHint: '',
    isTakeaway: false,
    isReady: false
  },

  onLoad(query) {
    this.orderId = query.order_id
    this.pointsEarned = Number(query.points_earned || 0)
    this._refreshWithSync()
    subscribe.requestOrderReadySubscribe()
    this._pollTimer = setInterval(() => this._refreshWithSync(true), 8000)
  },

  onShow() {
    if (this.orderId) this._refreshWithSync(true)
  },

  onUnload() {
    if (this._pollTimer) clearInterval(this._pollTimer)
  },

  onPullDownRefresh() {
    this._refreshWithSync(false).finally(() => wx.stopPullDownRefresh())
  },

  async _refreshWithSync(silent) {
    const pending = payUtil.readPending()
    const needSync =
      pending &&
      this.orderId &&
      String(pending.orderId) === String(this.orderId)
    if (needSync) {
      await payUtil.confirmPending(api, {
        orderId: this.orderId,
        paymentNo: pending.paymentNo
      })
    }
    await this.loadOrder(silent)
    const order = this.data.order || {}
    if (order.payment_status === 'PAID' || order.status === 'PAID') {
      payUtil.clearPending(this.orderId)
    }
  },

  onShareAppMessage() {
    const o = this.data.order || {}
    const title = `${o.store_name || '智能面馆'} · ${this.data.pageTitle}`
    const oid = this.orderId || o.id || ''
    return {
      title,
      // 分享 path 勿以 / 开头，且必须带有效 order_id，避免打开空页
      path: oid
        ? `packageOrder/result/result?order_id=${oid}`
        : 'pages/index/index'
    }
  },

  buildSteps(status) {
    const cur = status || ''
    if (cur === 'CANCELLED') {
      return [{ key: 'CANCELLED', label: '已取消', done: true, active: true }]
    }
    const flow = ['PENDING_PAYMENT', 'PAID', 'CONFIRMED', 'PREPARING', 'COOKING', 'READY', 'COMPLETED']
    const idx = flow.indexOf(cur)
    return STATUS_STEPS.map((s) => {
      const stepIdx = flow.indexOf(s.key)
      return {
        ...s,
        done: idx >= 0 && stepIdx <= idx,
        active: s.key === cur
      }
    }).filter((s) => s.key !== 'PENDING_PAYMENT' || cur === 'PENDING_PAYMENT' || idx > 0)
  },

  formatItems(items) {
    return (items || []).map((it) => {
      const opts = (it.options || []).map((o) => o.name || o.code).filter(Boolean)
      const optText = opts.length ? `（${opts.join('、')}）` : ''
      return {
        name: it.product_name || it.name || '商品',
        quantity: it.quantity || 1,
        subtotal: it.subtotal || it.line_total || '',
        optionText: optText
      }
    })
  },

  async loadOrder(silent) {
    if (!this.orderId) return
    if (!silent) this.setData({ loading: !this.data.order.order_no })
    try {
      const order = await api.get(`/orders/${this.orderId}`)
      const fee = Number(order && order.packing_fee)
      const discount = parseFloat(order && order.discount_amount) || 0
      const couponDisc = parseFloat(order && order.coupon_discount) || 0
      const voucherDisc = parseFloat(order && order.voucher_discount) || 0
      const pickup = pickupUtil.decorate(order)
      const pageTitle = PAGE_TITLE[order.status] || order.status_label || '订单详情'
      wx.setNavigationBarTitle({ title: pageTitle })
      this.setData({
        order,
        ...pickup,
        orderItems: this.formatItems(order.items),
        hasPacking: fee > 0,
        hasDiscount: discount > 0,
        hasCouponDiscount: couponDisc > 0,
        discountAmount: order.discount_amount || '0.00',
        couponDiscountAmount: order.coupon_discount || '0.00',
        hasVoucherDiscount: voucherDisc > 0,
        voucherDiscountAmount: order.voucher_discount || '0.00',
        pointsEarned: this.pointsEarned || (order.payment_status === 'PAID' ? Math.floor(parseFloat(order.total_amount) || 0) : 0),
        memberLevelName: order.member_level_name || '',
        payLabel: PAY_LABEL[order.payment_status] || order.payment_status_label || order.payment_status || '',
        pageTitle,
        headerIcon: headerIcon(order.status),
        statusSteps: this.buildSteps(order.status),
        storeName: order.store_name || '',
        storeNo: order.store_no || '',
        ...seatFieldsFromOrder(order),
        loading: false
      })
      if (order && (order.status === 'PAID' || order.status === 'COMPLETED' || order.payment_status === 'PAID')) {
        prefUtil.clearPrefsCache(app)
      }
    } catch (e) {
      this.setData({ loading: false })
      if (!silent) wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  copyOrderNo() {
    const no = this.data.order && this.data.order.order_no
    if (!no) return
    wx.setClipboardData({
      data: no,
      success: () => wx.showToast({ title: '已复制订单号', icon: 'success' })
    })
  },

  copyPickupCode() {
    const code = this.data.pickupCode
    if (!code) return
    wx.setClipboardData({
      data: code,
      success: () => wx.showToast({ title: '已复制取餐号', icon: 'success' })
    })
  },

  async repay() {
    if (!this.orderId) return
    try {
      wx.showLoading({ title: '拉起支付' })
      const payRes = await api.post(`/payments/orders/${this.orderId}/pay`, {
        provider: 'WECHAT',
        channel_type: 'miniprogram'
      })
      try {
        await payUtil.payAndConfirm(api, payRes, this.orderId)
      } catch (payErr) {
        if (payErr && payErr.payCancel) {
          wx.hideLoading()
          wx.showToast({ title: '已取消支付', icon: 'none' })
          await this._refreshWithSync(true)
          return
        }
        throw payErr
      }
      wx.hideLoading()
      await subscribe.requestOrderReadySubscribe()
      await this._refreshWithSync()
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: e.message || '支付失败', icon: 'none' })
      await this._refreshWithSync(true)
    }
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  }
})
