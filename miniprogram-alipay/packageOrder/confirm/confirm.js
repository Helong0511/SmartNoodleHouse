const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const payUtil = require('../../utils/pay')
const voucherUtil = require('../../utils/voucher')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const tracker = require('../../utils/tracker')
const { fetchAllProducts } = require('../../utils/products')
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
    payMethod: 'ALIPAY',
    balanceTotal: '0.00',
    canPayBalance: false,
    memberLevelName: '',
    memberDiscountText: '',
    nextLevelName: '',
    nextLevelDiscountText: '',
    pointsToNext: 0,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableNo: '',
    seatLabel: '桌号',
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
    settleReady: false,
    settleStoreId: null,
    cartIds: []
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
    const products = await fetchAllProducts(api, { ...(sid ? { store_id: sid } : {}) }, false)
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
    const table = app.globalData.table || auth.readStorage('dine_table') || {}
    if (table && table.store_id) return table.store_id
    return this.storeId()
  },

  _newCheckoutNonce() {
    return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
  },

  makeIdempotencyKey(cart) {
    // 内容哈希 + 本次结算 nonce：网络重试可幂等；同款再买不会命中已付订单
    const cid = app.globalData.customerId || '0'
    const items = (cart || auth.getCart() || []).map((c) => {
      const opts = (c.option_ids || []).slice().map(String).sort().join('.')
      return `${c.product_id}x${c.quantity || 1}:${opts}`
    }).sort()
    const raw = [
      this.submitStoreId() || 0,
      this.data.needPack ? '1' : '0',
      this.data.selectedCouponId || 0,
      this.data.selectedVoucherId || 0,
      items.join(',')
    ].join('|')
    let h = 2166136261
    for (let i = 0; i < raw.length; i++) {
      h ^= raw.charCodeAt(i)
      h = Math.imul(h, 16777619)
    }
    const content = (h >>> 0).toString(36)
    if (this._idemContentKey !== content) {
      this._idemContentKey = content
      this._checkoutNonce = this._newCheckoutNonce()
    }
    if (!this._checkoutNonce) this._checkoutNonce = this._newCheckoutNonce()
    return `ali_${cid}_${content}_${this._checkoutNonce}`
  },

  clearCheckoutNonce() {
    this._checkoutNonce = null
    this._idemContentKey = null
  },

  onShow() {
    if (this._bindingTable) return
    // 同步预读本地桌台凭证，避免 bootstrapCheckout 异步完成前首帧闪现「扫码绑桌」
    try {
      const cachedTable = app.globalData.table || auth.readStorage('dine_table') || null
      const localToken = String(
        (cachedTable && cachedTable.qr_token)
        || app.globalData.tableToken
        || auth.readStorage('table_token')
        || ''
      ).trim()
      if (localToken && !this.data.tableToken) {
        this.setData({
          tableToken: localToken,
          needScanHint: false,
          tableName: this.data.tableName
            || (cachedTable && (cachedTable.table_name || cachedTable.table_no))
            || '',
          pickedLabel: this.data.pickedLabel
            || (cachedTable && (cachedTable.table_name || cachedTable.table_no))
            || '已绑桌'
        })
      }
    } catch (_) { }
    this._memberRate = 1
    this._couponManualPick = false
    this._voucherManualPick = false
    this._payMethodManualPick = false
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
  _checkoutStale(gen) {
    return gen !== this._checkoutGen || this._bindingTable
  },
  _readStoredTableToken(cached, ctx) {
    return String(
      (ctx && ctx.table && ctx.table.qr_token)
      || app.globalData.tableToken
      || auth.readStorage('table_token')
      || (cached && cached.qr_token)
      || ''
    ).trim()
  },
  async bootstrapCheckout() {
    const gen = (this._checkoutGen = (this._checkoutGen || 0) + 1)
    try {
      await auth.ensureLogin()
    } catch (e) {
      my.showToast({ content: auth.formatError(e, '登录失败'), type: 'fail' })
    }
    const ctx = await dineCtx.ensureDineContext()
    const cached = this._readCachedTable()
    const token = this._readStoredTableToken(cached, ctx)
    const tableLabel = ctx.tableName
      || (cached && (cached.table_name || cached.table_no))
      || this.data.tableName
      || ''
    this.setData({
      ...dineCtx.pageDineFields(ctx),
      tableName: tableLabel || ctx.tableName || '',
      // 外带虚拟桌 tableNo 必须为空；勿回退到 this.data.tableNo（会残留上次堂食桌号）
      tableNo: ctx.isTakeawayChannel
        ? ''
        : (ctx.tableNo || (cached && cached.table_no) || ''),
      ...(token
        ? {
          tableToken: token,
          needScanHint: false,
          pickedLabel: tableLabel || this.data.pickedLabel || '已绑桌'
        }
        : {}),
    })
    await Promise.all([
      this.loadMember(),
      this.loadCoupons(),
      this.loadVouchers(),
      this.resolveTable(gen),
    ])
    if (this._checkoutStale(gen)) return
    await this.refreshSummary()
  },
  clearTableBinding() {
    app.globalData.table = null
    app.globalData.tableToken = ''
    try {
      my.removeStorageSync({ key: 'table_token' })
      my.removeStorageSync({ key: 'dine_table' })
    } catch (_) { }
    this.setData({ tableName: '', tableNo: '', seatLabel: '桌号', tableToken: '', pickedLabel: '' })
  },
  _applyBoundTable(table, store, token) {
    const t = table || {}
    const tok = String(token || t.qr_token || '').trim()
    if (!tok) return false
    app.globalData.table = t
    app.globalData.tableToken = tok
    if (store) {
      app.globalData.store = store
      my.setStorageSync({ key: 'dine_store', data: store })
    }
    my.setStorageSync({ key: 'table_token', data: tok })
    my.setStorageSync({ key: 'dine_table', data: t })
    const dine = dineCtx.displayPayload(store || null, t)
    this.setData({
      ...dineCtx.pageDineFields(dine),
      tableToken: tok,
      needScanHint: false,
      pickedLabel: dine.tableName || '已绑桌',
    })
    return true
  },

  onTableChange(ctx) {
    if (!ctx || !ctx.table) return
    const token = String(
      ctx.table.qr_token || auth.readStorage('table_token') || this.data.tableToken || ''
    ).trim()
    if (this._applyBoundTable(ctx.table, ctx.store, token)) {
      this.refreshSummary()
    }
  },

  _readCachedTable() {
    return app.globalData.table || auth.readStorage('dine_table') || null
  },
  async resolveTable(checkoutGen) {
    const gen = checkoutGen != null ? checkoutGen : this._checkoutGen
    const cached = this._readCachedTable()
    let token = this._readStoredTableToken(cached, null)

    if (token && cached && (cached.table_name || cached.table_no)) {
      if (!cached.qr_token || String(cached.qr_token) === token) {
        this._applyBoundTable(cached, app.globalData.store || auth.readStorage('dine_store'), token)
        this._softRefreshTable(token, gen)
        return
      }
    }

    if (token) {
      try {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'alipay' }, false)
        if (this._checkoutStale(gen)) return
        if (entry && entry.table) {
          this._applyBoundTable(entry.table, entry.store || null, entry.table.qr_token || token)
          return
        }
        const rebound = await dineCtx.refreshTableBinding({ channel: 'alipay' })
        if (this._checkoutStale(gen)) return
        if (rebound.ok && rebound.token) {
          this._applyBoundTable(rebound.ctx.table, rebound.ctx.store, rebound.token)
          return
        }
        if (cached && (cached.table_name || cached.table_no)) {
          this._applyBoundTable(cached, app.globalData.store || auth.readStorage('dine_store'), token)
          return
        }
        if (!this._checkoutStale(gen)) this.clearTableBinding()
      } catch (_) {
        if (this._checkoutStale(gen)) return
        if (cached && (cached.table_name || cached.table_no)) {
          this._applyBoundTable(cached, app.globalData.store || auth.readStorage('dine_store'), token)
          return
        }
        this.setData({ needScanHint: true })
        return
      }
    }

    try {
      await auth.ensureLogin()
      const sid = (app.globalData.table && app.globalData.table.store_id)
        || (app.globalData.store && app.globalData.store.id)
        || null
      if (!sid) {
        if (!this._checkoutStale(gen)) {
          this.setData({ tableOptions: [], needScanHint: !token })
        }
        return
      }
      const list = await api.get('/miniprogram/tables', { store_id: sid })
      if (this._checkoutStale(gen)) return
      const options = (list || [])
        .filter((t) => !dineCtx.isTakeawayChannelTable(t))
        .map((t) => ({
          label: t.table_name || t.table_no,
          id: t.id,
          qr_token: t.qr_token || '',
          store_id: t.store_id,
          table_no: t.table_no,
          table_name: t.table_name,
        }))
      this.setData({ tableOptions: options, needScanHint: !token && !options.length })
    } catch (_) {
      if (!this._checkoutStale(gen)) {
        this.setData({ tableOptions: [], needScanHint: !token })
      }
    }
  },
  async _softRefreshTable(token, checkoutGen) {
    const gen = checkoutGen != null ? checkoutGen : this._checkoutGen
    if (this._checkoutStale(gen)) return
    const refreshed = await dineCtx.refreshTableBinding({ channel: 'alipay' })
    if (this._checkoutStale(gen)) return
    if (refreshed.ok && refreshed.ctx) {
      this._applyBoundTable(refreshed.ctx.table, refreshed.ctx.store, refreshed.token)
    }
  },
  async _ensureTableTokenForSubmit() {
    const refreshed = await dineCtx.refreshTableBinding({ channel: 'alipay' })
    if (refreshed.ok && refreshed.token) {
      if (refreshed.ctx) {
        this._applyBoundTable(refreshed.ctx.table, refreshed.ctx.store, refreshed.token)
      }
      return refreshed.token
    }
    // 网络失败时保留本地 token，避免弱网下误判未绑桌
    return String(
      refreshed.token
      || this.data.tableToken
      || app.globalData.tableToken
      || auth.readStorage('table_token')
      || ''
    ).trim()
  },
  async scanTable() {
    this._bindingTable = true
    this._checkoutGen = (this._checkoutGen || 0) + 1
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
        ? parseFloat(profile.member_discount_rate) : 1
      this._memberRate = Number.isFinite(rate) && rate > 0 && rate < 1 ? rate : 1
      this.setData({
        isMember: !!(profile && profile.is_member),
        balanceTotal: (profile && profile.balance_total) || '0.00',
        canPayBalance: !!(profile && profile.is_member && parseFloat(profile.balance_total || 0) > 0),
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
  onPayMethod(e) {
    const m = String((e.currentTarget.dataset && e.currentTarget.dataset.m) || '').toUpperCase()
    if (m === 'BALANCE' && !this.data.canPayBalance) {
      my.showToast({ content: '余额不足，请先充值', type: 'none' })
      return
    }
    if (m !== 'ALIPAY' && m !== 'BALANCE') return
    this._payMethodManualPick = true
    if (m === 'BALANCE') {
      // 余额支付不叠会员折扣/券（储值已含赠送）
      this._couponManualPick = true
      this._voucherManualPick = true
      this._selectedCoupon = null
      this._selectedVoucher = null
      this.setData({
        payMethod: m,
        selectedCouponId: 0,
        selectedCouponLabel: '余额支付不可用券',
        selectedVoucherId: 0,
        selectedVoucherLabel: '余额支付不可用券'
      }, () => this.refreshSummary())
      return
    }
    // 切回支付宝：恢复自动选用最优券，并重新叠加会员折扣
    this._couponManualPick = false
    this._voucherManualPick = false
    this._selectedCoupon = null
    this._selectedVoucher = null
    this.setData({
      payMethod: m,
      selectedCouponId: 0,
      selectedCouponLabel: '不使用优惠券',
      selectedVoucherId: 0,
      selectedVoucherLabel: '不使用代金券'
    }, () => this.refreshSummary())
  },
  async loadCoupons() {
    try {
      await auth.ensureLogin()
      const res = await api.get('/miniprogram/coupons/mine', { store_id: this.submitStoreId() })
      const items = (res && res.items) || []
      this.setData({
        coupons: items,
        couponPickerLabels: ['不使用优惠券'].concat(items.map((c) => voucherUtil.holdDisplayName(c)))
      })
    } catch (_) {
      this.setData({ coupons: [] })
    }
  },
  async loadVouchers() {
    try {
      await auth.ensureLogin()
      const res = await api.get('/miniprogram/vouchers/mine', { store_id: this.submitStoreId() })
      const items = (res && res.items) || []
      this.setData({
        vouchers: items,
        voucherPickerLabels: ['不使用代金券'].concat(items.map((c) => voucherUtil.holdDisplayName(c)))
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
        selectedCouponLabel: picked ? voucherUtil.holdDisplayName(picked) : '不使用优惠券'
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
        selectedVoucherLabel: picked ? voucherUtil.holdDisplayName(picked) : '不使用代金券'
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
    } catch (_) { }
    let packing = 0
    const cartPreview = enrichedList.map((item) => {
      const qty = item.quantity || 1
      packing += (boxMap[item.product_id] || 0) * qty
      const rawImg = item.main_image || item.imageUrl || ''
      return {
        ...item,
        imageUrl: media.resolveUrl(rawImg, item._productUpdatedAt || '')
      }
    })
    const sub = parseFloat(total) || 0
    const pack = Math.round(packing * 100) / 100
    const needPack = this.data.needPack
    const bal = parseFloat(this.data.balanceTotal || 0) || 0
    const fullPay = Math.max(0, Math.round((sub + (needPack ? pack : 0)) * 100) / 100)
    const canBal = !!(this.data.isMember && bal + 1e-9 >= fullPay)
    let nextPayMethod = this.data.payMethod
    if (!canBal && nextPayMethod === 'BALANCE') {
      nextPayMethod = 'ALIPAY'
    } else if (canBal && !this._payMethodManualPick) {
      // 余额足以按原价结清时，优先默认余额支付
      nextPayMethod = 'BALANCE'
    }
    const useBalance = nextPayMethod === 'BALANCE'
    const rate = useBalance ? 1 : (this._memberRate || 1)
    let discount = 0
    if (!useBalance && rate > 0 && rate < 1) {
      const discountable = cartUtil.calcDiscountableSubtotal(enrichedList)
      discount = Math.round(discountable * (1 - rate) * 100) / 100
    }
    const afterMember = Math.max(0, sub - discount)
    const couponEligible = cartUtil.calcCouponEligibleSubtotal(enrichedList, discount)

    if (useBalance) {
      this._selectedCoupon = null
      this._selectedVoucher = null
    } else if (!this._couponManualPick && (this.data.coupons || []).length) {
      const bestCoupon = pickBestCoupon(
        this.data.coupons,
        couponEligible,
        (amt, c) => this.calcCouponDiscount(amt, c)
      )
      this._selectedCoupon = bestCoupon
    }

    const couponDisc = useBalance ? 0 : this.calcCouponDiscount(couponEligible, this._selectedCoupon)
    let pay = afterMember - couponDisc
    const voucherEligible = cartUtil.calcVoucherEligiblePayable(
      couponEligible, couponDisc, pack, needPack
    )
    if (needPack) pay += pack

    if (!useBalance && !this._voucherManualPick && (this.data.vouchers || []).length) {
      const bestVoucher = pickBestVoucher(
        this.data.vouchers,
        voucherEligible,
        (amt, v) => this.calcVoucherDiscount(pay, v, amt)
      )
      this._selectedVoucher = bestVoucher
    }

    let voucherDisc = useBalance ? 0 : this.calcVoucherDiscount(pay, this._selectedVoucher, voucherEligible)
    if (!useBalance && this._selectedVoucher && voucherDisc <= 0) {
      if (this._voucherManualPick) {
        my.showToast({
          content: '订单金额未满代金券面额，已取消使用',
          type: 'none',
          duration: 2500
        })
        this._voucherManualPick = false
      }
      this._selectedVoucher = null
      voucherDisc = 0
    }
    pay = Math.max(0, Math.round((pay - voucherDisc) * 100) / 100)
    await media.hydrateImageUrls(cartPreview, 'imageUrl')
    const settleStoreId = this.submitStoreId()
    this.setData({
      cartPreview,
      cartIds: cartPreview.map((it) => it.product_id),
      settleReady: !!settleStoreId,
      settleStoreId: settleStoreId || null,
      subtotal: sub.toFixed(2),
      packingFee: pack.toFixed(2),
      discountAmount: discount.toFixed(2),
      hasDiscount: discount > 0,
      couponDiscountAmount: couponDisc.toFixed(2),
      hasCouponDiscount: couponDisc > 0,
      voucherDiscountAmount: voucherDisc.toFixed(2),
      hasVoucherDiscount: voucherDisc > 0,
      payEstimate: pay.toFixed(2),
      hasSpecialItems: enrichedList.some((i) => i.no_member_discount),
      canPayBalance: canBal,
      payMethod: nextPayMethod,
      selectedCouponId: useBalance ? 0 : (this._selectedCoupon ? this._selectedCoupon.id : 0),
      selectedCouponLabel: useBalance
        ? '余额支付不可用券'
        : (this._selectedCoupon ? voucherUtil.holdDisplayName(this._selectedCoupon) : '不使用优惠券'),
      selectedVoucherId: useBalance ? 0 : (this._selectedVoucher ? this._selectedVoucher.id : 0),
      selectedVoucherLabel: useBalance
        ? '余额支付不可用券'
        : (this._selectedVoucher ? voucherUtil.holdDisplayName(this._selectedVoucher) : '不使用代金券')
    })
  },
  async onPickTable(e) {
    const idx = Number(e.detail.value)
    const t = this.data.tableOptions[idx]
    if (!t) return
    let token = t.qr_token
    if (!token && t.id) {
      try {
        // P1 修复：bind 须传当前扫码 qr_token 凭证，后端校验同门店才允许绑定
        const cached = dineCtx.readCachedContext() || {}
        const credToken = String(cached.token || (cached.table && cached.table.qr_token) || '').trim()
        const bound = await api.post('/miniprogram/tables/bind', {
          table_id: t.id,
          store_id: t.store_id || (app.globalData.table && app.globalData.table.store_id),
          qr_token: credToken
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
    this._applyBoundTable({ ...t, qr_token: token, table_name: t.label || t.table_name }, app.globalData.store, token)
  },
  onPack(e) {
    this.setData({ needPack: String(e.currentTarget.dataset.pack) === '1' }, () => this.refreshSummary())
  },
  onRemark(e) { this.setData({ remark: e.detail.value }) },

  // ---- 顺手再来一份（settle_addon 推荐位）----
  onRecommendOpen(detail) {
    const pid = Number(detail && detail.id)
    if (pid) my.navigateTo({ url: `/packageOrder/product/product?id=${pid}` })
  },

  onRecommendAdd(detail) {
    const item = detail || {}
    const catalog = this._productCatalog || []
    const found = catalog.find((p) => String(p.id) === String(item.id))
    if (found && found.has_options) {
      my.navigateTo({ url: `/packageOrder/product/product?id=${item.id}` })
      return
    }
    const check = cartUtil.checkCategoryRequire(found || {
      id: item.id, name: item.name, category_id: item.category_id
    }, auth.getCart(), catalog, [])
    if (!check.ok) {
      my.showToast({ content: check.message, type: 'none', duration: 2200 })
      return
    }
    const unit = parseFloat(item.price) || 0
    const entry = {
      product_id: Number(item.id),
      name: item.name,
      quantity: 1,
      option_ids: [],
      option_labels: [],
      option_text: '',
      unit_price: unit,
      subtotal: unit.toFixed(2),
      imageUrl: item.image || '',
      main_image: found ? found.main_image || '' : '',
      no_member_discount: found ? !!found.no_member_discount : false,
      category_id: found ? found.category_id : item.category_id,
      require_category_ids: found ? found.require_category_ids || [] : [],
      require_product_ids: found ? found.require_product_ids || [] : []
    }
    auth.saveCart(cartUtil.mergeCartItem(auth.getCart(), entry))
    try {
      tracker.cartAdd(Number(item.id), {
        scene: 'settle_addon',
        category_id: entry.category_id || null,
        payload: { qty: 1 }
      })
    } catch (_) { }
    my.showToast({ content: '已加入', type: 'success' })
    this.refreshSummary()
  },
  async submit() {
    if (this.data.loading) return
    // 校验前即加锁，防止连点双提交
    this.setData({ loading: true })
    try {
      const cart = auth.getCart()
      if (!cart.length) {
        my.showToast({ content: '购物车为空', type: 'fail' })
        return
      }
      let productItems
      try {
        productItems = await this.loadProductCatalog()
      } catch (e) {
        my.alert({
          title: '无法校验规格',
          content: (e && e.message) || '商品信息加载失败，请检查网络后重试'
        })
        return
      }
      if (!Array.isArray(productItems) || !productItems.length) {
        my.alert({
          title: '无法校验规格',
          content: '未能加载商品规格，请返回菜单刷新后重试'
        })
        return
      }
      const optionIssues = cartUtil.validateRequiredOptions(cart, productItems)
      if (optionIssues.length) {
        const first = optionIssues[0]
        const hint = optionIssues.length === 1
          ? `「${first.name}」缺少必选规格：${first.group}`
          : `有 ${optionIssues.length} 件商品缺少必选规格，请返回商品页重新选择`
        my.alert({ title: '无法下单', content: `${hint}。请从菜单进入商品详情，选完面量/辣度等后再加入购物车。` })
        return
      }
      const catIssues = cartUtil.validateCategoryRequires(cart, productItems)
      if (catIssues.length) {
        my.alert({ title: '无法下单', content: catIssues[0].message || '加料类商品须先点主餐' })
        return
      }
      let tableToken = await this._ensureTableTokenForSubmit()
      if (!tableToken) {
        my.showToast({ content: '请先扫描或选择桌号', type: 'fail' })
        await this.resolveTable()
        return
      }
      await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: this.submitStoreId(),
        table_token: tableToken,
        channel: 'ALIPAY',
        need_pack: !!this.data.needPack,
        dining_type: this.data.needPack ? 'TAKEAWAY' : 'DINE_IN',
        remark: this.data.remark,
        idempotency_key: this.makeIdempotencyKey(cart),
        skip_promotions: this.data.payMethod === 'BALANCE',
        customer_coupon_id: this.data.payMethod === 'BALANCE' ? undefined : (this.data.selectedCouponId || undefined),
        customer_voucher_id: this.data.payMethod === 'BALANCE' ? undefined : (this.data.selectedVoucherId || undefined),
        items: cart.map((c) => ({
          product_id: c.product_id,
          quantity: c.quantity || 1,
          option_ids: c.option_ids || []
        }))
      })
      const payRes = await api.post(`/payments/orders/${order.id}/pay`, {
        provider: this.data.payMethod === 'BALANCE' ? 'BALANCE' : 'ALIPAY',
        channel_type: this.data.payMethod === 'BALANCE' ? 'balance' : 'miniprogram'
      })
      let payOutcome
      try {
        if (this.data.payMethod === 'BALANCE') {
          if (!(payRes && (payRes.status === 'PAID' || payRes.already_paid || payRes.fully_paid))) {
            throw new Error((payRes && payRes.message) || '余额支付失败')
          }
          payOutcome = { paid: true, payRes, sync: null }
        } else {
          payOutcome = await payUtil.payAndConfirm(api, payRes, order.id)
        }
      } catch (payErr) {
        const ptsFail = payRes && payRes.points_earned ? `&points_earned=${payRes.points_earned}` : ''
        if (payErr && payErr.payCancel) {
          my.alert({
            title: '支付未完成',
            content: '订单已创建，正在确认支付结果；也可稍后在「订单」页重新支付',
            success: () => {
              my.redirectTo({ url: `/packageOrder/result/result?order_id=${order.id}${ptsFail}` })
            }
          })
          return
        }
        my.showToast({ content: (payErr && payErr.message) || '支付未完成', type: 'fail' })
        my.redirectTo({ url: `/packageOrder/result/result?order_id=${order.id}${ptsFail}` })
        return
      }
      auth.saveCart([])
      this.clearCheckoutNonce()
      prefUtil.clearPrefsCache(app)
      const pts =
        (payOutcome && payOutcome.sync && payOutcome.sync.points_earned) ||
          (payRes && payRes.points_earned)
          ? `&points_earned=${(payOutcome && payOutcome.sync && payOutcome.sync.points_earned) || payRes.points_earned}`
          : ''
      my.redirectTo({ url: `/packageOrder/result/result?order_id=${order.id}${pts}` })
    } catch (e) {
      const msg = (e && e.message) || '下单失败'
      if (Number(e && e.code) === 40903 || /幂等键.*已结束|重新下单/.test(msg)) {
        this.clearCheckoutNonce()
      }
      if (/无效桌台|桌台二维码|选择桌号/.test(msg)) {
        const retry = await dineCtx.refreshTableBinding({ channel: 'alipay' })
        if (retry.ok && retry.token) {
          my.showToast({ content: '桌台已刷新，请再次提交', type: 'none' })
          if (retry.ctx) {
            this._applyBoundTable(retry.ctx.table, retry.ctx.store, retry.token)
          }
        } else if (retry.token) {
          // 网络失败但本地仍有凭证：保留绑定，仅提示重试
          my.showToast({ content: '网络异常，请稍后重试', type: 'none' })
        } else {
          this.clearTableBinding()
          await this.resolveTable()
        }
      }
      if (!/取消支付/.test(msg)) {
        if (msg.length > 14) {
          my.alert({ title: '下单失败', content: msg })
        } else {
          my.showToast({ content: msg, type: 'fail' })
        }
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
