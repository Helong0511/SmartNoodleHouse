const clientConfig = require('./clientConfig')

async function loadTemplates() {
  const cfg = await clientConfig.loadClientConfig()
  return (cfg && cfg.subscribe_templates) || {}
}

/** 下单成功后引导订阅「出餐/待取餐」提醒（需后台配置模板 ID） */
async function requestOrderReadySubscribe() {
  if (!wx.requestSubscribeMessage) return
  const tmpls = await loadTemplates()
  const tmplId = tmpls.order_ready
  if (!tmplId) return
  return new Promise((resolve) => {
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => resolve(res),
      fail: () => resolve(null)
    })
  })
}

/** 储值页引导订阅「余额偏低」提醒（需后台配置 balance_low 模板 ID） */
async function requestBalanceLowSubscribe() {
  if (!wx.requestSubscribeMessage) return
  const tmpls = await loadTemplates()
  const tmplId = tmpls.balance_low
  if (!tmplId) return
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
