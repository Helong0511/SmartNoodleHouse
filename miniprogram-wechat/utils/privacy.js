/** 微信隐私合规：启动检查、授权监听、打开隐私协议 */

const PRIVACY_RESOLVE_TIMEOUT_MS = 12000

function getAppSafe() {
  try {
    return getApp()
  } catch (_) {
    return null
  }
}

function clearPrivacySafetyTimer(app) {
  if (!app || !app.globalData || !app.globalData.privacySafetyTimer) return
  clearTimeout(app.globalData.privacySafetyTimer)
  app.globalData.privacySafetyTimer = null
}

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

/** 弹出隐私同意层（不调 requirePrivacyAuthorize，避免 SDK 内部 timer 超时） */
function showPrivacyPopup() {
  const app = getAppSafe()
  if (!app) return false
  if (app._privacyPopup && app._privacyPopup.show) {
    app._privacyPopup.show()
    return true
  }
  try {
    const pages = getCurrentPages()
    const page = pages && pages[pages.length - 1]
    if (page && page.selectComponent) {
      const popup = page.selectComponent('#privacy-popup')
      if (popup && popup.show) {
        popup.show()
        return true
      }
    }
  } catch (_) {}
  return false
}

function notifyPagePrivacyNeed() {
  try {
    const pages = getCurrentPages()
    const page = pages && pages[pages.length - 1]
    if (page && typeof page.setData === 'function') {
      page.setData({ needPrivacyAgree: true })
    }
  } catch (_) {}
}

function clearPrivacyPending(app) {
  if (!app || !app.globalData) return
  clearPrivacySafetyTimer(app)
  app.globalData.privacyResolve = null
  app.globalData.needPrivacyPopup = false
  if (app._privacyPopup && app._privacyPopup.hide) app._privacyPopup.hide()
}

function invokePrivacyResolve(resolve, payload) {
  if (typeof resolve !== 'function') return
  try {
    resolve(payload)
  } catch (e) {
    console.warn('[privacy] resolve failed', e)
  }
}

/** 用户点击 agreePrivacyAuthorization 后调用 */
function finishPrivacyAgree(buttonId) {
  const app = getAppSafe()
  if (!app || !app.globalData) return
  const resolve = app.globalData.privacyResolve
  clearPrivacyPending(app)
  try {
    wx.setStorageSync('privacy_agreed_v1', Date.now())
  } catch (_) {}
  invokePrivacyResolve(resolve, { buttonId: buttonId || 'agree-btn', event: 'agree' })
}

function finishPrivacyReject() {
  const app = getAppSafe()
  if (!app || !app.globalData) return
  const resolve = app.globalData.privacyResolve
  clearPrivacyPending(app)
  invokePrivacyResolve(resolve, { event: 'disagree' })
}

function bindAppPrivacyHandler(app) {
  onNeedPrivacyAuthorization((resolve) => {
    if (!app.globalData) {
      invokePrivacyResolve(resolve, { event: 'disagree' })
      return
    }
    if (app.globalData.privacyResolve) {
      invokePrivacyResolve(app.globalData.privacyResolve, { event: 'disagree' })
    }
    app.globalData.privacyResolve = resolve
    app.globalData.needPrivacyPopup = true
    clearPrivacySafetyTimer(app)
    app.globalData.privacySafetyTimer = setTimeout(() => {
      if (app.globalData.privacyResolve !== resolve) return
      console.warn('[privacy] resolve timeout, auto disagree to avoid SDK hang')
      finishPrivacyReject()
    }, PRIVACY_RESOLVE_TIMEOUT_MS)
    notifyPagePrivacyNeed()
    if (!showPrivacyPopup()) {
      wx.showToast({ title: '请先点「同意隐私指引」', icon: 'none' })
    }
  })
}

module.exports = {
  setupPrivacy,
  onNeedPrivacyAuthorization,
  openPrivacyContract,
  showPrivacyPopup,
  finishPrivacyAgree,
  finishPrivacyReject,
  bindAppPrivacyHandler
}
