const privacy = require('./utils/privacy')
const { parseTableTokenFromScan, normalizeLaunchQuery } = require('./utils/scan')
const { resolveApiBase } = require('./config/env')
const auth = require('./utils/auth')
const tabCartBadge = require('./utils/tabCartBadge')
const clientConfig = require('./utils/clientConfig')
const tracker = require('./utils/tracker')
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
    // 桌台恢复：qr_token 为静态凭证，是否有效由后端 /entry 裁决，
    // 本地不再因 4h TTL 删除 token（旧逻辑会导致真机反复要求扫码绑桌）
    try {
      const dineCtx = require('./utils/context')
      const cached = dineCtx.readCachedContext()
      if (cached.token || cached.table || cached.store) {
        this.globalData.store = cached.store
        this.globalData.table = cached.table
        this.globalData.tableToken = cached.token || ''
        // P2-1：启动恢复后立即校验购物车门店归属，跨店清空
        try { dineCtx.reconcileCartWithStore() } catch (_) {}
      }
    } catch (_) {}
    this.parseLaunch(options)
    privacy.setupPrivacy()
    privacy.bindAppPrivacyHandler(this)
    clientConfig.loadClientConfig().catch(() => {})
    auth.purgeStaleSessionIfNeeded().catch(() => {})
    // 应用层预拉取：冷启动时提前发起菜单请求，页面 onLoad 时可能已拿到数据
    const _sid = (this.globalData.table && this.globalData.table.store_id)
      || (this.globalData.store && this.globalData.store.id)
      || null
    if (_sid) {
      try {
        const productApi = require('./utils/products')
        const _api = require('./utils/api')
        this.globalData._menuPrefetch = {
          storeId: String(_sid),
          promise: productApi.fetchMenuBundle(_api, _sid, null).catch(() => null)
        }
      } catch (_) {}
    }
  },
  onError(err) {
    try {
      remoteLogger.logger.error(String(err || 'App.onError'), err)
    } catch (_) {}
  },
  onPageNotFound(res) {
    try {
      remoteLogger.logger.warn('onPageNotFound', res)
    } catch (_) {}
    const path = (res && res.path) || ''
    if (!path) {
      wx.switchTab({
        url: '/pages/menu/menu',
        fail: () => wx.reLaunch({ url: '/pages/index/index' })
      })
      return
    }
    wx.reLaunch({ url: '/pages/index/index' })
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
    tabCartBadge.updateTabCartBadge(this.globalData.cart || wx.getStorageSync('cart') || [])
    try { tracker.flush() } catch (_) {}
  },
  onHide() {
    // 进入后台/离开前把埋点队列上报出去
    try { tracker.flush() } catch (_) {}
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
