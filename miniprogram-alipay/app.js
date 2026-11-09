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
    channel: 'alipay',
    cart: [],
    needPrivacyPopup: false,
    privacyResolve: null,
    enableAlipayPhoneBind: false,
    enableAlipayAvatar: true,
    enableAlipayNickname: true,
    clientConfig: null,
    networkType: 'unknown', // P3-19：网络状态检测
    isOnline: true
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
    // P2-1：启动恢复后立即校验购物车门店归属，跨店清空
    try {
      if (typeof auth.ensureCartForCurrentStore === 'function') {
        auth.ensureCartForCurrentStore()
      }
    } catch (_) { }
    // 桌台恢复：qr_token 为静态凭证，是否有效由后端 /entry 裁决，
    // 本地不再因 4h TTL 删除 token（旧逻辑会导致真机反复要求扫码绑桌）
    this.parseLaunch(options)
    // 先注册平台授权回调，再读取授权状态；探测失败或本地未同意时显示自有隐私提示。
    privacy.bindAppPrivacyHandler(this)
    privacy.setupPrivacy().then((res) => {
      if ((res && res.needAuthorization) || !privacy.hasPrivacyAgreed()) {
        this.globalData.needPrivacyPopup = true
        privacy.showPrivacyPopup()
      }
    }).catch(() => {
      this.globalData.needPrivacyPopup = true
      privacy.showPrivacyPopup()
    })
    clientConfig.loadClientConfig().catch(() => { })
    // 库重置后本地 customer_token 会失效；静默探测并清理，避免启动期 Uncaught
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
    my.getNetworkType({
      success: (res) => {
        this.globalData.networkType = res.networkType || 'unknown'
        this.globalData.isOnline = res.networkType !== 'none'
      }
    })
    my.onNetworkStatusChange((res) => {
      this.globalData.networkType = res.networkType || 'unknown'
      this.globalData.isOnline = !!res.isConnected
      if (!res.isConnected) {
        my.showToast({ content: '网络已断开', type: 'none', duration: 2000 })
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
    my.switchTab({
      url: '/pages/menu/menu',
      fail: () => my.reLaunch({ url: '/pages/index/index' })
    })
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
    // 支付宝：query 可能是对象，也可能是 "token=xx&channel=alipay" 字符串
    const q = normalizeLaunchQuery(options.query)
    let token = (q.token || '').trim()
    const channel = 'alipay'

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
      } catch (_) { }
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
