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
  const cachedToken = app.globalData.customerToken || wx.getStorageSync('customer_token')
  if (cachedToken) {
    app.globalData.customerToken = cachedToken
    app.globalData.customerId = app.globalData.customerId || wx.getStorageSync('customer_id') || null
    app.globalData.customerNo = app.globalData.customerNo || wx.getStorageSync('customer_no') || ''
    app.globalData.nickname = app.globalData.nickname || wx.getStorageSync('nickname') || ''
    app.globalData.avatarUrl = app.globalData.avatarUrl || wx.getStorageSync('avatar_url') || ''
    return {
      customer_id: app.globalData.customerId,
      customer_no: app.globalData.customerNo,
      customer_token: cachedToken,
      nickname: app.globalData.nickname,
      avatar_url: app.globalData.avatarUrl,
      need_member_prompt: !app.globalData.nickname
    }
  }

  const loginRes = await wxLogin()
  if (!loginRes || !loginRes.code) {
    throw new Error('wx.login 未返回 code，请检查开发者工具登录状态')
  }
  const nickname = app.globalData.nickname || wx.getStorageSync('nickname') || null
  const avatarUrl = app.globalData.avatarUrl || wx.getStorageSync('avatar_url') || null
  const data = await api.post('/miniprogram/wechat/login', {
    code: loginRes.code,
    table_token: tableToken || app.globalData.tableToken || wx.getStorageSync('table_token') || null,
    nickname,
    avatar_url: avatarUrl
  }, false)
  if (!data || !data.customer_token) {
    throw new Error('登录成功但未返回 customer_token')
  }
  // 微信授权登录后落库客户信息，并写回本地
  app.globalData.customerId = data.customer_id
  app.globalData.customerNo = data.customer_no
  app.globalData.customerToken = data.customer_token
  app.globalData.nickname = data.nickname || nickname || ''
  app.globalData.avatarUrl = data.avatar_url || avatarUrl || ''
  app.globalData.faceStatus = (data.face_status && data.face_status.consent_status) || 'NONE'
  if (data.table) {
    app.globalData.table = data.table
    app.globalData.tableToken = data.table.qr_token
    wx.setStorageSync('table_token', data.table.qr_token)
    if (data.table.store) {
      app.globalData.store = data.table.store
      wx.setStorageSync('dine_store', data.table.store)
    }
  }
  wx.setStorageSync('customer_id', data.customer_id)
  wx.setStorageSync('customer_token', data.customer_token)
  wx.setStorageSync('customer_no', data.customer_no || '')
  if (app.globalData.nickname) wx.setStorageSync('nickname', app.globalData.nickname)
  if (app.globalData.avatarUrl) wx.setStorageSync('avatar_url', app.globalData.avatarUrl)
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
