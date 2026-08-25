const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: { nickname: '', customerNo: '', faceOn: false, prefs: [] },
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
      my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
  },
  onFaceSwitch(e) {
    if (e.detail.value) my.navigateTo({ url: '/pages/face/face' })
    else this.revokeFace()
  },
  async revokeFace() {
    try {
      await api.post('/miniprogram/face/revoke', {})
      this.setData({ faceOn: false })
      my.showToast({ content: '已关闭人脸迎宾' })
    } catch (e) {
      this.setData({ faceOn: true })
      my.showToast({ content: e.message || '操作失败', type: 'fail' })
    }
  },
  reorder() { my.navigateTo({ url: '/pages/menu/menu' }) }
})
