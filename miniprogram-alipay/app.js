const privacy = require('./utils/privacy')
const { parseTableTokenFromScan } = require('./utils/scan')
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
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
    tabCartBadge.updateTabCartBadge(this.globalData.cart || auth.readStorage('cart') || [])
  },
  parseLaunch(options) {
    const q = (options && options.query) || {}
    let token = q.token || ''
    let channel = q.channel || ''

    const sceneRaw = (options && options.scene) || q.scene || ''
    if (!token && sceneRaw) {
      try {
        const scene = decodeURIComponent(String(sceneRaw))
        token = parseTableTokenFromScan(scene)
        if (!channel && scene.indexOf('channel=') >= 0) {
          scene.split('&').forEach((pair) => {
            const [k, v] = pair.split('=')
            if (k === 'channel' && v) channel = v
          })
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
