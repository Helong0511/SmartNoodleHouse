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
    avatarUrl: '',
    enableFace: false,
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购与到店迎宾推荐。',
    faceText: '开启人脸迎宾后，到店可自动欢迎，并按您常点口味智能推荐。仅摄像头实时采集，可随时关闭。'
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
      // 微信登录已创建/更新客户档案；仅无昵称时引导完善资料
      const showMemberPrompt = !!login.need_member_prompt
      this.setData({
        loading: false,
        nickname: login.nickname || '',
        avatarUrl: login.avatar_url || app.globalData.avatarUrl || '',
        showMemberPrompt,
        ready: true,
        enableFace: false
      })
      if (login.nickname) wx.setStorageSync('nickname', login.nickname)
      if (login.avatar_url) {
        app.globalData.avatarUrl = login.avatar_url
        wx.setStorageSync('avatar_url', login.avatar_url)
      }
      if (login.customer_no) wx.setStorageSync('customer_no', login.customer_no)
    } catch (e) {
      this.setData({ loading: false, ready: true })
      const msg = (e && e.message) || '登录失败'
      console.error('[bootstrap login]', e)
      wx.showModal({ title: '登录失败', content: msg, showCancel: false })
    }
  },

  onChooseAvatar(e) {
    const url = (e.detail && e.detail.avatarUrl) || ''
    if (!url) return
    this.setData({ avatarUrl: url })
    app.globalData.avatarUrl = url
    wx.setStorageSync('avatar_url', url)
  },

  onNickname(e) {
    const nickname = (e.detail && e.detail.value) || ''
    this.setData({ nickname })
    app.globalData.nickname = nickname
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
      wx.showLoading({ title: '保存中' })
      const res = await api.post('/miniprogram/member/register', {
        nickname: this.data.nickname || '面馆会员',
        avatar_url: this.data.avatarUrl || null,
        enable_face: this.data.enableFace
      })
      app.globalData.nickname = (res && res.nickname) || this.data.nickname
      app.globalData.avatarUrl = (res && res.avatar_url) || this.data.avatarUrl || ''
      if (res && res.customer_token) {
        app.globalData.customerToken = res.customer_token
        wx.setStorageSync('customer_token', res.customer_token)
      }
      if (res && res.customer_id) {
        app.globalData.customerId = res.customer_id
        wx.setStorageSync('customer_id', res.customer_id)
      }
      if (res && res.customer_no) {
        app.globalData.customerNo = res.customer_no
        wx.setStorageSync('customer_no', res.customer_no)
      }
      wx.setStorageSync('nickname', app.globalData.nickname)
      if (app.globalData.avatarUrl) wx.setStorageSync('avatar_url', app.globalData.avatarUrl)
      wx.hideLoading()
      if (this.data.enableFace || (res && res.need_face_consent)) {
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
