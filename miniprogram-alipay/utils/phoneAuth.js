/** 支付宝手机号授权：须由 button open-type=getAuthorize 用户点击触发，不可静默调用 */

const privacy = require('./privacy')

function normalizePhoneEncrypted(res) {
  const response = res && res.response
  if (response == null || response === '') return ''
  if (typeof response === 'object') {
    try {
      return JSON.stringify(response)
    } catch (_) {
      return ''
    }
  }
  return String(response)
}

function resolveAuthScope(e) {
  if (!e) return ''
  return String(
    e.authScope
    || (e.detail && e.detail.authScope)
    || ''
  ).trim()
}

/**
 * button onGetAuthorize 回调：仅在用户主动授权 phoneNumber 后调用 my.getPhoneNumber
 * @param {object} page 页面实例
 * @param {object} e 授权成功事件
 * @param {(encrypted: string) => void} onEncrypted 拿到密文后的回调
 */
function handleGetAuthorizeSuccess(page, e, onEncrypted) {
  const scope = resolveAuthScope(e)
  // 官方：仅当 authScope === phoneNumber 时再取号；部分基础库不回传 scope，默认视为手机号授权按钮触发
  if (scope && scope !== 'phoneNumber') return
  return privacy.ensurePrivacyConsent().then((agreed) => {
    if (!agreed) {
      my.showToast({ content: '请先阅读并同意隐私政策', type: 'none' })
      return
    }
    if (!my.getPhoneNumber) {
      my.showToast({ content: '当前支付宝版本不支持获取手机号', type: 'none' })
      return
    }
    my.getPhoneNumber({
      success: (res) => {
        const encrypted = normalizePhoneEncrypted(res)
        if (!encrypted) {
          my.showToast({ content: '未获取到手机号密文，请稍后重试', type: 'none' })
          return
        }
        if (typeof onEncrypted === 'function') onEncrypted(encrypted)
      },
      fail: (err) => {
        const { localizeNativeErrMsg } = require('./errMsg')
        const raw = String((err && (err.errorMessage || err.error)) || '获取手机号失败')
        my.showToast({ content: localizeNativeErrMsg(raw) || raw, type: 'none' })
      }
    })
  })
}

/** button onAuthError：用户拒绝授权 */
function handleAuthError(page, err) {
  const msg = String((err && (err.errorMessage || err.error || err.message)) || '用户拒绝授权')
  if (/privacy|隐私|协议/i.test(msg)) {
    if (page && typeof page.setData === 'function') {
      page.setData({ needPrivacyAgree: true })
    }
    privacy.showPrivacyPopup()
    my.alert({
      title: '需先同意隐私政策',
      content: '请先在小程序内同意隐私政策，并在开放平台开通「获取会员手机号」能力（手机号仅支持官方一键授权）。',
      buttonText: '知道了'
    })
    return
  }
  const { localizeNativeErrMsg } = require('./errMsg')
  my.showToast({
    content: /拒绝|cancel|deny/i.test(msg) ? '已取消手机号授权' : (localizeNativeErrMsg(msg) || msg),
    type: 'none'
  })
}

/** @deprecated 请用 handleGetAuthorizeSuccess / handleAuthError */
function handleGetPhoneAuthorize(page, err, onEncrypted) {
  if (err) {
    handleAuthError(page, err)
    return
  }
  handleGetAuthorizeSuccess(page, { authScope: 'phoneNumber' }, onEncrypted)
}

function confirmAccountMerge(message) {
  return new Promise((resolve) => {
    my.confirm({
      title: '确认合并账号',
      content: message || '该手机号已关联另一账号。合并后将统一历史订单、积分和优惠券，是否继续？',
      confirmButtonText: '确认合并',
      cancelButtonText: '暂不合并',
      success: (res) => resolve(!!(res && res.confirm)),
      fail: () => resolve(false)
    })
  })
}

module.exports = {
  handleGetAuthorizeSuccess,
  handleAuthError,
  handleGetPhoneAuthorize,
  normalizePhoneEncrypted,
  confirmAccountMerge
}
