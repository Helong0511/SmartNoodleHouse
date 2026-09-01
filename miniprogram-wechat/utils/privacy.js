/** 微信隐私合规：启动检查、授权监听、打开隐私协议 */

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

function openPrivacyContract() {
  if (!wx.openPrivacyContract) {
    wx.showToast({ title: '请升级微信版本', icon: 'none' })
    return Promise.resolve(false)
  }
  return new Promise((resolve) => {
    wx.openPrivacyContract({
      success: () => resolve(true),
      fail: () => resolve(false)
    })
  })
}

/** 主动触发隐私授权（会回调 onNeedPrivacyAuthorization） */
function requirePrivacyAuthorize() {
  if (!wx.requirePrivacyAuthorize) {
    return Promise.resolve(true)
  }
  return new Promise((resolve) => {
    wx.requirePrivacyAuthorize({
      success: () => resolve(true),
      fail: () => resolve(false)
    })
  })
}

module.exports = {
  setupPrivacy,
  onNeedPrivacyAuthorization,
  openPrivacyContract,
  requirePrivacyAuthorize
}
