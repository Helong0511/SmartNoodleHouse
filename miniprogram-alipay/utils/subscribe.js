/**
 * 支付宝小程序订阅消息
 * P0-3：补齐支付宝端订阅消息推送能力，与微信端对齐
 * 支付宝使用 my.requestSubscribeMessage 发起订阅（需在开放平台配置消息模板）
 */
var clientConfig = require('./clientConfig')

function loadTemplates() {
  return clientConfig.loadClientConfig().then(function (cfg) {
    return (cfg && cfg.subscribe_templates) || {}
  })
}

/**
 * 下单成功后引导订阅「出餐/待取餐」提醒。
 * 只用 alipay_order_ready，禁止降级微信 order_ready（模板 ID 不互通）。
 */
function requestOrderReadySubscribe() {
  if (typeof my === 'undefined' || !my.requestSubscribeMessage) return Promise.resolve(null)
  return loadTemplates().then(function (tmpls) {
    var tmplId = tmpls.alipay_order_ready
    if (!tmplId) return null
    return new Promise(function (resolve) {
      my.requestSubscribeMessage({
        templateIds: [tmplId],
        success: function (res) { resolve(res) },
        fail: function () { resolve(null) }
      })
    })
  })
}

/**
 * 储值页引导订阅「余额偏低」提醒。
 * 只用 alipay_balance_low，禁止降级微信 balance_low。
 */
function requestBalanceLowSubscribe() {
  if (typeof my === 'undefined' || !my.requestSubscribeMessage) return Promise.resolve(null)
  return loadTemplates().then(function (tmpls) {
    var tmplId = tmpls.alipay_balance_low
    if (!tmplId) return null
    return new Promise(function (resolve) {
      my.requestSubscribeMessage({
        templateIds: [tmplId],
        success: function (res) { resolve(res) },
        fail: function () { resolve(null) }
      })
    })
  })
}

module.exports = {
  loadTemplates: loadTemplates,
  requestOrderReadySubscribe: requestOrderReadySubscribe,
  requestBalanceLowSubscribe: requestBalanceLowSubscribe
}
