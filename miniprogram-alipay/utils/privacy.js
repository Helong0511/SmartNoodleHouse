/** 支付宝隐私合规：启动检查、授权监听、打开隐私协议 */

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

function hasPrivacyAgreed() {
  try {
    const stored = my.getStorageSync({ key: 'privacy_agreed_v1' })
    const value = stored && Object.prototype.hasOwnProperty.call(stored, 'data')
      ? stored.data
      : stored
    const timestamp = Number(value)
    return value === true || (Number.isFinite(timestamp) && timestamp > 0)
  } catch (_) {
    return false
  }
}

function setupPrivacy() {
  // 平台接口缺失/调用失败时必须走自有隐私提示，不能把“无法检测”当作“已同意”。
  if (!my.getPrivacySetting) return Promise.resolve({ needAuthorization: true, unsupported: true })
  return new Promise((resolve) => {
    my.getPrivacySetting({
      success: (res) => resolve(res || { needAuthorization: true }),
      fail: () => resolve({ needAuthorization: true, checkFailed: true })
    })
  })
}

function ensurePrivacyConsent() {
  if (hasPrivacyAgreed()) return Promise.resolve(true)
  const app = getAppSafe()
  if (!app || !app.globalData) return Promise.resolve(false)
  return new Promise((resolve) => {
    app.globalData.privacyResolve = (payload) => {
      resolve(!!(payload && payload.event === 'agree'))
    }
    app.globalData.needPrivacyPopup = true
    clearPrivacySafetyTimer(app)
    app.globalData.privacySafetyTimer = setTimeout(() => {
      if (app.globalData.privacyResolve) finishPrivacyReject()
    }, PRIVACY_RESOLVE_TIMEOUT_MS)
    if (!tryShowPrivacyPopup()) showPrivacyPopup()
  })
}

function onNeedPrivacyAuthorization(handler) {
  if (my.onNeedPrivacyAuthorization) {
    my.onNeedPrivacyAuthorization(handler)
  }
}

function openPrivacyContract() {
  if (!my.openPrivacyContract) {
    my.showModal({
      title: '查看隐私政策',
      content: '当前支付宝版本未提供直接打开隐私政策的接口。请在支付宝小程序首页点击右上角“…”并查看“隐私保护指引”，阅读后再返回本页选择是否同意。',
      showCancel: false,
      confirmButtonText: '知道了'
    })
    return Promise.resolve(false)
  }
  return new Promise((resolve) => {
    my.openPrivacyContract({
      success: () => resolve(true),
      fail: () => resolve(false)
    })
  })
}

function tryShowPrivacyPopup() {
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
        app._privacyPopup = popup
        popup.show()
        return true
      }
    }
    if (page && page.$selectComponent) {
      const popup = page.$selectComponent('#privacy-popup')
      if (popup && popup.show) {
        app._privacyPopup = popup
        popup.show()
        return true
      }
    }
  } catch (_) {}
  return false
}

function showPrivacyPopup() {
  if (tryShowPrivacyPopup()) return true
  let attempts = 0
  const timer = setInterval(() => {
    attempts += 1
    if (tryShowPrivacyPopup() || attempts >= 8) {
      clearInterval(timer)
    }
  }, 80)
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

function finishPrivacyAgree(buttonId) {
  const app = getAppSafe()
  if (!app || !app.globalData) return
  const resolve = app.globalData.privacyResolve
  clearPrivacyPending(app)
  try {
    my.setStorageSync({ key: 'privacy_agreed_v1', data: Date.now() })
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
      console.warn('[privacy] resolve timeout, auto disagree')
      finishPrivacyReject()
    }, PRIVACY_RESOLVE_TIMEOUT_MS)
    notifyPagePrivacyNeed()
    if (!tryShowPrivacyPopup()) {
      showPrivacyPopup()
    }
  })
}

module.exports = {
  setupPrivacy,
  hasPrivacyAgreed,
  ensurePrivacyConsent,
  onNeedPrivacyAuthorization,
  openPrivacyContract,
  showPrivacyPopup,
  finishPrivacyAgree,
  finishPrivacyReject,
  bindAppPrivacyHandler
}
