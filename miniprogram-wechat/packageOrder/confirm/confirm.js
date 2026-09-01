const api = require('../../utils/api')
const auth = require('../../utils/auth')
const payUtil = require('../../utils/pay')
const subscribe = require('../../utils/subscribe')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const voucherUtil = require('../../utils/voucher')
const dineCtx = require('../../utils/context')
const app = getApp()

function memberDiscountText(rate) {
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  const zhe = Math.round(n * 100) / 10
  return `${zhe}折`
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
    tableToken: '',
    tableOptions: [],
    pickedLabel: '',
    needScanHint: false,
    needPack: false,
    cartPreview: [],
    subtotal: '0.00',
    packingFee: '0.00',
    discountAmount: '0.00',
    payEstimate: '0.00',
    hasDiscount: false,
    boxPriceMap: {},
    isMember: false,
    memberLevelName: '',
    memberDiscountText: '',
    nextLevelName: '',
    nextLevelDiscountText: '',
    pointsToNext: 0,
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
    hasSpecialItems: false,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: ''
  },

  onShow() {
    // 扫码相机返回会触发 onShow；跳过整页重载，避免与「识别桌台」叠请求变慢
    if (this._bindingTable) return
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
    try {
      await auth.ensureLogin()
    } catch (e) {
      wx.showToast({ title: (e && e.message) || '登录失败', icon: 'none' })
    }
    await this.loadDineContext()
    await Promise.all([
      this.loadMember(),
      this.loadCoupons(),
      this.loadVouchers(),
      this.resolveTable(),
    ])
    await this.refreshSummary()
  },

  clearTableBinding() {
    const g = app.globalData
    g.table = null
    g.tableToken = ''
    try {
      wx.removeStorageSync('table_token')
      wx.removeStorageSync('dine_table')
    } catch (_) {}
    this.setData({ tableName: '', tableNo: '', tableToken: '', pickedLabel: '' })
  },

  async loadDineContext() {
    const ctx = await dineCtx.ensureDineContext()
    const token = String(
      (ctx.table && ctx.table.qr_token)
      || app.globalData.tableToken
      || wx.getStorageSync('table_token')
      || ''
    ).trim()
    this.setData({
      storeName: ctx.storeName,
      storeNo: ctx.storeNo,
      storeAddress: ctx.storeAddress,
      storePhone: ctx.storePhone,
      tableName: ctx.tableName || this.data.tableName || '',
      tableNo: ctx.tableNo || this.data.tableNo || '',
      ...(token && ctx.hasTable
        ? { tableToken: token, needScanHint: false, pickedLabel: ctx.tableName }
        : {}),
    })
  },

  _applyBoundTable(table, store, token) {
    const t = table || {}
    const tok = String(token || t.qr_token || '').trim()
    if (!tok) return false
    app.globalData.table = t
    app.globalData.tableToken = tok
    if (store) {
      app.globalData.store = store
      wx.setStorageSync('dine_store', store)
    }
    wx.setStorageSync('table_token', tok)
    wx.setStorageSync('dine_table', t)
    const name = t.table_name || t.table_no || ''
    this.setData({
      tableName: name,
      tableNo: t.table_no || '',
      tableToken: tok,
      needScanHint: false,
      pickedLabel: name,
    })
    return true
  },

  _readCachedTable() {
    return app.globalData.table || wx.getStorageSync('dine_table') || null
  },

  async resolveTable() {
    const cached = this._readCachedTable()
    let token = String(
      app.globalData.tableToken
      || wx.getStorageSync('table_token')
      || (cached && cached.qr_token)
      || ''
    ).trim()

    // 本地已有桌台：先展示，后台静默校验（网络失败不清除）
    if (token && cached && (cached.table_name || cached.table_no)) {
      if (!cached.qr_token || String(cached.qr_token) === token) {
        this._applyBoundTable(cached, app.globalData.store || wx.getStorageSync('dine_store'), token)
        this._softRefreshTable(token)
        return
      }
    }

    if (token) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
        if (entry && entry.table && entry.table.qr_token) {
          this._applyBoundTable(entry.table, entry.store || null, entry.table.qr_token)
          return
        }
        // 服务端明确无效：才清绑定
        this.clearTableBinding()
      } catch (_) {
        // 网络抖动：保留本地绑定，避免真机反复要求扫码
        if (cached && (cached.table_name || cached.table_no)) {
          this._applyBoundTable(cached, app.globalData.store || wx.getStorageSync('dine_store'), token)
          return
        }
        this.setData({ needScanHint: true })
        return
      }
    }

    try {
      await auth.ensureLogin()
      const sid = this.storeId()
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

  async _softRefreshTable(token) {
    try {
      const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
      if (!(entry && entry.table && entry.table.qr_token)) return
      this._applyBoundTable(entry.table, entry.store || null, entry.table.qr_token)
    } catch (_) {
      /* 静默失败，保留本地 */
    }
  },

  async scanTable() {
    this._bindingTable = true
    try {
      const ctx = await dineCtx.scanAndBindTable()
      if (!ctx) return
      const table = ctx.table || app.globalData.table
      const token = (table && table.qr_token) || app.globalData.tableToken || ''
      if (table && token) {
        this._applyBoundTable(table, ctx.store || app.globalData.store, token)
      }
      this.setData({
        storeName: ctx.storeName || this.data.storeName,
        storeNo: ctx.storeNo || this.data.storeNo,
        storeAddress: ctx.storeAddress || this.data.storeAddress,
        storePhone: ctx.storePhone || this.data.storePhone,
      })
    } finally {
      this._bindingTable = false
    }
  },

  async loadMember() {
    try {
      await auth.ensureLogin()
      const profile = await api.get('/miniprogram/customers/me')
      const rate = profile && profile.is_member && profile.member_discount_rate
        ? parseFloat(profile.member_discount_rate)
        : 1
      this._memberRate = Number.isFinite(rate) && rate > 0 && rate < 1 ? rate : 1
      this.setData({
        isMember: !!(profile && profile.is_member),
        memberLevelName: (profile && profile.member_level_name) || '',
        memberDiscountText: memberDiscountText(profile && profile.member_discount_rate),
        nextLevelName: (profile && profile.next_level_name) || '',
        nextLevelDiscountText: (profile && profile.member_levels && profile.next_level_name)
          ? (() => {
            const next = (profile.member_levels || []).find((l) => l.name === profile.next_level_name)
            return next && next.discount_text ? next.discount_text : ''
          })()
          : '',
        pointsToNext: (profile && profile.points_to_next_level) || 0
      })
    } catch (_) {
      this._memberRate = 1
    }
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

  /** 下单门店：优先已绑桌台，避免与 table_token 所属门店不一致 */
  submitStoreId() {
    const table = app.globalData.table || wx.getStorageSync('dine_table') || {}
    if (table && table.store_id) return table.store_id
    return this.storeId()
  },

  async loadCoupons() {
    try {
      await auth.ensureLogin()
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

  async loadVouchers() {
    try {
      await auth.ensureLogin()
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

  calcVoucherDiscount(payable, voucher, voucherEligible) {
    return voucherUtil.calcVoucherDiscount(payable, voucher, voucherEligible)
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
      const box = boxMap[item.product_id] || 0
      packing += box * qty
      // 优先 main_image（服务端路径），再回退购物车里已有的 imageUrl
      const rawImg = item.main_image || item.imageUrl || ''
      return {
        ...item,
        imageUrl: media.resolveUrl(rawImg, item._productUpdatedAt || '')
      }
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
        wx.showToast({
          title: '订单金额未满代金券面额，已取消使用',
          icon: 'none',
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
    await media.hydrateImageUrls(cartPreview, 'imageUrl')
    this.setData({
      cartPreview,
      subtotal: sub.toFixed(2),
      packingFee: pack.toFixed(2),
      discountAmount: discount.toFixed(2),
      couponDiscountAmount: couponDisc.toFixed(2),
      hasCouponDiscount: couponDisc > 0,
      voucherDiscountAmount: voucherDisc.toFixed(2),
      hasVoucherDiscount: voucherDisc > 0,
      payEstimate: pay.toFixed(2),
      hasDiscount: discount > 0,
      hasSpecialItems: enrichedList.some((i) => i.no_member_discount)
    })
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
          store_id: t.store_id || this.storeId()
        })
        token = bound && bound.qr_token
        if (bound) {
          t.qr_token = token
          t.store_id = bound.store_id
        }
      } catch (err) {
        wx.showToast({ title: (err && err.message) || '选桌失败', icon: 'none' })
        return
      }
    }
    if (!token) {
      wx.showToast({ title: '无法获取桌台令牌', icon: 'none' })
      return
    }
    this._applyBoundTable(
      { ...t, qr_token: token, table_name: t.label || t.table_name },
      app.globalData.store || wx.getStorageSync('dine_store'),
      token
    )
  },

  onPack(e) {
    const needPack = e.currentTarget.dataset.pack === '1' || e.currentTarget.dataset.pack === 1
    this.setData({ needPack }, () => this.refreshSummary())
  },

  onRemark(e) {
    this.setData({ remark: e.detail.value })
  },

  makeIdempotencyKey() {
    const cid = app.globalData.customerId || '0'
    const rand = Math.random().toString(36).slice(2, 10)
    return `wx_${cid}_${Date.now()}_${rand}`
  },

  async submit() {
    if (this.data.loading) return
    const cart = auth.getCart()
    if (!cart.length) {
      wx.showToast({ title: '购物车为空', icon: 'none' })
      return
    }
    let productItems
    try {
      productItems = await this.loadProductCatalog()
    } catch (e) {
      wx.showModal({
        title: '无法校验规格',
        content: (e && e.message) || '商品信息加载失败，请检查网络后重试',
        showCancel: false
      })
      return
    }
    if (!Array.isArray(productItems) || !productItems.length) {
      wx.showModal({
        title: '无法校验规格',
        content: '未能加载商品规格，请返回菜单刷新后重试',
        showCancel: false
      })
      return
    }
    const optionIssues = cartUtil.validateRequiredOptions(cart, productItems)
    if (optionIssues.length) {
      const first = optionIssues[0]
      const hint = optionIssues.length === 1
        ? `「${first.name}」缺少必选规格：${first.group}`
        : `有 ${optionIssues.length} 件商品缺少必选规格，请返回商品页重新选择`
      wx.showModal({
        title: '无法下单',
        content: `${hint}。请从菜单进入商品详情，选完面量/辣度等后再加入购物车。`,
        showCancel: false
      })
      return
    }
    const tableToken = String(
      this.data.tableToken || app.globalData.tableToken || wx.getStorageSync('table_token') || ''
    ).trim()
    if (!tableToken) {
      wx.showToast({ title: '请先扫描或选择桌号', icon: 'none' })
      await this.resolveTable()
      return
    }
    this.setData({ loading: true })
    try {
      await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: this.submitStoreId(),
        table_token: tableToken,
        channel: 'WECHAT',
        need_pack: !!this.data.needPack,
        dining_type: this.data.needPack ? 'TAKEAWAY' : 'DINE_IN',
        remark: this.data.remark,
        idempotency_key: this.makeIdempotencyKey(),
        customer_coupon_id: this.data.selectedCouponId || undefined,
        customer_voucher_id: this.data.selectedVoucherId || undefined,
        items: cart.map((c) => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      const payRes = await api.post(`/payments/orders/${order.id}/pay`, {
        provider: 'WECHAT',
        channel_type: 'miniprogram'
      })
      await payUtil.requestWechatPay(payRes)
      auth.saveCart([])
      await subscribe.requestOrderReadySubscribe()
      const pts = payRes && payRes.points_earned ? `&points_earned=${payRes.points_earned}` : ''
      wx.redirectTo({ url: `/packageOrder/result/result?order_id=${order.id}${pts}` })
    } catch (e) {
      const msg = (e && e.message) || '下单失败'
      if (/无效桌台|桌台二维码|选择桌号/.test(msg)) {
        this.clearTableBinding()
        await this.resolveTable()
      }
      if (!/取消支付/.test(msg)) {
        wx.showToast({ title: msg, icon: 'none', duration: 2500 })
      } else {
        wx.showModal({
          title: '支付未完成',
          content: '订单已创建，您可在「订单」页查看并重新支付',
          showCancel: false
        })
      }
    } finally {
      this.setData({ loading: false })
    }
  }
})
