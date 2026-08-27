const api = require('./api')
const app = getApp()

function getAuthCode() {
  return new Promise((resolve, reject) => {
    my.getAuthCode({
      scopes: 'auth_user',
      success: resolve,
      fail: reject
    })
  })
}

async function ensureLogin(tableToken) {
  const auth = await getAuthCode()
  if (!auth || !auth.authCode) {
    throw new Error('getAuthCode 未返回 authCode，请检查支付宝登录授权')
  }
  const data = await api.post('/miniprogram/alipay/login', {
    auth_code: auth.authCode,
    table_token: tableToken || app.globalData.tableToken || (my.getStorageSync({ key: 'table_token' }) || {}).data || null,
    nickname: app.globalData.nickname || (my.getStorageSync({ key: 'nickname' }) || {}).data || null
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  app.globalData.customerId = data.customer_id
  app.globalData.customerNo = data.customer_no
  app.globalData.customerToken = data.customer_token
  app.globalData.nickname = data.nickname || ''
  app.globalData.faceStatus = (data.face_status && data.face_status.consent_status) || 'NONE'
  if (data.table) {
    app.globalData.table = data.table
    app.globalData.tableToken = data.table.qr_token
    my.setStorageSync({ key: 'table_token', data: data.table.qr_token })
  }
  my.setStorageSync({ key: 'customer_id', data: data.customer_id })
  my.setStorageSync({ key: 'customer_token', data: data.customer_token })
  my.setStorageSync({ key: 'customer_no', data: data.customer_no || '' })
  if (data.nickname) my.setStorageSync({ key: 'nickname', data: data.nickname })
  return data
}

function saveCart(cart) {
  app.globalData.cart = cart
  my.setStorageSync({ key: 'cart', data: cart })
}

function getCart() {
  return app.globalData.cart || my.getStorageSync({ key: 'cart' }).data || []
}

module.exports = { ensureLogin, saveCart, getCart }
