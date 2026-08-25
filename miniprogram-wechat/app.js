App({
  globalData: {
    apiBase: 'http://127.0.0.1:8000/api/v1',
    customerId: null,
    customerNo: null,
    nickname: '',
    tableToken: '',
    table: null,
    channel: 'wechat',
    faceStatus: 'NONE',
    cart: []
  },
  onLaunch(options) {
    const cart = wx.getStorageSync('cart') || []
    this.globalData.cart = cart
    this.parseScene(options)
  },
  parseScene(options) {
    const q = options.query || {}
    if (q.token) {
      this.globalData.tableToken = q.token
      wx.setStorageSync('table_token', q.token)
    }
    if (q.channel) {
      this.globalData.channel = q.channel
    }
  }
})
