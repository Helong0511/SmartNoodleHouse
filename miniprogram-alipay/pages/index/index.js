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
      my.setStorageSync({ key: 'table_token', data: query.token })
    }
    if (query.channel) app.globalData.channel = query.channel
    this.bootstrap()
  },

  async bootstrap() {
    try {
      this.setData({ loading: true })
      const token = app.globalData.tableToken || (my.getStorageSync({ key: 'table_token' }) || {}).data
      if (token) {
        const entry = await api.get('/miniprogram/entry', { token, channel: 'alipay' }, false)
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
      const showMemberPrompt = !!login.need_member_prompt
      this.setData({
        loading: false,
        nickname: login.nickname || '',
        showMemberPrompt,
        ready: true,
        enableFace: false
      })
      if (login.nickname) {
        my.setStorageSync({ key: 'nickname', data: login.nickname })
      }
      if (login.customer_no) {
        my.setStorageSync({ key: 'customer_no', data: login.customer_no })
      }
    } catch (e) {
      this.setData({ loading: false, ready: true })
      const msg = (e && e.message) || '登录失败'
      console.error('[bootstrap login]', e)
      my.alert({ title: '登录失败', content: msg })
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
      my.showToast({ content: '请先登录', type: 'fail' })
      return
    }
    try {
      my.showLoading({ content: '注册中' })
      const res = await api.post('/miniprogram/member/register', {
        nickname: this.data.nickname || '面馆会员',
        enable_face: this.data.enableFace
      })
      app.globalData.nickname = (res && res.nickname) || this.data.nickname
      if (res && res.customer_token) {
        app.globalData.customerToken = res.customer_token
        my.setStorageSync({ key: 'customer_token', data: res.customer_token })
      }
      if (res && res.customer_id) {
        app.globalData.customerId = res.customer_id
        my.setStorageSync({ key: 'customer_id', data: res.customer_id })
      }
      if (res && res.customer_no) {
        app.globalData.customerNo = res.customer_no
        my.setStorageSync({ key: 'customer_no', data: res.customer_no })
      }
      my.setStorageSync({ key: 'nickname', data: app.globalData.nickname })
      my.hideLoading()
      if (this.data.enableFace) {
        my.navigateTo({ url: '/pages/face/face?from=register' })
        return
      }
      this.goMenu()
    } catch (e) {
      my.hideLoading()
      my.showToast({ content: e.message || '注册失败', type: 'fail' })
    }
  },

  onSkip() { this.goMenu() },
  goMenu() { my.redirectTo({ url: '/pages/menu/menu' }) },
  goMember() { my.navigateTo({ url: '/pages/member/member' }) }
})
