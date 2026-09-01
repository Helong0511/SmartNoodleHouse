const privacy = require('../../utils/privacy')

Component({
  data: {
    visible: false
  },

  didMount() {
    const app = getApp()
    if (!app) return
    app._privacyPopup = this
    if (app.globalData && app.globalData.needPrivacyPopup) {
      this.show()
    }
  },

  didUnmount() {
    const app = getApp()
    if (app && app._privacyPopup === this) app._privacyPopup = null
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
            privacyTip: '已同意隐私政策。请点头像选头像，点昵称框获取支付宝昵称，并可授权手机号。'
          })
        }
      } catch (_) {}
      my.showToast({ content: '已同意', type: 'success' })
    },

    onReject() {
      privacy.finishPrivacyReject()
      my.showToast({ content: '需同意后才能获取头像昵称', type: 'none' })
    }
  }
})
