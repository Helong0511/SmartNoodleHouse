/** 支付宝手机号授权辅助 */

const privacy = require('./privacy')

function handleGetPhoneAuthorize(page, err, onEncrypted) {
  if (err) {
    const msg = String((err && (err.errorMessage || err.error)) || '未授权')
    if (/privacy|授权|协议/i.test(msg)) {
      if (page && typeof page.setData === 'function') {
        page.setData({ needPrivacyAgree: true })
      }
      privacy.showPrivacyPopup()
      my.showModal({
        title: '需先同意隐私政策',
        content: '请先在小程序内同意隐私政策，并在开放平台配置手机号权限；也可手动输入手机号。',
        confirmText: '知道了'
      })
    } else {
      my.showToast({ content: msg, type: 'none' })
    }
    return
  }
  if (!my.getPhoneNumber) {
    my.showToast({ content: '当前支付宝版本不支持获取手机号', type: 'none' })
    return
  }
  my.getPhoneNumber({
    success: (res) => {
      const encrypted = (res && res.response) || ''
      if (!encrypted) {
        my.showToast({ content: '未获取到手机号密文，请手输', type: 'none' })
        return
      }
      if (typeof onEncrypted === 'function') onEncrypted(encrypted)
    },
    fail: (e) => {
      const msg = String((e && (e.errorMessage || e.error)) || '获取手机号失败')
      my.showToast({ content: msg, type: 'none' })
    }
  })
}

module.exports = {
  handleGetPhoneAuthorize
}
