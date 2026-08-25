const api = require('./api')
const app = getApp()

function wxLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: resolve,
      fail: reject
    })
  })
}

async function ensureLogin(tableToken) {
  const loginRes = await wxLogin()
  const data = await api.post('/miniprogram/wechat/login', {
    code: loginRes.code,
    table_token: tableToken || app.globalData.tableToken || wx.getStorageSync('table_token') || null,
    nickname: app.globalData.nickname || null
  }, false)
  app.globalData.customerId = data.customer_id
  app.globalData.customerNo = data.customer_no
  app.globalData.customerToken = data.customer_token
  app.globalData.nickname = data.nickname || ''
  app.globalData.faceStatus = (data.face_status && data.face_status.consent_status) || 'NONE'
  if (data.table) {
    app.globalData.table = data.table
    app.globalData.tableToken = data.table.qr_token
    wx.setStorageSync('table_token', data.table.qr_token)
  }
  wx.setStorageSync('customer_id', data.customer_id)
  wx.setStorageSync('customer_token', data.customer_token)
  return data
}

function saveCart(cart) {
  app.globalData.cart = cart
  wx.setStorageSync('cart', cart)
}

function getCart() {
  return app.globalData.cart || wx.getStorageSync('cart') || []
}

module.exports = {
  ensureLogin,
  saveCart,
  getCart
}
