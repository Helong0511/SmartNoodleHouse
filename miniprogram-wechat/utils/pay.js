/** 微信小程序支付：mock 环境跳过收银台，生产调 wx.requestPayment */

function isMockPay(payData) {
  if (!payData) return false
  return !!(payData.mock_settled || payData._mock)
}

function requestWechatPay(payData) {
  return new Promise((resolve, reject) => {
    if (!payData) {
      reject(new Error('支付参数为空'))
      return
    }
    if (isMockPay(payData)) {
      resolve(payData)
      return
    }
    const { timeStamp, nonceStr, package: pkg, paySign, signType } = payData
    if (!timeStamp || !nonceStr || !pkg || !paySign) {
      reject(new Error('支付参数不完整，请确认已配置微信支付'))
      return
    }
    wx.requestPayment({
      timeStamp: String(timeStamp),
      nonceStr,
      package: pkg,
      signType: signType || 'RSA',
      paySign,
      success: (res) => resolve(res),
      fail: (err) => {
        const msg = (err && err.errMsg) || '支付已取消'
        if (/cancel/i.test(msg)) {
          reject(new Error('您已取消支付'))
        } else {
          reject(new Error(msg))
        }
      }
    })
  })
}

module.exports = {
  isMockPay,
  requestWechatPay
}
