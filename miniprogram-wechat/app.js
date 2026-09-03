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
      wx.setStorageSync('table_token', token)
    }
    if (channel) {
      this.globalData.channel = channel
    }
  }
})
