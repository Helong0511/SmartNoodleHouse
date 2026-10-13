const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cartUtil = require('../../utils/cart')
const media = require('../../utils/media')
const payUtil = require('../../utils/pay')
const voucherUtil = require('../../utils/voucher')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const tracker = require('../../utils/tracker')
const { loadStoreCatalog } = require('../../utils/products')
const clientConfig = require('../../utils/clientConfig')
const { pad2, formatDate } = require('../../utils/date')
const app = getApp()

const CHECKOUT_CACHE_MS = 30 * 1000
const REMARK_MAX_LEN = 50

/** 今天/明天两档，30 分钟粒度（11:00–20:30），仅保留 ≥ 当前时间+30 分钟的槽位 */
function buildPickupSlots(dayIdx) {
  const base = new Date()
  base.setDate(base.getDate() + (dayIdx === 1 ? 1 : 0))
  const dateStr = formatDate(base)
  const minTime = Date.now() + 30 * 60 * 1000
  const slots = []
  for (let h = 11; h <= 20; h++) {
    for (const m of [0, 30]) {
      const label = `${pad2(h)}:${pad2(m)}`
      const dt = new Date(base.getFullYear(), base.getMonth(), base.getDate(), h, m, 0)
      if (dt.getTime() < minTime) continue
      slots.push({ label, iso: `${dateStr}T${label}:00` })
    }
  }
  return slots
}

function memberDiscountText(rate) {
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  return `${Math.round(n * 100) / 10}折`
}

/** 本地兜底费率（无 preview 时用；正式以 /social-benefits/preview 为准） */
const SOCIAL_RATE_HINT = {
  SANITATION: 0.8,
  RIDER: 0.8,
  ELDER: 0.85,
  TEACHER: 0.9,
  STUDENT: 0.9,
  MEDICAL: 0.85,
  DISABLED: 0.85,
  VETERAN: 0.85,
  VOLUNTEER: 0.9
}
const SOCIAL_LABEL = {
  SANITATION: '环卫工公益',
  RIDER: '骑手公益',
  ELDER: '长者公益',
  TEACHER: '教师公益',
  STUDENT: '学生公益',
  MEDICAL: '医护公益',
  DISABLED: '残障公益',
  VETERAN: '退役军人公益',
  VOLUNTEER: '志愿者公益'
}

function bestSocialHint(codes) {
  let bestCode = ''
  let bestRate = 1
  for (const c of codes || []) {
    const r = SOCIAL_RATE_HINT[c]
    if (Number.isFinite(r) && r < bestRate) {
      bestRate = r
      bestCode = c
    }
  }
  if (!bestCode) return null
  return { code: bestCode, rate: bestRate, label: SOCIAL_LABEL[bestCode] || bestCode, fromPreview: false }
}

function socialSubsidyOf(hint, discountable) {
  if (!hint) return 0
  const s = parseFloat(hint.subsidy)
  if (Number.isFinite(s) && s >= 0) return Math.round(s * 100) / 100
  const r = parseFloat(hint.rate)
  if (!(r > 0 && r < 1)) return 0
  const base = Math.max(0, parseFloat(discountable) || 0)
  return Math.round(base * (1 - r) * 100) / 100
}

function memberOnlySave(discountable, memberRate) {
  const base = Math.max(0, parseFloat(discountable) || 0)
  const mr = parseFloat(memberRate)
  if (!(Number.isFinite(mr) && mr > 0 && mr < 1)) return 0
  return Math.round(base * (1 - mr) * 100) / 100
}

function pickBestCoupon(coupons, discountable, memberRate, calcTotalSavingsFn) {
  // 按「总优惠力度」取最优：FIXED=会员折+满减；PERCENT=仅当严格优于会员折时独占折扣
  let best = null
  let bestSave = 0
  for (const c of coupons || []) {
    const save = calcTotalSavingsFn(discountable, memberRate, c)
    if (save <= 0) continue
    if (save > bestSave) {
      bestSave = save
      best = c
    } else if (save === bestSave && best) {
      const expA = c.expires_at || ''
      const expB = best.expires_at || ''
      if (expA && expB && expA < expB) best = c
    }
  }
  return best
}

function percentBeatsMember(couponRate, memberRate) {
  const cr = parseFloat(couponRate)
  const mr = parseFloat(memberRate)
  if (!Number.isFinite(cr) || cr <= 0 || cr >= 1) return false
  const m = Number.isFinite(mr) && mr > 0 && mr <= 1 ? mr : 1
  return cr < m
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
    quickRemarks: [],
    selectedRemarks: [],
    scheduleEnabled: false,
    scheduleDayIdx: 0,
    scheduleSlots: [],
    scheduleSlotIdx: -1,
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
    storeLogo: '',
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
    cartIds: [],
    hasSocialBenefit: false,
    useSocialBenefit: false,
    socialBenefitLabel: '',
    socialDiscountAmount: '0.00',
    hasSocialDiscount: false,
    socialRecommendTip: ''
  },
  storeId() {
    try {
      const ctx = dineCtx.currentDisplay()
      if (ctx && ctx.storeId) return ctx.storeId
    } catch (_) { }
    const table = app.globalData.table || auth.readStorage('dine_table') || null
    const store = app.globalData.store || auth.readStorage('dine_store') || null
    return (table && table.store_id)
      || (store && (store.id || store.store_id))
      || null
  },
  async loadProductCatalog() {
    if (this._productCatalog && this._catalogComplete) {
      return this._productCatalog
    }
    const sid = this.submitStoreId() || this.storeId()
    if (!sid) {
      throw new Error('请先扫桌码绑定门店后再结算')
    }
    const shared = app.globalData && app.globalData._sharedCatalog
    if (shared && String(shared.storeId) === String(sid) && shared.items && shared.items.length) {
      const items = shared.items
      this._productCatalog = items
      this._catalogComplete = true
      const boxMap = {}
      items.forEach((p) => {
        boxMap[p.id] = parseFloat(p.box_price || 0) || 0
      })
      this.setData({ boxPriceMap: boxMap })
      return items
    }
    const catalog = await loadStoreCatalog(api, sid, { preferCache: true })
    const items = (catalog && catalog.items) || []
    if (!catalog || (!catalog.complete && !items.length)) {
      throw new Error('菜单加载不完整，请稍后重试')
    }
    this._productCatalog = items
    this._catalogComplete = true
    const boxMap = {}
    items.forEach((p) => {
      boxMap[p.id] = parseFloat(p.box_price || 0) || 0
    })
    this.setData({ boxPriceMap: boxMap })
    return items
  },

  /** 下单门店：桌台 > 门店上下文 > 再来一单写入的 cart_store_id */
  submitStoreId() {
    const table = app.globalData.table || auth.readStorage('dine_table') || {}
    if (table && table.store_id) return table.store_id
    const fromCtx = this.storeId()
    if (fromCtx) return fromCtx
    try {
      const cartSid = Number(auth.readStorage('cart_store_id'))
      if (Number.isFinite(cartSid) && cartSid > 0) return cartSid
    } catch (_) { }
    return null
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
      this._scheduledPickupIso() || '',
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
    // 快捷备注标签来自后端下发配置（可能为空数组）
    clientConfig.loadClientConfig().then((cfg) => {
      const opts = (cfg && Array.isArray(cfg.quick_remark_options)) ? cfg.quick_remark_options : []
      this.setData({
        quickRemarks: opts.filter((s) => String(s || '').trim()).map((s) => ({ label: String(s).trim(), on: false }))
      })
    }).catch(() => { })
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
    const cartFp = this._cartFingerprint()
    const now = Date.now()
    if (
      this._checkoutReadyAt
      && now - this._checkoutReadyAt < CHECKOUT_CACHE_MS
      && cartFp === this._checkoutCartFp
      && (this.data.cartPreview || []).length
    ) {
      this.refreshSummary()
      return
    }
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
  _cartFingerprint() {
    const cart = auth.getCart() || []
    return cart.map((c) => {
      const opts = (c.option_ids || []).slice().map(String).sort().join('.')
      return `${c.product_id}x${c.quantity || 1}:${opts}`
    }).sort().join('|')
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
      this.loadSocialBenefit(),
      this.loadCoupons(),
      this.loadVouchers(),
      this.resolveTable(gen),
    ])
    if (this._checkoutStale(gen)) return
    await this.refreshSummary()
    if (this._checkoutStale(gen)) return
    this._checkoutReadyAt = Date.now()
    this._checkoutCartFp = this._cartFingerprint()
  },
  clearTableBinding() {
    try {
      dineCtx.clearDineContext()
    } catch (_) {
      app.globalData.table = null
      app.globalData.store = null
      app.globalData.tableToken = ''
      try {
        my.removeStorageSync({ key: 'table_token' })
        my.removeStorageSync({ key: 'dine_table' })
        my.removeStorageSync({ key: 'dine_store' })
        my.removeStorageSync({ key: 'dine_bound_at' })
      } catch (__) { }
    }
    this.setData({
      storeName: '',
      storeNo: '',
      storeLogo: '',
      tableName: '',
      tableNo: '',
      seatLabel: '桌号',
      tableToken: '',
      pickedLabel: '',
      hasTable: false
    })
  },
  _applyBoundTable(table, store, token) {
    const t = table || {}
    const tok = String(token || t.qr_token || '').trim()
    if (!tok) return false
    const tableWithToken = Object.assign({}, t, { qr_token: tok })
    dineCtx.applyContext(tableWithToken, store || null)
    const dine = dineCtx.displayPayload(
      store || app.globalData.store || auth.readStorage('dine_store') || null,
      tableWithToken
    )
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
      const sid = this.storeId()
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
  async loadMember(force = false) {
    const now = Date.now()
    if (!force && this._memberAt && now - this._memberAt < CHECKOUT_CACHE_MS) {
      return
    }
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
      this._memberAt = Date.now()
    } catch (_) {
      this._memberRate = 1
    }
  },
  async loadSocialBenefit() {
    this._socialHint = null
    try {
      await clientConfig.loadClientConfig().catch(() => { })
      if (!clientConfig.isSocialBenefitEnabled()) {
        this.setData({ hasSocialBenefit: false, useSocialBenefit: false, socialBenefitLabel: '' })
        return
      }
      if (!auth.getToken()) {
        this.setData({ hasSocialBenefit: false, useSocialBenefit: false, socialBenefitLabel: '' })
        return
      }
      const res = await api.get('/social-benefits/identities/mine')
      const codes = ((res && res.items) || [])
        .filter((x) => x && x.status === 'ACTIVE')
        .map((x) => String(x.identity_code || '').toUpperCase())
      const hint = bestSocialHint(codes)
      this._socialHint = hint
      this.setData({
        hasSocialBenefit: !!hint,
        socialBenefitLabel: hint ? hint.label : '',
        // 有身份时不默认打开，避免静默覆盖会员+券路径；用户显式开关
        useSocialBenefit: hint ? !!this.data.useSocialBenefit : false
      })
    } catch (_) {
      this._socialHint = null
      this.setData({ hasSocialBenefit: false, useSocialBenefit: false, socialBenefitLabel: '' })
    }
  },

  onToggleSocial(e) {
    const raw = e && e.detail ? e.detail.value : false
    const on = raw === true || raw === 'true' || raw === 1 || raw === '1'
    if (on) {
      this._couponManualPick = true
      this._voucherManualPick = true
      this._selectedCoupon = null
      this._selectedVoucher = null
      this.setData({
        useSocialBenefit: true,
        selectedCouponId: 0,
        selectedCouponLabel: '公益折扣不可用券',
        selectedVoucherId: 0,
        selectedVoucherLabel: '公益折扣不可用券'
      }, () => this.refreshSummary())
      return
    }
    this._couponManualPick = false
    this._voucherManualPick = false
    this.setData({ useSocialBenefit: false }, () => this.refreshSummary())
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
  async loadCoupons(force = false) {
    const now = Date.now()
    if (!force && this._couponsAt && now - this._couponsAt < CHECKOUT_CACHE_MS) {
      return
    }
    try {
      await auth.ensureLogin()
      const res = await api.get('/miniprogram/coupons/mine', { store_id: this.submitStoreId() })
      const items = (res && res.items) || []
      this.setData({
        coupons: items,
        couponPickerLabels: ['不使用优惠券'].concat(items.map((c) => voucherUtil.holdDisplayName(c)))
      })
      this._couponsAt = Date.now()
    } catch (_) {
      this.setData({ coupons: [] })
    }
  },
  async loadVouchers(force = false) {
    const now = Date.now()
    if (!force && this._vouchersAt && now - this._vouchersAt < CHECKOUT_CACHE_MS) {
      return
    }
    try {
      await auth.ensureLogin()
      const res = await api.get('/miniprogram/vouchers/mine', { store_id: this.submitStoreId() })
      const items = (res && res.items) || []
      this.setData({
        vouchers: items,
        voucherPickerLabels: ['不使用代金券'].concat(items.map((c) => voucherUtil.holdDisplayName(c)))
      })
      this._vouchersAt = Date.now()
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
  calcCouponDiscount(couponEligible, coupon, memberRate) {
    if (!coupon || !coupon.id) return 0
    const eligible = couponEligible
    const minAmt = parseFloat(coupon.min_order_amount || 0)
    if (eligible < minAmt) return 0
    if (coupon.coupon_type === 'PERCENT') {
      const rate = parseFloat(coupon.discount_value)
      if (!percentBeatsMember(rate, memberRate == null ? 1 : memberRate)) return 0
      return Math.round(eligible * (1 - rate) * 100) / 100
    }
    const fixed = parseFloat(coupon.discount_value) || 0
    return Math.min(fixed, eligible)
  },

  /** 比较总优惠：PERCENT 与会员折互斥取优；FIXED 叠在会员折后 */
  calcCouponTotalSavings(discountable, memberRate, coupon) {
    if (!coupon || !coupon.id) return 0
    const base = Math.max(0, parseFloat(discountable) || 0)
    const mr = parseFloat(memberRate)
    const memberR = Number.isFinite(mr) && mr > 0 && mr < 1 ? mr : 1
    const memberDisc = memberR < 1 ? Math.round(base * (1 - memberR) * 100) / 100 : 0
    const minAmt = parseFloat(coupon.min_order_amount || 0)
    if (coupon.coupon_type === 'PERCENT') {
      const cr = parseFloat(coupon.discount_value)
      if (!percentBeatsMember(cr, memberR)) return 0
      if (base < minAmt) return 0
      return Math.round(base * (1 - cr) * 100) / 100
    }
    const afterMember = Math.max(0, base - memberDisc)
    if (afterMember < minAmt) return 0
    const fixed = Math.min(parseFloat(coupon.discount_value) || 0, afterMember)
    return Math.round((memberDisc + fixed) * 100) / 100
  },

  calcVoucherDiscount(payable, voucher, voucherEligible) {
    return voucherUtil.calcVoucherDiscount(payable, voucher, voucherEligible)
  },
  async refreshSummary() {
    const cart = auth.getCart()
    const { list, total } = cartUtil.cartSummary(cart)
    const boxMap = this.data.boxPriceMap || {}
    let enrichedList = list
    let catalogOk = false
    try {
      const productItems = await this.loadProductCatalog()
      catalogOk = !!(productItems && productItems.length)
      if (catalogOk) {
        enrichedList = cartUtil.enrichCartFromProducts(list, productItems)
      }
    } catch (_) {
      catalogOk = false
    }
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
    const useSocial = !useBalance && !!this.data.useSocialBenefit && !!this._socialHint
    const memberRate = useBalance || useSocial ? 1 : (this._memberRate || 1)
    const discountable = cartUtil.calcDiscountableSubtotal(enrichedList)

    // 公益路径：与券互斥；与会员折比补贴取更优（服务端也会再比一次）
    if (useSocial) {
      this._selectedCoupon = null
      this._selectedVoucher = null
      const socialRate = this._socialHint.rate
      let socialDisc = 0
      if (socialRate > 0 && socialRate < 1) {
        socialDisc = Math.round(discountable * (1 - socialRate) * 100) / 100
      }
      let pay = Math.max(0, sub - socialDisc)
      if (needPack) pay += pack
      pay = Math.max(0, Math.round(pay * 100) / 100)
      await media.hydrateImageUrls(cartPreview, 'imageUrl')
      const settleStoreId = this.submitStoreId()
      this.setData({
        cartPreview,
        cartIds: cartPreview.map((it) => it.product_id),
        settleReady: !!(settleStoreId && catalogOk),
        settleStoreId: settleStoreId || null,
        subtotal: sub.toFixed(2),
        packingFee: pack.toFixed(2),
        discountAmount: '0.00',
        hasDiscount: false,
        socialDiscountAmount: socialDisc.toFixed(2),
        hasSocialDiscount: socialDisc > 0,
        couponDiscountAmount: '0.00',
        hasCouponDiscount: false,
        voucherDiscountAmount: '0.00',
        hasVoucherDiscount: false,
        payEstimate: pay.toFixed(2),
        hasSpecialItems: enrichedList.some((i) => i.no_member_discount),
        canPayBalance: canBal,
        payMethod: nextPayMethod,
        selectedCouponId: 0,
        selectedCouponLabel: '公益折扣不可用券',
        selectedVoucherId: 0,
        selectedVoucherLabel: '公益折扣不可用券',
        socialBenefitLabel: this._socialHint.label,
        socialRecommendTip: ''
      })
      return
    }

    // 手动选了不可用的 PERCENT（同等/更弱）则清空
    if (
      !useBalance &&
      this._selectedCoupon &&
      this._selectedCoupon.coupon_type === 'PERCENT' &&
      !percentBeatsMember(this._selectedCoupon.discount_value, memberRate)
    ) {
      if (this._couponManualPick) {
        my.showToast({ content: '折扣力度未优于会员折，已取消使用', type: 'none', duration: 2500 })
        this._couponManualPick = false
      }
      this._selectedCoupon = null
    }

    if (useBalance) {
      this._selectedCoupon = null
      this._selectedVoucher = null
    } else if (!this._couponManualPick && (this.data.coupons || []).length) {
      const bestCoupon = pickBestCoupon(
        this.data.coupons,
        discountable,
        memberRate,
        (base, mr, c) => this.calcCouponTotalSavings(base, mr, c)
      )
      this._selectedCoupon = bestCoupon
    }

    // PERCENT 严格更优时跳过会员折，由折扣券独占
    let effectiveRate = memberRate
    const sel = this._selectedCoupon
    if (
      !useBalance &&
      sel &&
      sel.coupon_type === 'PERCENT' &&
      percentBeatsMember(sel.discount_value, memberRate)
    ) {
      effectiveRate = 1
    }
    let discount = 0
    if (!useBalance && effectiveRate > 0 && effectiveRate < 1) {
      discount = Math.round(discountable * (1 - effectiveRate) * 100) / 100
    }
    const afterMember = Math.max(0, sub - discount)
    const couponEligible = cartUtil.calcCouponEligibleSubtotal(enrichedList, discount)

    const couponDisc = useBalance
      ? 0
      : this.calcCouponDiscount(couponEligible, this._selectedCoupon, memberRate)
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
    let socialRecommendTip = ''
    if (!useBalance && this._socialHint) {
      const socialSave = Math.round(discountable * (1 - this._socialHint.rate) * 100) / 100
      const memberPathSave = Math.round((discount + couponDisc + voucherDisc) * 100) / 100
      if (socialSave > memberPathSave + 0.009) {
        socialRecommendTip = `开启公益约多省¥${(socialSave - memberPathSave).toFixed(2)}（优于会员折；不可用券）`
      } else if (memberPathSave > socialSave + 0.009) {
        socialRecommendTip = '当前会员折更优，勾选公益也会按会员折结算'
      }
    }
    await media.hydrateImageUrls(cartPreview, 'imageUrl')
    const settleStoreId = this.submitStoreId()
    this.setData({
      cartPreview,
      cartIds: cartPreview.map((it) => it.product_id),
      settleReady: !!(settleStoreId && catalogOk),
      settleStoreId: settleStoreId || null,
      subtotal: sub.toFixed(2),
      packingFee: pack.toFixed(2),
      discountAmount: discount.toFixed(2),
      couponDiscountAmount: couponDisc.toFixed(2),
      hasCouponDiscount: couponDisc > 0,
      voucherDiscountAmount: voucherDisc.toFixed(2),
      hasVoucherDiscount: voucherDisc > 0,
      socialDiscountAmount: '0.00',
      hasSocialDiscount: false,
      socialRecommendTip,
      payEstimate: pay.toFixed(2),
      hasDiscount: discount > 0,
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
    const needPack = String(e.currentTarget.dataset.pack) === '1'
    const patch = { needPack }
    if (!needPack && this.data.scheduleEnabled) {
      // 切回堂食：预约取餐随打包单生效，关闭即恢复立即制作
      patch.scheduleEnabled = false
      patch.scheduleSlotIdx = -1
    }
    this.setData(patch, () => this.refreshSummary())
  },
  onRemark(e) { this.setData({ remark: e.detail.value }) },

  // ---- 快捷备注标签 ----
  toggleQuickRemark(e) {
    const label = String(e.currentTarget.dataset.label || '')
    if (!label) return
    const list = (this.data.quickRemarks || []).slice()
    const hit = list.find((t) => t.label === label)
    if (!hit) return
    const selected = this.data.selectedRemarks.slice()
    const pos = selected.indexOf(label)
    if (pos >= 0) {
      selected.splice(pos, 1)
      hit.on = false
    } else {
      const merged = selected.concat([label]).join(' ')
      const manual = String(this.data.remark || '').trim()
      const total = [merged, manual].filter(Boolean).join(' ')
      if (total.length > REMARK_MAX_LEN) {
        my.showToast({ content: `备注最多 ${REMARK_MAX_LEN} 字`, type: 'none' })
        return
      }
      selected.push(label)
      hit.on = true
    }
    this.setData({ selectedRemarks: selected, quickRemarks: list })
  },

  // ---- 预约取餐时段（打包单） ----
  onScheduleToggle(e) {
    const on = !!(e.detail && e.detail.value)
    this.setData({
      scheduleEnabled: on,
      scheduleSlots: on ? buildPickupSlots(this.data.scheduleDayIdx) : [],
      scheduleSlotIdx: on ? this.data.scheduleSlotIdx : -1
    })
  },

  onScheduleDay(e) {
    const dayIdx = Number(e.currentTarget.dataset.day) === 1 ? 1 : 0
    if (dayIdx === this.data.scheduleDayIdx) return
    this.setData({
      scheduleDayIdx: dayIdx,
      scheduleSlots: buildPickupSlots(dayIdx),
      scheduleSlotIdx: -1
    })
  },

  onScheduleSlot(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    if (!Number.isFinite(idx)) return
    this.setData({ scheduleSlotIdx: idx })
  },

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
  /** 打包单开启预约后返回选定槽位的本地时区 ISO（无 Z 后缀）；关闭/未选返回 null */
  _scheduledPickupIso() {
    if (!this.data.needPack || !this.data.scheduleEnabled) return null
    const slot = (this.data.scheduleSlots || [])[this.data.scheduleSlotIdx]
    return slot ? slot.iso : null
  },

  /** 整单备注 = 快捷标签（空格拼接，去重）+ 手动输入 */
  _composeRemark() {
    const parts = this.data.selectedRemarks.slice()
    const manual = String(this.data.remark || '').trim()
    if (manual) parts.push(manual)
    return parts.filter(Boolean).join(' ')
  },

  async submit() {
    if (this.data.loading) return
    if (!this.data.settleReady) {
      my.showToast({ content: '菜单未就绪，请稍后重试', type: 'none' })
      this.refreshSummary()
      return
    }
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
      const remarkText = this._composeRemark()
      if (remarkText.length > REMARK_MAX_LEN) {
        my.showToast({ content: `备注最多 ${REMARK_MAX_LEN} 字`, type: 'none' })
        return
      }
      if (this.data.needPack && this.data.scheduleEnabled && !this._scheduledPickupIso()) {
        my.showToast({ content: '请选择预约取餐时段', type: 'none' })
        return
      }
      await auth.ensureLogin()
      const order = await api.post('/orders', {
        store_id: this.submitStoreId(),
        table_token: tableToken,
        channel: 'ALIPAY',
        need_pack: !!this.data.needPack,
        dining_type: this.data.needPack ? 'TAKEAWAY' : 'DINE_IN',
        remark: remarkText,
        scheduled_pickup_at: this._scheduledPickupIso() || undefined,
        idempotency_key: this.makeIdempotencyKey(cart),
        skip_promotions: this.data.payMethod === 'BALANCE',
        use_social_benefit: this.data.payMethod !== 'BALANCE' && !!this.data.useSocialBenefit,
        customer_coupon_id: (this.data.payMethod === 'BALANCE' || this.data.useSocialBenefit)
          ? undefined
          : (this.data.selectedCouponId || undefined),
        customer_voucher_id: (this.data.payMethod === 'BALANCE' || this.data.useSocialBenefit)
          ? undefined
          : (this.data.selectedVoucherId || undefined),
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
