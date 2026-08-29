const api = require('../../utils/api')
const app = getApp()

Page({
  data: { cameraOn: false, from: '', capturing: false },

  onLoad(query) {
    this.setData({ from: query.from || '' })
  },

  startCamera() {
    this.setData({ cameraOn: true })
  },

  onCameraError() {
    my.showToast({ content: '摄像头不可用，请检查权限', type: 'fail' })
    this.setData({ cameraOn: false })
  },

  /** 仅用页面内 camera 实拍，禁止相册选图 */
  confirmConsent() {
    if (!this.data.cameraOn) {
      my.showToast({ content: '请先打开摄像头', type: 'fail' })
      return
    }
    if (this.data.capturing) return
    this.setData({ capturing: true })
    const ctx = my.createCameraContext('faceCamera')
    ctx.takePhoto({
      quality: 'high',
      success: (res) => {
        const path = res.tempImagePath
        my.getFileSystemManager().readFile({
          filePath: path,
          encoding: 'base64',
          success: async (file) => {
            await this.uploadFace(file.data)
          },
          fail: () => {
            this.setData({ capturing: false })
            my.showToast({ content: '读取照片失败', type: 'fail' })
          }
        })
      },
      fail: () => {
        this.setData({ capturing: false })
        my.showToast({ content: '拍照失败，请正对摄像头重试', type: 'fail' })
      }
    })
  },

  async uploadFace(base64) {
    if (!app.globalData.customerToken) {
      this.setData({ capturing: false })
      my.showToast({ content: '请先登录', type: 'fail' })
      return
    }
    try {
      my.showLoading({ content: '人脸校验中' })
      await api.post('/miniprogram/face/consent-base64', { image_base64: base64 })
      app.globalData.faceStatus = 'AUTHORIZED'
      my.hideLoading()
      my.showToast({ content: '授权成功' })
      setTimeout(() => {
        if (this.data.from === 'register') my.switchTab({ url: '/pages/menu/menu' })
        else my.navigateBack()
      }, 500)
    } catch (e) {
      my.hideLoading()
      this.setData({ capturing: false })
      const msg = (e && e.message) || '授权失败'
      my.alert({ title: '人脸授权失败', content: msg })
    }
  },

  cancel() {
    if (this.data.from === 'register') my.switchTab({ url: '/pages/menu/menu' })
    else my.navigateBack()
  }
})
