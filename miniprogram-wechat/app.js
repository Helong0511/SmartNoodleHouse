const privacy = require('./utils/privacy')
const { parseTableTokenFromScan } = require('./utils/scan')
const { resolveApiBase } = require('./config/env')

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
    faceStatus: 'NONE',
    cart: []
  },
  onLaunch(options) {
    const cart = wx.getStorageSync('cart') || []
    this.globalData.cart = cart
    this.globalData.nickname = wx.getStorageSync('nickname') || ''
    this.globalData.avatarUrl = wx.getStorageSync('avatar_url') || ''
    this.globalData.customerToken = wx.getStorageSync('customer_token') || ''
    this.globalData.customerId = wx.getStorageSync('customer_id') || null
    this.globalData.customerNo = wx.getStorageSync('customer_no') || ''
    this.globalData.store = wx.getStorageSync('dine_store') || null
    this.globalData.table = wx.getStorageSync('dine_table') || null
    this.parseLaunch(options)
    privacy.setupPrivacy()
    privacy.onNeedPrivacyAuthorization((resolve) => {
      wx.showModal({
        title: '隐私保护提示',
        content: '为提供登录、点餐、人脸迎宾等服务，需您同意《用户隐私保护指引》。',
        confirmText: '同意',
        cancelText: '拒绝',
        success: (res) => {
          if (res.confirm) resolve({ buttonId: 'agree-btn', event: 'agree' })
          else resolve({ buttonId: 'disagree-btn', event: 'disagree' })
        }
      })
    })
  },
  onShow(options) {
    // 从扫码/分享再次进入时补解析
    if (options) this.parseLaunch(options)
  },
  parseLaunch(options) {
    const q = (options && options.query) || {}
    let token = q.token || ''
    let channel = q.channel || ''

    // 微信小程序码 scene（需短参数；支持 token=xxx 或纯 token）
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
