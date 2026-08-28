const api = require('./api')

let cachedTemplates = null

async function loadTemplates() {
  if (cachedTemplates) return cachedTemplates
  try {
    const data = await api.get('/miniprogram/config', {}, false)
    cachedTemplates = (data && data.subscribe_templates) || {}
  } catch (_) {
    cachedTemplates = {}
  }
  return cachedTemplates
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

module.exports = {
  loadTemplates,
  requestOrderReadySubscribe
}
