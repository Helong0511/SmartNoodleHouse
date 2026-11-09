const clientConfig = require('./clientConfig')

let cachedTemplates = {}

async function loadTemplates() {
  const cfg = await clientConfig.loadClientConfig()
  cachedTemplates = (cfg && cfg.subscribe_templates) || {}
  return cachedTemplates
}

/** 必须由明确的用户点击触发；模板 ID 预先异步加载，点击时同步调用平台 API。 */
function requestOrderReadySubscribe() {
  if (typeof wx === 'undefined' || !wx.requestSubscribeMessage) return Promise.resolve(null)
  const tmplId = cachedTemplates.order_ready
  if (!tmplId) return Promise.resolve(null)
  return new Promise((resolve) => {
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => resolve(res),
      fail: () => resolve(null)
    })
  })
}

/** 储值页余额提醒同样必须由用户点击触发。 */
function requestBalanceLowSubscribe() {
  if (typeof wx === 'undefined' || !wx.requestSubscribeMessage) return Promise.resolve(null)
  const tmplId = cachedTemplates.balance_low
  if (!tmplId) return Promise.resolve(null)
  return new Promise((resolve) => {
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => resolve(res),
      fail: () => resolve(null)
    })
  })
}

module.exports = {
  loadTemplates,
  requestOrderReadySubscribe,
  requestBalanceLowSubscribe
}
