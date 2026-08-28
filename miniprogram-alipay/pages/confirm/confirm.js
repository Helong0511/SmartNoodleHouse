const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const payUtil = require('../../utils/pay')
const voucherUtil = require('../../utils/voucher')
const app = getApp()

function memberDiscountText(rate) {
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  return `${Math.round(n * 100) / 10}折`
}

function pickBestCoupon(coupons, afterMember, calcFn) {
  let best = null
  let bestDisc = 0
  for (const c of coupons || []) {
    const disc = calcFn(afterMember, c)
    if (disc <= 0) continue
    if (disc > bestDisc) {
      bestDisc = disc
      best = c
    } else if (disc === bestDisc && best) {
      const expA = c.expires_at || ''
      const expB = best.expires_at || ''
      if (expA && expB && expA < expB) best = c
    }
  }
  return best
}

function pickBestVoucher(vouchers, payable, calcFn) {
  let best = null
  let bestDisc = 0
  for (const v of vouchers || []) {
    const disc = calcFn(payable, v)
    if (disc <= 0) continue
    if (disc > bestDisc) {
      bestDisc = disc
      best = v
    } else if (disc === bestDisc && best) {
      const expA = v.expires_at || ''
      const expB = best.expires_at || ''
      if (expA && expB && expA < expB) best = v
    }
  }
  return best
}

Page({
  data: {
    remark: '',
    loading: false,
    tableName: '',
    tableToken: '',
    tableOptions: [],
    pickedLabel: '',
    needPack: false,
    cartPreview: [],
    subtotal: '0.00',
    packingFee: '0.00',
    discountAmount: '0.00',
    payEstimate: '0.00',
    hasDiscount: false,
    isMember: false,
    memberLevelName: '',
    memberDiscountText: '',
    coupons: [],
    couponPickerLabels: ['不使用优惠券'],
    selectedCouponId: 0,
    selectedCouponLabel: '不使用优惠券',
    couponDiscountAmount: '0.00',
    hasCouponDiscount: false,
    vouchers: [],
    voucherPickerLabels: ['不使用代金券'],
    selectedVoucherId: 0,
    selectedVoucherLabel: '不使用代金券',
    voucherDiscountAmount: '0.00',
    hasVoucherDiscount: false,
    boxPriceMap: {},
    needScanHint: false,
    hasSpecialItems: false,
  },
  storeId() {
    return (app.globalData.table && app.globalData.table.store_id)
      || (app.globalData.store && app.globalData.store.id)
      || null
  },
  async loadProductCatalog() {
    if (this._productCatalog && this._productCatalog.length) {
      return this._productCatalog
    }
    const sid = this.storeId()
    const products = await api.get('/products', { page_size: 100, ...(sid ? { store_id: sid } : {}) }, false)
    const items = (products && products.items) || []
    this._productCatalog = items
    const boxMap = {}
    items.forEach((p) => {
      boxMap[p.id] = parseFloat(p.box_price || 0) || 0
    })
    this.setData({ boxPriceMap: boxMap })
    return items
  },
  onShow() {
    this._memberRate = 1
    this._couponManualPick = false
    this._voucherManualPick = false
    this._selectedCoupon = null
    this._selectedVoucher = null
    this.setData({
      selectedCouponId: 0,
      selectedCouponLabel: '不使用优惠券',
      selectedVoucherId: 0,
      selectedVoucherLabel: '不使用代金券',
    })
    this.bootstrapCheckout()
  },
  async bootstrapCheckout() {
    await Promise.all([this.loadMember(), this.loadCoupons(), this.loadVouchers()])
    await this.resolveTable()
    await this.refreshSummary()
  },
  clearTableBinding() {
    app.globalData.table = null
    app.globalData.tableToken = ''
    try {
      my.removeStorageSync({ key: 'table_token' })
      my.removeStorageSync({ key: 'dine_table' })
    } catch (_) {}
    this.setData({ tableName: '', tableToken: '', pickedLabel: '' })
  },
  async loadMember() {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const profile = await api.get('/miniprogram/customers/me')
      const rate = profile && profile.is_member && profile.member_discount_rate
        ? parseFloat(profile.member_discount_rate) : 1
      this._memberRate = Number.isFinite(rate) && rate > 0 && rate < 1 ? rate : 1
      this.setData({
        isMember: !!(profile && profile.is_member),
        memberLevelName: (profile && profile.member_level_name) || '',
        memberDiscountText: memberDiscountText(profile && profile.member_discount_rate)
      })
    } catch (_) {
      this._memberRate = 1
    }
  },
  async loadCoupons() {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const res = await api.get('/miniprogram/coupons/mine')
      const items = (res && res.items) || []
      this.setData({
        coupons: items,
        couponPickerLabels: ['不使用优惠券'].concat(items.map((c) => c.summary || c.name))
      })
    } catch (_) {
      this.setData({ coupons: [] })
    }
  },
  async loadVouchers() {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const res = await api.get('/miniprogram/vouchers/mine')
      const items = (res && res.items) || []
      this.setData({
        vouchers: items,
        voucherPickerLabels: ['不使用代金券'].concat(items.map((c) => c.summary || c.name))
      })
    } catch (_) {
      this.setData({ vouchers: [] })
    }
  },
  onPickCoupon(e) {
    this._couponManualPick = true
    const idx = Number(e.detail.value)
    if (idx <= 0) {
      this._selectedCoupon = null
      this.setData({ selectedCouponId: 0, selectedCouponLabel: '不使用优惠券' })
    } else {
      const picked = (this.data.coupons || [])[idx - 1]
      this._selectedCoupon = picked || null
      this.setData({
        selectedCouponId: picked ? picked.id : 0,
        selectedCouponLabel: picked ? (picked.summary || picked.name) : '不使用优惠券'
      })
    }
    this.refreshSummary()
  },
  onPickVoucher(e) {
    this._voucherManualPick = true
    const idx = Number(e.detail.value)
    if (idx <= 0) {
      this._selectedVoucher = null
      this.setData({ selectedVoucherId: 0, selectedVoucherLabel: '不使用代金券' })
    } else {
      const picked = (this.data.vouchers || [])[idx - 1]
      this._selectedVoucher = picked || null
      this.setData({
        selectedVoucherId: picked ? picked.id : 0,
        selectedVoucherLabel: picked ? (picked.summary || picked.name) : '不使用代金券'
      })
    }
    this.refreshSummary()
  },
  calcCouponDiscount(couponEligible, coupon) {
    if (!coupon || !coupon.id) return 0
    const eligible = couponEligible
    const minAmt = parseFloat(coupon.min_order_amount || 0)
    if (eligible < minAmt) return 0
    if (coupon.coupon_type === 'PERCENT') {
      const rate = parseFloat(coupon.discount_value)
      if (!Number.isFinite(rate) || rate <= 0 || rate >= 1) return 0
      return Math.round(eligible * (1 - rate) * 100) / 100
    }
    const fixed = parseFloat(coupon.discount_value) || 0
    return Math.min(fixed, eligible)
  },
  calcVoucherDiscount(payable, voucher, voucherEligible) {
    return voucherUtil.calcVoucherDiscount(payable, voucher, voucherEligible)
  },
  async refreshSummary() {
    const cart = auth.getCart()
    const { list, total } = cartUtil.cartSummary(cart)
    const boxMap = this.data.boxPriceMap || {}
    let enrichedList = list
    try {
      const productItems = await this.loadProductCatalog()
      if (productItems.length) {
        enrichedList = cartUtil.enrichCartFromProducts(list, productItems)
      }
    } catch (_) {}
    let packing = 0
    const cartPreview = enrichedList.map((item) => {
      const qty = item.quantity || 1
      packing += (boxMap[item.product_id] || 0) * qty
      return { ...item, imageUrl: item.imageUrl || media.resolveUrl(item.main_image || '') }
    })
    const sub = parseFloat(total) || 0
    const pack = Math.round(packing * 100) / 100
    const needPack = this.data.needPack
    const rate = this._memberRate || 1
    let discount = 0
    if (rate > 0 && rate < 1) {
      const discountable = cartUtil.calcDiscountableSubtotal(enrichedList)
      discount = Math.round(discountable * (1 - rate) * 100) / 100
    }
    const afterMember = Math.max(0, sub - discount)
    const couponEligible = cartUtil.calcCouponEligibleSubtotal(enrichedList, discount)

    if (!this._couponManualPick && (this.data.coupons || []).length) {
      const bestCoupon = pickBestCoupon(
        this.data.coupons,
        couponEligible,
        (amt, c) => this.calcCouponDiscount(amt, c)
      )
      this._selectedCoupon = bestCoupon
      this.setData({
        selectedCouponId: bestCoupon ? bestCoupon.id : 0,
        selectedCouponLabel: bestCoupon ? (bestCoupon.summary || bestCoupon.name) : '不使用优惠券',
      })
    }

    const couponDisc = this.calcCouponDiscount(couponEligible, this._selectedCoupon)
    let pay = afterMember - couponDisc
    const voucherEligible = cartUtil.calcVoucherEligiblePayable(
      couponEligible, couponDisc, pack, needPack
    )
    if (needPack) pay += pack

    if (!this._voucherManualPick && (this.data.vouchers || []).length) {
      const bestVoucher = pickBestVoucher(
        this.data.vouchers,
        voucherEligible,
        (amt, v) => this.calcVoucherDiscount(pay, v, amt)
      )
      this._selectedVoucher = bestVoucher
      this.setData({
        selectedVoucherId: bestVoucher ? bestVoucher.id : 0,
        selectedVoucherLabel: bestVoucher ? (bestVoucher.summary || bestVoucher.name) : '不使用代金券',
      })
    }

    let voucherDisc = this.calcVoucherDiscount(pay, this._selectedVoucher, voucherEligible)
    if (this._selectedVoucher && voucherDisc <= 0) {
      if (this._voucherManualPick) {
        my.showToast({
          content: '订单金额未满代金券面额，已取消使用',
          type: 'none',
          duration: 2500
        })
        this._voucherManualPick = false
      }
      this._selectedVoucher = null
      this.setData({
        selectedVoucherId: 0,
        selectedVoucherLabel: '不使用代金券'
      })
      voucherDisc = 0
    }
    pay = Math.max(0, Math.round((pay - voucherDisc) * 100) / 100)
    this.setData({
      cartPreview,
      subtotal: sub.toFixed(2),
      packingFee: pack.toFixed(2),
      discountAmount: discount.toFixed(2),
      hasDiscount: discount > 0,
      couponDiscountAmount: couponDisc.toFixed(2),
      hasCouponDiscount: couponDisc > 0,
      voucherDiscountAmount: voucherDisc.toFixed(2),
      hasVoucherDiscount: voucherDisc > 0,
      payEstimate: pay.toFixed(2),
      hasSpecialItems: enrichedList.some((i) => i.no_member_discount)
    })
  },
  async resolveTable() {
    let token = String(
      app.globalData.tableToken || (my.getStorageSync({ key: 'table_token' }).data) || ''
    ).trim()

    if (token) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'alipay' }, false)
        if (entry && entry.table && entry.table.qr_token) {
          const table = entry.table
          app.globalData.table = table
          app.globalData.tableToken = table.qr_token
          token = table.qr_token
          if (entry.store) {
            app.globalData.store = entry.store
            my.setStorageSync({ key: 'dine_store', data: entry.store })
          }
          my.setStorageSync({ key: 'table_token', data: table.qr_token })
          my.setStorageSync({ key: 'dine_table', data: table })
          this.setData({
            tableName: table.table_name || table.table_no,
            tableToken: token,
            needScanHint: false,
          })
          return
        }
      } catch (_) {}
      this.clearTableBinding()
    } else {
      this.clearTableBinding()
    }

    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const sid = (app.globalData.table && app.globalData.table.store_id)
        || (app.globalData.store && app.globalData.store.id)
        || null
      if (!sid) {
        this.setData({ tableOptions: [], needScanHint: true })
        return
      }
      const list = await api.get('/miniprogram/tables', { store_id: sid })
      const options = (list || []).map((t) => ({
        label: t.table_name || t.table_no,
        id: t.id,
        qr_token: t.qr_token || '',
        store_id: t.store_id,
        table_no: t.table_no,
        table_name: t.table_name,
      }))
      this.setData({ tableOptions: options, needScanHint: !options.length })
    } catch (_) {
      this.setData({ tableOptions: [], needScanHint: true })
    }
  },
  async onPickTable(e) {
    const idx = Number(e.detail.value)
    const t = this.data.tableOptions[idx]
    if (!t) return
    let token = t.qr_token
    if (!token && t.id) {
      try {
        const bound = await api.post('/miniprogram/tables/bind', {
          table_id: t.id,
          store_id: t.store_id || (app.globalData.table && app.globalData.table.store_id)
        })
        token = bound && bound.qr_token
        if (bound) { t.qr_token = token; t.store_id = bound.store_id }
      } catch (err) {
        my.showToast({ content: (err && err.message) || '选桌失败', type: 'fail' })
        return
      }
    }
    if (!token) {
      my.showToast({ content: '无法获取桌台令牌', type: 'fail' })
      return
    }
    app.globalData.table = { ...t, qr_token: token }
    app.globalData.tableToken = token
    my.setStorageSync({ key: 'table_token', data: token })
    this.setData({ tableName: t.label, tableToken: token, pickedLabel: t.label })
  },
  onPack(e) {
    this.setData({ needPack: String(e.currentTarget.dataset.pack) === '1' }, () => this.refreshSummary())
  },
  onRemark(e) { this.setData({ remark: e.detail.value }) },
  async submit() {
    const cart = auth.getCart()
    if (!cart.length) {
      my.showToast({ content: '购物车为空', type: 'fail' })
      return
    }
    const tableToken = String(
      this.data.tableToken || app.globalData.tableToken || (my.getStorageSync({ key: 'table_token' }).data) || ''
    ).trim()
    if (!tableToken) {
      my.showToast({ content: '请先扫描或选择桌号', type: 'fail' })
      await this.resolveTable()
      return
    }
    this.setData({ loading: true })
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const table = app.globalData.table
        || ((my.getStorageSync({ key: 'dine_table' }).data) || null)
      const order = await api.post('/orders', {
        store_id: (table && table.store_id) || (app.globalData.store && app.globalData.store.id),
        table_token: tableToken,
        channel: 'ALIPAY',
        need_pack: !!this.data.needPack,
        dining_type: this.data.needPack ? 'TAKEAWAY' : 'DINE_IN',
        remark: this.data.remark,
        idempotency_key: `ali_${app.globalData.customerId}_${Date.now()}`,
        customer_coupon_id: this.data.selectedCouponId || undefined,
        customer_voucher_id: this.data.selectedVoucherId || undefined,
        items: cart.map((c) => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      const payRes = await api.post(`/payments/orders/${order.id}/pay`, {
        provider: 'ALIPAY',
        channel_type: 'miniprogram'
      })
      await payUtil.requestAlipayPay(payRes)
      auth.saveCart([])
      const pts = payRes && payRes.points_earned ? `&points_earned=${payRes.points_earned}` : ''
      my.redirectTo({ url: `/pages/result/result?order_id=${order.id}${pts}` })
    } catch (e) {
      const msg = e.message || '下单失败'
      if (/无效桌台|桌台二维码|选择桌号/.test(msg)) {
        this.clearTableBinding()
        await this.resolveTable()
      }
      if (!/取消支付/.test(msg)) {
        my.showToast({ content: msg, type: 'fail' })
      } else {
        my.alert({
          title: '支付未完成',
          content: '订单已创建，您可在「订单」页查看并重新支付'
        })
      }
    } finally {
      this.setData({ loading: false })
    }
  }
})
