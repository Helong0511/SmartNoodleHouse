const privacy = require('../../utils/privacy')

Component({
  data: {
    visible: false
  },

  lifetimes: {
    attached() {
      const app = getApp()
      if (!app) return
      app._privacyPopup = this
      if (app.globalData && app.globalData.needPrivacyPopup) {
        this.show()
      }
    },
    detached() {
      const app = getApp()
      if (app && app._privacyPopup === this) app._privacyPopup = null
    }
  },

  methods: {
    noop() {},

    show() {
      this.setData({ visible: true })
    },

    hide() {
      this.setData({ visible: false })
    },

    openContract() {
      privacy.openPrivacyContract()
    },

    onAgree() {
      this.hide()
      privacy.finishPrivacyAgree('agree-btn')
      try {
        const pages = getCurrentPages()
        const page = pages && pages[pages.length - 1]
        if (page && typeof page.setData === 'function') {
          page.setData({
            needPrivacyAgree: false,
            privacyTip: '已同意隐私指引。请点头像选微信头像，点输入框取微信昵称，并可授权手机号。'
          })
        }
      } catch (_) {}
      wx.showToast({ title: '已同意', icon: 'success' })
    },

    onReject() {
      privacy.finishPrivacyReject()
      wx.showToast({ title: '需同意后才能获取头像昵称', icon: 'none' })
    }
  }
})
