App({
  globalData: {
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
    const cart = my.getStorageSync({ key: 'cart' }).data || []
    this.globalData.cart = cart
    const q = (options && options.query) || {}
    if (q.token) {
      this.globalData.tableToken = q.token
      my.setStorageSync({ key: 'table_token', data: q.token })
    }
  }
})
