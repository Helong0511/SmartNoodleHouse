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
      const app = getApp()
      const resolve = app && app.globalData && app.globalData.privacyResolve
      this.hide()
      if (app && app.globalData) {
        app.globalData.needPrivacyPopup = false
        app.globalData.privacyResolve = null
      }
      if (typeof resolve === 'function') {
        resolve({ buttonId: 'agree-btn', event: 'agree' })
      }
    },

    onReject() {
      const app = getApp()
      const resolve = app && app.globalData && app.globalData.privacyResolve
      this.hide()
      if (app && app.globalData) {
        app.globalData.needPrivacyPopup = false
        app.globalData.privacyResolve = null
      }
      if (typeof resolve === 'function') {
        resolve({ event: 'disagree' })
      }
      wx.showToast({ title: '需同意后才能获取头像昵称', icon: 'none' })
    }
  }
})
