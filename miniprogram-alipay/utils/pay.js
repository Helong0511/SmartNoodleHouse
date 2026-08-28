/** 支付宝小程序支付：mock 环境跳过收银台，生产调 my.tradePay */

function isMockPay(payData) {
  if (!payData) return false
  return !!(payData.mock_settled || payData._mock)
}

function requestAlipayPay(payData) {
  return new Promise((resolve, reject) => {
    if (!payData) {
      reject(new Error('支付参数为空'))
      return
    }
    if (isMockPay(payData)) {
      resolve(payData)
      return
    }
    const tradeNO = payData.trade_no || payData.tradeNO
    if (!tradeNO) {
      reject(new Error('支付参数不完整，请确认已配置支付宝支付'))
      return
    }
    my.tradePay({
      tradeNO: String(tradeNO),
      success: (res) => {
        if (res && (res.resultCode === '9000' || res.resultCode === 9000)) {
          resolve(res)
          return
        }
        reject(new Error((res && res.memo) || '支付未完成'))
      },
      fail: (err) => {
        const msg = (err && err.errorMessage) || (err && err.memo) || '支付已取消'
        if (/cancel|6001/i.test(msg)) {
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
  requestAlipayPay
}
