/**
 * 支付宝小程序订阅消息。
 * 模板 ID 预先异步加载；点击订阅按钮时同步调用 requestSubscribeMessage。
 */
var clientConfig = require('./clientConfig')
var cachedTemplates = {}

function loadTemplates() {
  return clientConfig.loadClientConfig().then(function (cfg) {
    cachedTemplates = (cfg && cfg.subscribe_templates) || {}
    return cachedTemplates
  })
}

/** 下单成功后由顾客主动点击「订阅出餐提醒」触发。 */
function requestOrderReadySubscribe() {
  if (typeof my === 'undefined' || !my.requestSubscribeMessage) return Promise.resolve(null)
  var tmplId = cachedTemplates.alipay_order_ready
  if (!tmplId) return Promise.resolve(null)
  return new Promise(function (resolve) {
    my.requestSubscribeMessage({
      entityIds: [tmplId],
      success: function (res) { resolve(res) },
      fail: function () { resolve(null) }
    })
  })
}

/** 顾客主动点击「订阅余额提醒」触发。 */
function requestBalanceLowSubscribe() {
  if (typeof my === 'undefined' || !my.requestSubscribeMessage) return Promise.resolve(null)
  var tmplId = cachedTemplates.alipay_balance_low
  if (!tmplId) return Promise.resolve(null)
  return new Promise(function (resolve) {
    my.requestSubscribeMessage({
      entityIds: [tmplId],
      success: function (res) { resolve(res) },
      fail: function () { resolve(null) }
    })
  })
}

module.exports = {
  loadTemplates: loadTemplates,
  requestOrderReadySubscribe: requestOrderReadySubscribe,
  requestBalanceLowSubscribe: requestBalanceLowSubscribe
}
