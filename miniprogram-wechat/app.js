App({
  globalData: {
    // 开发者工具可用 127.0.0.1；真机预览请改成电脑局域网 IP，如 http://192.168.1.8:8000/api/v1
    // 后端请监听 0.0.0.0：uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    // 若出现 ERR_CONNECTION_RESET：多半是后端重启/崩溃，或未勾选「不校验合法域名」
    apiBase: 'http://127.0.0.1:8000/api/v1',
    customerId: null,
    customerNo: null,
    nickname: '',
    avatarUrl: '',
    tableToken: '',
    table: null,
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
    this.parseLaunch(options)
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
        if (scene.indexOf('token=') >= 0) {
          scene.split('&').forEach((pair) => {
            const [k, v] = pair.split('=')
            if (k === 'token' && v) token = v
            if (k === 'channel' && v) channel = v
          })
        } else if (scene) {
          token = scene
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
