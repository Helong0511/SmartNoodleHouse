const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: {
    nickname: '',
    customerNo: '',
    faceOn: false,
    prefs: []
  },

  async onShow() {
    try {
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const face = await api.get('/miniprogram/customers/me/face-status')
      let prefs = []
      try {
        const cid = app.globalData.customerId
        const p = await api.get(`/customers/${cid}/preferences`)
        prefs = (p && p.preferences) || []
      } catch (_) {}
      this.setData({
        nickname: app.globalData.nickname || '',
        customerNo: app.globalData.customerNo || '',
        faceOn: !!(face && face.has_face),
        prefs
      })
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },

  onFaceSwitch(e) {
    const on = e.detail.value
    if (on) {
      wx.navigateTo({ url: '/pages/face/face' })
    } else {
      this.revokeFace()
    }
  },

  async revokeFace() {
    try {
      await api.post('/miniprogram/face/revoke', {})
      app.globalData.faceStatus = 'REVOKED'
      this.setData({ faceOn: false })
      wx.showToast({ title: '已关闭人脸迎宾', icon: 'success' })
    } catch (e) {
      this.setData({ faceOn: true })
      wx.showToast({ title: e.message || '操作失败', icon: 'none' })
    }
  },

  reorder() {
    wx.navigateTo({ url: '/pages/menu/menu' })
  }
})
