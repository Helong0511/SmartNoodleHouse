/** 微信 getPhoneNumber 错误解析与隐私/额度提示 */

const privacy = require('./privacy')
const { getEnvDebugInfo } = require('../config/env')

function parseDetail(detail) {
  const d = detail || {}
  return {
    code: d.code || '',
    errMsg: String(d.errMsg || ''),
    errno: d.errno != null ? d.errno : d.errorCode
  }
}

function isPrivacyError(errMsg) {
  return /privacy|authorize|声明|agree|scope|declared/i.test(errMsg)
}

function isQuotaError(errMsg, errno) {
  return String(errno) === '1400001' || /1400001|quota|次数已达|上限/i.test(errMsg)
}

function markPrivacyNeed(page, scene) {
  if (!page || typeof page.setData !== 'function') return
  const patch = { needPrivacyAgree: true }
  if (typeof page.buildDebugInfo === 'function') {
    patch.debugInfo = getEnvDebugInfo().isRelease ? '' : page.buildDebugInfo(scene || '手机:隐私')
  }
  page.setData(patch)
}

function handleGetPhoneNumber(page, detail, onCode) {
  const { code, errMsg, errno } = parseDetail(detail)
  if (isPrivacyError(errMsg)) {
    markPrivacyNeed(page, '手机:隐私未声明')
    privacy.showPrivacyPopup()
    wx.showModal({
      title: '需先同意隐私指引',
      content: '请先在小程序内同意隐私指引，并在微信公众平台声明「手机号」能力；也可手动输入手机号。',
      showCancel: false
    })
    return
  }
  if (!code) {
    if (/deny|cancel/i.test(errMsg)) {
      wx.showToast({ title: '已取消手机号授权', icon: 'none' })
    } else if (isQuotaError(errMsg, errno)) {
      wx.showModal({
        title: '手机号验证额度不足',
        content: '微信手机号快速验证次数已用尽，请在公众平台「付费管理」购买资源包，或改用手动输入手机号。',
        showCancel: false
      })
    } else if (errMsg) {
      wx.showToast({ title: errMsg.replace(/^getPhoneNumber:fail\s*/i, ''), icon: 'none' })
    } else {
      wx.showModal({
        title: '手机号授权无响应',
        content: '请确认：①已同意隐私指引 ②公众平台已声明手机号 ③小程序已认证。也可手动输入手机号。',
        showCancel: false
      })
    }
    return
  }
  if (typeof onCode === 'function') onCode(code)
}

function confirmAccountMerge(message) {
  return new Promise((resolve) => {
    wx.showModal({
      title: '确认合并账号',
      content: message || '该手机号已关联另一账号。合并后将统一历史订单、积分和优惠券，是否继续？',
      confirmText: '确认合并',
      cancelText: '暂不合并',
      success: (res) => resolve(!!(res && res.confirm)),
      fail: () => resolve(false)
    })
  })
}

module.exports = {
  parseDetail,
  isPrivacyError,
  isQuotaError,
  handleGetPhoneNumber,
  confirmAccountMerge
}
