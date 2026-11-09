const privacy = require('./utils/privacy')
const { parseTableTokenFromScan, normalizeLaunchQuery } = require('./utils/scan')
const { resolveApiBase, isRelease } = require('./config/env')
const auth = require('./utils/auth')
const clientConfig = require('./utils/clientConfig')
const tracker = require('./utils/tracker')
const remoteLogger = require('./utils/logger')
// 远程日志会上报所有 console 输出（含 API 路径、错误详情），仅在非正式版启用：
// - 隐私合规：正式版集中收集用户端错误详情需在《隐私保护指引》中声明用途
// - 性能：每次 console 调用都要 JSON.stringify + 入队，正式版没必要承担
if (!isRelease()) remoteLogger.install()

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
    clientConfig: null,
    networkType: 'unknown', // P3-19：网络状态检测
    isOnline: true
  },
  onLaunch(options) {
    if (!this.globalData.apiBase) {
      this.globalData.apiBase = resolveApiBase()
    }
    const cart = wx.getStorageSync('cart') || []
    this.globalData.cart = cart
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
        try { dineCtx.reconcileCartWithStore() } catch (_) { }
      }
    } catch (_) { }
    this.parseLaunch(options)
    privacy.setupPrivacy()
    privacy.bindAppPrivacyHandler(this)
    clientConfig.loadClientConfig().catch(() => { })
    auth.purgeStaleSessionIfNeeded().catch(() => { })
    // P1-4：启动时恢复查单，防止支付后崩溃/退出导致状态丢失
    try {
      const pay = require('./utils/pay')
      const pending = pay.readPending()
      if (pending && pending.orderId) {
        const _api2 = require('./utils/api')
        pay.confirmPending(_api2, { orderId: pending.orderId, paymentNo: pending.paymentNo }).catch(() => { })
      }
    } catch (_) { }
    // 应用层预拉取：冷启动时提前发起菜单请求，页面 onLoad 时可能已拿到数据
    const _sid = (this.globalData.table && this.globalData.table.store_id)
      || (this.globalData.store && this.globalData.store.id)
      || null
    if (_sid) {
      try {
        const productApi = require('./utils/products')
        const _api = require('./utils/api')
        let authFinger = '0'
        try {
          const auth = require('./utils/auth')
          authFinger = auth.getToken && auth.getToken() ? '1' : '0'
        } catch (_) { }
        this.globalData._menuPrefetch = {
          storeId: String(_sid),
          authFinger,
          promise: productApi.fetchMenuBundle(_api, _sid, null).catch(() => null)
        }
      } catch (_) { }
    }
    // P3-19：网络状态检测，断网时提示用户
    wx.getNetworkType({
      success: (res) => {
        this.globalData.networkType = res.networkType || 'unknown'
        this.globalData.isOnline = res.networkType !== 'none'
      }
    })
    wx.onNetworkStatusChange((res) => {
      this.globalData.networkType = res.networkType || 'unknown'
      this.globalData.isOnline = !!res.isConnected
      if (!res.isConnected) {
        wx.showToast({ title: '网络已断开', icon: 'none', duration: 2000 })
      }
    })
  },
  onError(err) {
    // P1-5：正式版也记录崩溃，不静默吞掉
    try { console.error('[onError]', String(err || '')) } catch (_) { }
    if (!isRelease()) {
      try { remoteLogger.logger.error(String(err || 'App.onError'), err) } catch (_) { }
    }
  },
  // P2-8：全局未处理 Promise 拒绝
  onUnhandledRejection(res) {
    try { console.error('[onUnhandledRejection]', String(res && (res.reason || res))) } catch (_) { }
    if (!isRelease()) {
      try { remoteLogger.logger.error('[onUnhandledRejection]', res && res.reason) } catch (_) { }
    }
  },
  onPageNotFound(res) {
    if (!isRelease()) {
      try {
        remoteLogger.logger.warn('onPageNotFound', res)
      } catch (_) { }
    }
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
    try { tracker.flush() } catch (_) { }
  },
  onHide() {
    // 进入后台/离开前把埋点队列上报出去
    try { tracker.flush() } catch (_) { }
  },
  parseLaunch(options) {
    if (!options) return
    const q = normalizeLaunchQuery(options.query)
    let token = (q.token || '').trim()
    const channel = 'wechat'

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
      } catch (_) { }
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
