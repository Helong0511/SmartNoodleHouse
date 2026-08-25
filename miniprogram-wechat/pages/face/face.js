const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: { cameraOn: false, from: '' },

  onLoad(query) {
    this.setData({ from: query.from || '' })
  },

  startCamera() {
    this.setData({ cameraOn: true })
  },

  chooseAndUpload() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera', 'album'],
      success: (res) => {
        const path = res.tempFiles[0].tempFilePath
        wx.getFileSystemManager().readFile({
          filePath: path,
          encoding: 'base64',
          success: async (file) => {
            await this.uploadFace(file.data)
          },
          fail: () => wx.showToast({ title: '读取图片失败', icon: 'none' })
        })
      }
    })
  },

  async uploadFace(base64) {
    if (!app.globalData.customerToken) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    try {
      wx.showLoading({ title: '授权中' })
      await api.post('/miniprogram/face/consent-base64', { image_base64: base64 })
      app.globalData.faceStatus = 'AUTHORIZED'
      wx.hideLoading()
      wx.showToast({ title: '授权成功', icon: 'success' })
      setTimeout(() => {
        if (this.data.from === 'register') {
          wx.redirectTo({ url: '/pages/menu/menu' })
        } else {
          wx.navigateBack()
        }
      }, 600)
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: e.message || '授权失败', icon: 'none' })
    }
  },

  confirmConsent() {
    this.chooseAndUpload()
  },

  cancel() {
    if (this.data.from === 'register') {
      wx.redirectTo({ url: '/pages/menu/menu' })
    } else {
      wx.navigateBack()
    }
  }
})
