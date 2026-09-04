const privacy = require('./utils/privacy')
const { parseTableTokenFromScan, normalizeLaunchQuery } = require('./utils/scan')
const { resolveApiBase } = require('./config/env')
const auth = require('./utils/auth')
const tabCartBadge = require('./utils/tabCartBadge')
const clientConfig = require('./utils/clientConfig')

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
    channel: 'wechat',
    cart: [],
    needPrivacyPopup: false,
    privacyResolve: null,
    enableWxPhoneBind: false,
    enableWxAvatar: true,
    enableWxNickname: true,
    clientConfig: null
  },
  onLaunch(options) {
    if (!this.globalData.apiBase) {
      this.globalData.apiBase = resolveApiBase()
    }
    const cart = wx.getStorageSync('cart') || []
    this.globalData.cart = cart
    tabCartBadge.updateTabCartBadge(cart)
    this.globalData.nickname = wx.getStorageSync('nickname') || ''
    this.globalData.avatarUrl = wx.getStorageSync('avatar_url') || ''
    this.globalData.customerToken = wx.getStorageSync('customer_token') || ''
    this.globalData.customerId = wx.getStorageSync('customer_id') || null
    this.globalData.customerNo = wx.getStorageSync('customer_no') || ''
    this.globalData.store = wx.getStorageSync('dine_store') || null
    this.globalData.table = wx.getStorageSync('dine_table') || null
    this.parseLaunch(options)
    privacy.setupPrivacy()
    privacy.bindAppPrivacyHandler(this)
    clientConfig.loadClientConfig().catch(() => {})
    auth.purgeStaleSessionIfNeeded().catch(() => {})
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
    tabCartBadge.updateTabCartBadge(this.globalData.cart || wx.getStorageSync('cart') || [])
  },
  parseLaunch(options) {
    if (!options) return
    const q = normalizeLaunchQuery(options.query)
    let token = (q.token || '').trim()
    let channel = (q.channel || '').trim()

    // path 偶发带 ?token=；query 内 path 须用归一化后的 q（query 可能是字符串）
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
      wx.setStorageSync('table_token', token)
    }
    if (channel) {
      this.globalData.channel = channel
    }
  }
})
