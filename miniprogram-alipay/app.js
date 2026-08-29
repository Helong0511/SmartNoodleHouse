const { parseTableTokenFromScan } = require('./utils/scan')
const { resolveApiBase } = require('./config/env')
const auth = require('./utils/auth')

App({
  globalData: {
    apiBase: resolveApiBase(),
    customerId: null,
    customerNo: null,
    nickname: '',
    tableToken: '',
    table: null,
    store: null,
    channel: 'alipay',
    faceStatus: 'NONE',
    cart: []
  },
  onLaunch(options) {
    if (!this.globalData.apiBase) {
      this.globalData.apiBase = resolveApiBase()
    }
    this.globalData.cart = auth.readStorage('cart') || []
    this.globalData.nickname = auth.readStorage('nickname') || ''
    this.globalData.customerToken = auth.readStorage('customer_token') || ''
    this.globalData.customerId = auth.readStorage('customer_id') || null
    this.globalData.customerNo = auth.readStorage('customer_no') || ''
    this.globalData.store = auth.readStorage('dine_store') || null
    this.globalData.table = auth.readStorage('dine_table') || null
    this.globalData.tableToken = auth.readStorage('table_token') || ''
    this.parseLaunch(options)
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
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
