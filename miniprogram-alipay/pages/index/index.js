const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')

function appRef() {
  return auth.getAppSafe()
}

Page({
  data: {
    loading: true,
    ready: false,
    hasTable: false,
    showMemberPrompt: false,
    table: null,
    storeName: '',
    nickname: '',
    enableFace: false,
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购与到店迎宾推荐。',
    faceText: '开启人脸迎宾后，到店可自动欢迎，并按您常点口味智能推荐。仅摄像头实时采集，可随时关闭。'
  },

  onLoad(query) {
    const app = appRef()
    if (query.token && app) {
      app.globalData.tableToken = query.token
      my.setStorageSync({ key: 'table_token', data: query.token })
    }
    if (query.channel && app) app.globalData.channel = query.channel
    this.bootstrap()
  },

  async bootstrap() {
    try {
      this.setData({ loading: true })
      const app = appRef()
      const gd = (app && app.globalData) || {}
      const token = gd.tableToken || auth.readStorage('table_token') || ''
      if (token) {
        try {
          await dineCtx.bindTableFromToken(token)
        } catch (_) {}
      }
      const ctx = dineCtx.currentDisplay()
      if (ctx.table) {
        try {
          const entry = await api.get('/miniprogram/entry', { token: ctx.table.qr_token || token, channel: 'alipay' }, false)
          if (entry && entry.prompts) {
            this.setData({
              memberText: entry.prompts.member_consent_text,
              faceText: entry.prompts.face_consent_text
            })
          }
        } catch (_) {}
      }
      let login = null
      try {
        login = await auth.ensureLogin(token)
      } catch (loginErr) {
        console.error('[bootstrap login]', loginErr)
        const loginMsg = auth.formatError(loginErr, '登录失败')
        my.showToast({
          content: loginMsg.length > 28 ? `${loginMsg.slice(0, 28)}…` : loginMsg,
          type: 'none',
          duration: 3500
        })
      }
      const showMemberPrompt = !!(login && login.need_member_prompt)
      this.setData({
        loading: false,
        nickname: (login && login.nickname) || '',
        showMemberPrompt,
        ready: true,
        enableFace: false,
        table: ctx.table,
        storeName: ctx.storeName,
        hasTable: ctx.hasTable
      })
      if (login && login.nickname) {
        my.setStorageSync({ key: 'nickname', data: login.nickname })
      }
      if (login && login.customer_id && app) {
        app.globalData.customerId = login.customer_id
        my.setStorageSync({ key: 'customer_id', data: login.customer_id })
      }
      if (login && login.customer_no) {
        my.setStorageSync({ key: 'customer_no', data: login.customer_no })
      }
    } catch (e) {
      this.setData({ loading: false, ready: true, hasTable: false })
      const msg = auth.formatError(e, '初始化失败')
      console.error('[bootstrap]', e)
      my.alert({ title: '初始化失败', content: msg })
    }
  },

  scanTable() {
    dineCtx.scanAndBindTable()
      .then((ctx) => {
        if (ctx) {
          this.setData({
            table: ctx.table,
            storeName: ctx.storeName,
            hasTable: ctx.hasTable
          })
        }
      })
      .catch((e) => {
        my.showToast({ content: auth.formatError(e, '扫码失败'), type: 'none' })
      })
  },

  onNickname(e) {
    this.setData({ nickname: e.detail.value })
  },

  toggleFace() {
    this.setData({ enableFace: !this.data.enableFace })
  },

  async onRegister() {
    const app = appRef()
    if (!app || !app.globalData.customerToken) {
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
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      } else if (res && res.customer_id) {
        auth.refreshCustomerToken(app, app.globalData.customerToken, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      my.setStorageSync({ key: 'nickname', data: app.globalData.nickname })
      my.hideLoading()
      if (this.data.enableFace || (res && res.need_face_consent)) {
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
  goMenu() {
    my.switchTab({
      url: '/pages/menu/menu',
      fail: (err) => {
        my.showToast({ content: auth.formatError(err, '无法打开点餐页'), type: 'none' })
      }
    })
  },

  goMember() {
    my.switchTab({
      url: '/pages/member/member',
      fail: (err) => {
        my.showToast({ content: auth.formatError(err, '无法打开会员页'), type: 'none' })
      }
    })
  }
})
