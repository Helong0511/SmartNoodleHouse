const api = require('../../utils/api')
const payUtil = require('../../utils/pay')
const prefUtil = require('../../utils/preferences')
const pickupUtil = require('../../utils/pickup')
const dineCtx = require('../../utils/context')
const tracker = require('../../utils/tracker')
const app = getApp()

// 轻量评价标签（与后端 feedback_service.VERDICT_WHITELIST 对齐）
const FEEDBACK_TAGS = [
  { code: 'TASTY', label: '好吃对味', tone: 'pos' },
  { code: 'WILL_REORDER', label: '想再点', tone: 'pos' },
  { code: 'FRESH', label: '新鲜', tone: 'pos' },
  { code: 'GOOD_PORTION', label: '分量足', tone: 'pos' },
  { code: 'PORTION_OK', label: '分量合适', tone: 'neu' },
  { code: 'TOO_SPICY', label: '太辣', tone: 'neg' },
  { code: 'TOO_SWEET', label: '太甜', tone: 'neg' },
  { code: 'TOO_SOUR', label: '太酸', tone: 'neg' },
  { code: 'TOO_SALTY', label: '太咸', tone: 'neg' },
  { code: 'TOO_BITTER', label: '太苦', tone: 'neg' },
  { code: 'TOO_BLAND', label: '太淡', tone: 'neg' },
  { code: 'TOO_SMALL', label: '分量少', tone: 'neg' },
  { code: 'TOO_EXPENSIVE', label: '不太值', tone: 'neg' },
  { code: 'NOT_FRESH', label: '不新鲜', tone: 'neg' },
  { code: 'DISLIKE', label: '不喜欢', tone: 'neg' }
]
const FEEDBACK_PAID_STATUSES = ['PAID', 'PARTIAL_REFUND']
const FEEDBACK_SKIP_PREFIX = 'feedback_skip_'

function buildFeedbackTags(selected) {
  return FEEDBACK_TAGS.map((t) => Object.assign({}, t, { on: selected.indexOf(t.code) >= 0 }))
}

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
    memberLevelName: '',
    pageTitle: '订单详情',
    headerIcon: '…',
    statusSteps: [],
    payLabel: '',
    storeName: '',
    storeNo: '',
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
    isReady: false,
    showMemberBanner: false,
    activatingMember: false,
    tasteHint: '',
    feedbackVisible: false,
    feedbackSubmitting: false,
    feedbackItems: []
  },

  onLoad(query) {
    this.orderId = query.order_id
    this.pointsEarned = Number(query.points_earned || 0)
    this._refreshWithSync()
    this._pollTimer = setInterval(() => this._refreshWithSync(true), 8000)
  },

  onShow() {
    if (this.orderId) this._refreshWithSync(true)
  },

  onUnload() {
    if (this._pollTimer) clearInterval(this._pollTimer)
  },

  onPullDownRefresh() {
    this._refreshWithSync(false).finally(() => my.stopPullDownRefresh())
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
      my.setNavigationBar({ title: pageTitle })
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
        this._maybeShowMemberBanner(order)
      }
      if (FEEDBACK_PAID_STATUSES.indexOf(String(order.payment_status || '').toUpperCase()) >= 0) {
        this._loadFeedback()
      }
    } catch (e) {
      this.setData({ loading: false })
      if (!silent) my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
    }
  },

  async _maybeShowMemberBanner(order) {
    if (this._memberBannerChecked) return
    try {
      const auth = require('../../utils/auth')
      const me = await auth.fetchCustomerMeSilent()
      const isMember = me && (me.is_member === true || String(me.member_status || '').toUpperCase() === 'MEMBER')
      if (isMember) {
        this.setData({ showMemberBanner: false })
        this._memberBannerChecked = true
        return
      }
      const items = (order && order.items) || []
      const tasteParts = []
      items.slice(0, 2).forEach((it) => {
        const name = it.product_name || it.name || ''
        const opts = (it.options || []).map((o) => o.name || o.code).filter(Boolean)
        if (name) tasteParts.push(opts.length ? `${name}·${opts.slice(0, 2).join('·')}` : name)
      })
      this.setData({
        showMemberBanner: true,
        tasteHint: tasteParts.join('、') || '您刚点的口味'
      })
      this._memberBannerChecked = true
    } catch (_) {}
  },

  dismissMemberBanner() {
    this.setData({ showMemberBanner: false })
  },

  async activateMemberFromResult() {
    if (this.data.activatingMember) return
    try {
      this.setData({ activatingMember: true })
      const auth = require('../../utils/auth')
      const res = await auth.activateMember({})
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      this.setData({ showMemberBanner: false })
      const tip = (res && res.welcome_coupon_issued)
        ? `已开启会员，赠送「${(res.welcome_coupon && res.welcome_coupon.name) || '新客券'}」`
        : '已开启智能会员'
      my.showToast({ content: tip, type: 'success' })
    } catch (e) {
      my.showToast({ content: (e && e.message) || '开通失败', type: 'fail' })
    } finally {
      this.setData({ activatingMember: false })
    }
  },

  copyOrderNo() {
    const no = this.data.order && this.data.order.order_no
    if (!no) return
    my.setClipboard({ text: no, success: () => my.showToast({ content: '已复制订单号', type: 'success' }) })
  },

  copyPickupCode() {
    const code = this.data.pickupCode
    if (!code) return
    my.setClipboard({ text: code, success: () => my.showToast({ content: '已复制取餐号', type: 'success' }) })
  },

  async repay() {
    if (!this.orderId) return
    try {
      my.showLoading({ content: '拉起支付' })
      const payRes = await api.post(`/payments/orders/${this.orderId}/pay`, {
        provider: 'ALIPAY',
        channel_type: 'miniprogram'
      })
      try {
        await payUtil.payAndConfirm(api, payRes, this.orderId)
      } catch (payErr) {
        if (payErr && payErr.payCancel) {
          my.hideLoading()
          my.showToast({ content: '已取消支付', type: 'none' })
          await this._refreshWithSync(true)
          return
        }
        throw payErr
      }
      my.hideLoading()
      await this._refreshWithSync()
    } catch (e) {
      my.hideLoading()
      my.showToast({ content: (e && e.message) || '支付失败', type: 'fail' })
      await this._refreshWithSync(true)
    }
  },

  async _loadFeedback() {
    if (!this.orderId || this._feedbackLoaded) return
    this._feedbackLoaded = true
    try {
      const skipped = my.getStorageSync({ key: FEEDBACK_SKIP_PREFIX + this.orderId }).data
      const data = await api.get(`/miniprogram/orders/${this.orderId}/feedback`)
      if (!data || !data.can_evaluate) return
      const items = (data.items || []).map((it) => {
        const selected = it.feedback ? (it.feedback.verdicts || []) : []
        return {
          product_id: it.product_id,
          product_name: it.product_name,
          given: it.feedback || null,
          selected,
          tags: buildFeedbackTags(selected)
        }
      })
      const hasPending = items.some((it) => !it.given)
      this.setData({ feedbackItems: items, feedbackVisible: hasPending && !skipped })
    } catch (_) {
      this._feedbackLoaded = false
    }
  },

  toggleFeedbackTag(e) {
    const ds = e.currentTarget.dataset
    const idx = Number(ds.idx)
    const code = ds.code
    const items = this.data.feedbackItems.slice()
    const it = items[idx]
    if (!it || it.given) return
    const selected = it.selected.slice()
    const pos = selected.indexOf(code)
    if (pos >= 0) {
      selected.splice(pos, 1)
    } else {
      if (selected.length >= 5) {
        my.showToast({ content: '每道最多选 5 个', type: 'none' })
        return
      }
      selected.push(code)
    }
    it.selected = selected
    it.tags = buildFeedbackTags(selected)
    this.setData({ feedbackItems: items })
  },

  async submitFeedback() {
    if (this.data.feedbackSubmitting) return
    const pending = this.data.feedbackItems.filter((it) => !it.given && it.selected.length)
    if (!pending.length) {
      my.showToast({ content: '点选标签后提交，或跳过', type: 'none' })
      return
    }
    this.setData({ feedbackSubmitting: true })
    try {
      await api.post(`/miniprogram/orders/${this.orderId}/feedback`, {
        items: pending.map((it) => ({ product_id: it.product_id, verdicts: it.selected }))
      })
      pending.forEach((it) => {
        const hasNeg = it.selected.some((c) => {
          const tag = FEEDBACK_TAGS.find((t) => t.code === c)
          return tag && tag.tone === 'neg'
        })
        const hasPos = it.selected.some((c) => {
          const tag = FEEDBACK_TAGS.find((t) => t.code === c)
          return tag && tag.tone === 'pos'
        })
        const sentiment = hasNeg && !hasPos ? 'NEGATIVE' : (hasPos && !hasNeg ? 'POSITIVE' : 'NEUTRAL')
        tracker.feedback(it.product_id, {
          scene: 'order_result',
          payload: { sentiment, verdicts: it.selected }
        })
      })
      my.setStorageSync({ key: FEEDBACK_SKIP_PREFIX + this.orderId, data: 1 })
      this.setData({ feedbackVisible: false })
      my.showToast({ content: '感谢反馈', type: 'success', duration: 1200 })
    } catch (e) {
      my.showToast({ content: (e && e.message) || '提交失败', type: 'none' })
    } finally {
      this.setData({ feedbackSubmitting: false })
    }
  },

  skipFeedback() {
    my.setStorageSync({ key: FEEDBACK_SKIP_PREFIX + this.orderId, data: 1 })
    this.setData({ feedbackVisible: false })
  },

  goMenu() {
    my.switchTab({ url: '/pages/menu/menu' })
  }
})
