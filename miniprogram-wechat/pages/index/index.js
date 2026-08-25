const api = require('../../utils/api')
const auth = require('../../utils/auth')
const app = getApp()

Page({
  data: {
    loading: true,
    ready: false,
    showMemberPrompt: false,
    table: null,
    nickname: '',
    enableFace: false,
    memberText: '注册会员后可查看历史订单、口味偏好，并支持一键复购。',
    faceText: '开启人脸迎宾后，到店可自动欢迎并推荐您常点口味。仅在您主动授权后生效，可随时关闭。'
  },

  onLoad(query) {
    if (query.token) {
      app.globalData.tableToken = query.token
      wx.setStorageSync('table_token', query.token)
    }
    if (query.channel) app.globalData.channel = query.channel
    this.bootstrap()
  },

  async bootstrap() {
    try {
      this.setData({ loading: true })
      const token = app.globalData.tableToken || wx.getStorageSync('table_token')
      if (token) {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'wechat' }, false)
        if (entry && entry.table) {
          app.globalData.table = entry.table
          this.setData({
            table: entry.table,
            memberText: entry.prompts.member_consent_text,
            faceText: entry.prompts.face_consent_text
          })
        }
      }
      const login = await auth.ensureLogin(token)
      const needPrompt = login.need_member_prompt || !(login.face_status && login.face_status.has_face)
      this.setData({
        loading: false,
        nickname: login.nickname || '',
        showMemberPrompt: !!login.need_member_prompt || needPrompt && !login.nickname,
        ready: true,
        enableFace: false
      })
      if (login.nickname && !(login.face_status && login.face_status.has_face)) {
        this.setData({ showMemberPrompt: true })
      }
      if (login.nickname && login.face_status && login.face_status.has_face) {
        this.setData({ showMemberPrompt: false })
      }
    } catch (e) {
      this.setData({ loading: false, ready: true })
      wx.showToast({ title: e.message || '登录失败', icon: 'none' })
    }
  },

  onNickname(e) {
    this.setData({ nickname: e.detail.value })
  },

  toggleFace() {
    this.setData({ enableFace: !this.data.enableFace })
  },

  async onRegister() {
    if (!app.globalData.customerToken) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    try {
      wx.showLoading({ title: '注册中' })
      const res = await api.post('/miniprogram/member/register', {
        nickname: this.data.nickname || '面馆会员',
        enable_face: this.data.enableFace
      })
      app.globalData.nickname = (res && res.nickname) || this.data.nickname
      wx.hideLoading()
      if (this.data.enableFace) {
        wx.navigateTo({ url: '/pages/face/face?from=register' })
        return
      }
      this.goMenu()
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: e.message || '注册失败', icon: 'none' })
    }
  },

  onSkip() {
    this.goMenu()
  },

  goMenu() {
    wx.redirectTo({ url: '/pages/menu/menu' })
  },

  goMember() {
    wx.navigateTo({ url: '/pages/member/member' })
  }
})
