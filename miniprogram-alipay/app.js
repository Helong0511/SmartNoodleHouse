const privacy = require('./utils/privacy')
const { parseTableTokenFromScan, normalizeLaunchQuery } = require('./utils/scan')
const { resolveApiBase } = require('./config/env')
const auth = require('./utils/auth')
const tabCartBadge = require('./utils/tabCartBadge')
const clientConfig = require('./utils/clientConfig')
const remoteLogger = require('./utils/logger')
remoteLogger.install()

App({
  globalData: {
    apiBase: resolveApiBase(),
    customerId: null,
    customerNo: null,
    nickname: '',
    avatarUrl: '',
    tableToken: '',
    table: null,
    store: null,
    channel: 'alipay',
    cart: [],
    needPrivacyPopup: false,
    privacyResolve: null,
    enableAlipayPhoneBind: false,
    enableAlipayAvatar: true,
    enableAlipayNickname: true,
    clientConfig: null
  },
  onLaunch(options) {
    if (!this.globalData.apiBase) {
      this.globalData.apiBase = resolveApiBase()
    }
    this.globalData.cart = auth.readStorage('cart') || []
    this.globalData.nickname = auth.readStorage('nickname') || ''
    this.globalData.avatarUrl = auth.readStorage('avatar_url') || ''
    this.globalData.customerToken = auth.readStorage('customer_token') || ''
    this.globalData.customerId = auth.readStorage('customer_id') || null
    this.globalData.customerNo = auth.readStorage('customer_no') || ''
    this.globalData.store = auth.readStorage('dine_store') || null
    this.globalData.table = auth.readStorage('dine_table') || null
    this.globalData.tableToken = auth.readStorage('table_token') || ''
    tabCartBadge.updateTabCartBadge(this.globalData.cart)
    this.parseLaunch(options)
    privacy.setupPrivacy()
    privacy.bindAppPrivacyHandler(this)
    clientConfig.loadClientConfig().catch(() => {})
    // 库重置后本地 customer_token 会失效；静默探测并清理，避免启动期 Uncaught
    auth.purgeStaleSessionIfNeeded().catch(() => {})
  },
  onError(err) {
    try {
      remoteLogger.logger.error(String(err || 'App.onError'), err)
    } catch (_) {}
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
    tabCartBadge.updateTabCartBadge(this.globalData.cart || auth.readStorage('cart') || [])
  },
  parseLaunch(options) {
    if (!options) return
    // 支付宝：query 可能是对象，也可能是 "token=xx&channel=alipay" 字符串
    const q = normalizeLaunchQuery(options.query)
    let token = (q.token || '').trim()
    let channel = (q.channel || '').trim()

    // Scheme 的 page 会落到 options.path；query 内 path 须用归一化后的 q（query 可能是字符串）
    if (!token) {
      const fromPage = parseTableTokenFromScan(
        [options.path, q.path]
          .filter(Boolean)
          .join('&')
      )
      if (fromPage) token = fromPage
    }

    const sceneRaw = options.scene || q.scene || ''
    if (!token && sceneRaw) {
      try {
        const scene = String(sceneRaw)
        token = parseTableTokenFromScan(scene)
        if (!channel) {
          const nested = normalizeLaunchQuery(scene)
          if (nested.channel) channel = nested.channel
        }
      } catch (_) {}
    }

    if (token) {
      this.globalData.tableToken = token
      my.setStorageSync({ key: 'table_token', data: token })
    }
    if (channel) {
      this.globalData.channel = channel
    }
  }
})
