const api = require('../../utils/api')
const app = getApp()

Page({
  data: { cameraOn: false, from: '' },
  onLoad(query) { this.setData({ from: query.from || '' }) },
  startCamera() { this.setData({ cameraOn: true }) },
  chooseAndUpload() {
    my.chooseImage({
      count: 1,
      sourceType: ['camera', 'album'],
      success: (res) => {
        const path = res.apFilePaths[0]
        my.getFileSystemManager().readFile({
          filePath: path,
          encoding: 'base64',
          success: async (file) => {
            await this.uploadFace(file.data)
          },
          fail: () => my.showToast({ content: '读取图片失败', type: 'fail' })
        })
      }
    })
  },
  async uploadFace(base64) {
    if (!app.globalData.customerToken) {
      my.showToast({ content: '请先登录', type: 'fail' })
      return
    }
    try {
      my.showLoading({ content: '授权中' })
      await api.post('/miniprogram/face/consent-base64', { image_base64: base64 })
      app.globalData.faceStatus = 'AUTHORIZED'
      my.hideLoading()
      my.showToast({ content: '授权成功' })
      setTimeout(() => {
        if (this.data.from === 'register') my.redirectTo({ url: '/pages/menu/menu' })
        else my.navigateBack()
      }, 500)
    } catch (e) {
      my.hideLoading()
      my.showToast({ content: e.message || '授权失败', type: 'fail' })
    }
  },
  confirmConsent() { this.chooseAndUpload() },
  cancel() {
    if (this.data.from === 'register') my.redirectTo({ url: '/pages/menu/menu' })
    else my.navigateBack()
  }
})
