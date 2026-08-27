App({
  globalData: {
    // 开发者工具可用 127.0.0.1；真机请改局域网 IP，如 http://192.168.1.8:8000/api/v1
    // 后端：uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    apiBase: 'http://127.0.0.1:8000/api/v1',
    customerId: null,
    customerNo: null,
    nickname: '',
    tableToken: '',
    table: null,
    channel: 'alipay',
    faceStatus: 'NONE',
    cart: []
  },
  onLaunch(options) {
    const cart = (my.getStorageSync({ key: 'cart' }).data) || []
    this.globalData.cart = cart
    this.parseLaunch(options)
  },
  onShow(options) {
    if (options) this.parseLaunch(options)
  },
  parseLaunch(options) {
    const q = (options && options.query) || {}
    let token = q.token || ''
    let channel = q.channel || ''

    // 支付宝扫码 / 小程序码可能带 query 或 scene
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
      my.setStorageSync({ key: 'table_token', data: token })
    }
    if (channel) {
      this.globalData.channel = channel
    }
  }
})
