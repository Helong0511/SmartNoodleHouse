const api = require('../../utils/api')
const auth = require('../../utils/auth')
const cameraAuth = require('../../utils/cameraAuth')
const app = getApp()

Page({
  data: { cameraOn: false, from: '', uploading: false, openingCamera: false },

  onLoad(query) {
    this.setData({ from: query.from || '' })
  },

  async startCamera() {
    if (this.data.openingCamera || this.data.cameraOn) return
    this.setData({ openingCamera: true })
    try {
      await cameraAuth.ensureCameraReady()
      this.setData({ cameraOn: true })
    } catch (e) {
      wx.showModal({
        title: '无法打开摄像头',
        content: (e && e.message) || cameraAuth.CAMERA_DENIED_HINT,
        showCancel: false
      })
    } finally {
      this.setData({ openingCamera: false })
    }
  },

  async onCameraError(e) {
    const msg = (e && e.detail && e.detail.errMsg) || '摄像头不可用'
    this.setData({ cameraOn: false })
    const ok = await cameraAuth.handleCameraError(msg)
    if (ok) this.setData({ cameraOn: true })
  },

  /** 仅从 live camera 取帧，不走相册 / chooseMedia 读图文件 */
  captureAndConsent() {
    if (!this.data.cameraOn) {
      wx.showToast({ title: '请先打开摄像头', icon: 'none' })
      return
    }
    if (this.data.uploading) return
    const ctx = wx.createCameraContext('faceCamera', this)
    ctx.takePhoto({
      quality: 'high',
      success: (res) => {
        const path = res.tempImagePath
        wx.getFileSystemManager().readFile({
          filePath: path,
          encoding: 'base64',
          success: async (file) => {
            await this.uploadFace(file.data)
          },
          fail: () => wx.showToast({ title: '读取摄像头帧失败', icon: 'none' })
        })
      },
      fail: () => wx.showToast({ title: '拍照失败，请检查摄像头权限', icon: 'none' })
    })
  },

  async uploadFace(base64) {
    try {
      await auth.ensureLogin()
    } catch (e) {
      this.setData({ uploading: false, capturing: false })
      wx.showToast({ title: auth.formatError(e, '请先登录'), icon: 'none' })
      return
    }
    this.setData({ uploading: true })
    try {
      wx.showLoading({ title: '校验人脸中' })
      await api.post('/miniprogram/face/consent-base64', { image_base64: base64 })
      app.globalData.faceStatus = 'AUTHORIZED'
      wx.hideLoading()
      wx.showToast({ title: '授权成功', icon: 'success' })
      setTimeout(() => {
        if (this.data.from === 'register') {
          wx.switchTab({ url: '/pages/menu/menu' })
        } else {
          wx.navigateBack()
        }
      }, 600)
    } catch (e) {
      wx.hideLoading()
      const msg = (e && e.message) || '授权失败'
      // toast 字数太短，完整错误用 modal
      wx.showModal({
        title: '人脸授权失败',
        content: msg,
        showCancel: false
      })
    } finally {
      this.setData({ uploading: false })
    }
  },

  cancel() {
    if (this.data.from === 'register') {
      wx.switchTab({ url: '/pages/menu/menu' })
    } else {
      wx.navigateBack()
    }
  }
})
