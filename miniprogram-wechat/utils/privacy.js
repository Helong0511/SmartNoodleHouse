/** 微信隐私合规：启动时检查并监听授权 */

function setupPrivacy() {
  if (!wx.getPrivacySetting) return Promise.resolve({ needAuthorization: false })

  return new Promise((resolve) => {
    wx.getPrivacySetting({
      success: (res) => resolve(res || { needAuthorization: false }),
      fail: () => resolve({ needAuthorization: false })
    })
  })
}

function onNeedPrivacyAuthorization(handler) {
  if (wx.onNeedPrivacyAuthorization) {
    wx.onNeedPrivacyAuthorization(handler)
  }
}

module.exports = {
  setupPrivacy,
  onNeedPrivacyAuthorization
}
