const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const { enableAlipayUserProfile } = require('../../config/env')

function appRef() {
  return auth.getAppSafe()
}

const PLACEHOLDER_NICKS = new Set(['联调员', '面馆会员', '微信用户', '支付宝用户'])

function isPlaceholderNickname(name) {
  const n = String(name || '').trim()
  return !n || PLACEHOLDER_NICKS.has(n)
}

function parseAlipayUserInfo(res) {
  if (!res) return { nickname: '', avatarUrl: '' }
  let raw = res.response != null ? res.response : res
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw)
    } catch (_) {
      return { nickname: '', avatarUrl: '' }
    }
  }
  const info = (raw && raw.response) || raw || {}
  const nickname = String(info.nickName || info.nick || info.userName || '').trim()
  const avatarUrl = String(info.avatar || info.avatarUrl || '').trim()
  return { nickname, avatarUrl }
}

Page({
  data: {
    loading: true,
    ready: false,
    hasTable: false,
    showMemberPrompt: false,
    saving: false,
    table: null,
    storeName: '',
    nickname: '',
    avatarUrl: '',
    phone: '',
    phoneInput: '',
    canUseAlipayProfile: !!enableAlipayUserProfile,
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购；完成注册可领取欢迎券并享受会员等级权益。'
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
              memberText: entry.prompts.member_consent_text
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
      const nickname = (login && login.nickname) || gd.nickname || ''
      const showMemberPrompt = !!(login && login.need_member_prompt) || isPlaceholderNickname(nickname)
      const avatarUrl = (login && login.avatar_url) || gd.avatarUrl || ''
      let phone = ''
      try {
        const me = await api.get('/miniprogram/customers/me').catch(() => null)
        if (me && me.phone) phone = me.phone
      } catch (_) {}
      this.setData({
        loading: false,
        nickname: isPlaceholderNickname(nickname) ? '' : nickname,
        avatarUrl,
        phone,
        showMemberPrompt,
        ready: true,
        table: ctx.table,
        storeName: ctx.storeName,
        hasTable: ctx.hasTable
      })
      if (login && login.nickname && !isPlaceholderNickname(login.nickname)) {
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

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  onPickLocalAvatar() {
    my.chooseImage({
      count: 1,
      sourceType: ['album', 'camera'],
      success: (res) => {
        const url = (res.apFilePaths && res.apFilePaths[0]) || (res.tempFilePaths && res.tempFilePaths[0]) || ''
        if (!url) {
          my.showToast({ content: '未选择图片', type: 'none' })
          return
        }
        this.applyProfile('', url)
        my.showToast({ content: '头像已选择', type: 'success' })
      }
    })
  },

  applyProfile(nickname, avatarUrl) {
    const app = appRef()
    const patch = {}
    if (nickname) {
      patch.nickname = nickname
      if (app) app.globalData.nickname = nickname
      my.setStorageSync({ key: 'nickname', data: nickname })
    }
    if (avatarUrl) {
      patch.avatarUrl = avatarUrl
      if (app) app.globalData.avatarUrl = avatarUrl
      my.setStorageSync({ key: 'avatar_url', data: avatarUrl })
    }
    if (Object.keys(patch).length) this.setData(patch)
  },

  fetchAlipayOpenUserInfo() {
    return new Promise((resolve, reject) => {
      if (!my.getOpenUserInfo) {
        reject(new Error('当前支付宝版本不支持获取昵称'))
        return
      }
      my.getOpenUserInfo({
        success: (res) => resolve(parseAlipayUserInfo(res)),
        fail: (err) => reject(err || new Error('获取支付宝资料失败'))
      })
    })
  },

  async onGetAlipayProfile() {
    try {
      const profile = await this.fetchAlipayOpenUserInfo()
      if (!profile.nickname && !profile.avatarUrl) {
        my.showToast({ content: '未获取到支付宝昵称', type: 'none' })
        return
      }
      this.applyProfile(profile.nickname, profile.avatarUrl)
      my.showToast({ content: profile.nickname ? `已填入 ${profile.nickname}` : '已获取头像', type: 'success' })
    } catch (e) {
      my.showToast({ content: auth.formatError(e, '获取失败，请手动输入'), type: 'none' })
    }
  },

  onGetAlipayProfileFail(e) {
    const msg = (e && (e.errorMessage || e.error)) || '未授权'
    my.showToast({ content: String(msg), type: 'none' })
  },

  async onSaveAndGo() {
    if (this.data.saving) return
    const app = appRef()
    if (!app) {
      my.showToast({ content: '应用未就绪', type: 'fail' })
      return
    }
    const nickname = (this.data.nickname || '').trim()
    if (nickname && (nickname.length < 2 || nickname.length > 20)) {
      my.showToast({ content: '昵称需 2-20 个字', type: 'fail' })
      return
    }
    const phoneInput = (this.data.phoneInput || '').trim()
    if (phoneInput && !/^1\d{10}$/.test(phoneInput)) {
      my.showToast({ content: '请输入11位手机号', type: 'fail' })
      return
    }
    try {
      await auth.ensureLogin()
    } catch (e) {
      my.showToast({ content: auth.formatError(e, '请先登录'), type: 'fail' })
      return
    }
    try {
      this.setData({ saving: true })
      my.showLoading({ content: '保存中' })
      const res = await api.post('/miniprogram/member/register', {
        nickname: nickname || '面馆会员',
        avatar_url: this.data.avatarUrl || null,
        phone: phoneInput || null
      })
      app.globalData.nickname = (res && res.nickname) || nickname || '面馆会员'
      app.globalData.avatarUrl = (res && res.avatar_url) || this.data.avatarUrl || ''
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
      if (res && res.is_member != null) app.globalData.isMember = res.is_member
      if (res && res.member_level_name) app.globalData.memberLevelName = res.member_level_name
      if (res && res.member_discount_rate) app.globalData.memberDiscountRate = res.member_discount_rate
      my.setStorageSync({ key: 'nickname', data: app.globalData.nickname })
      if (app.globalData.avatarUrl) {
        my.setStorageSync({ key: 'avatar_url', data: app.globalData.avatarUrl })
      }
      this.setData({
        nickname: app.globalData.nickname,
        phone: (res && res.phone) || this.data.phone || phoneInput || '',
        phoneInput: '',
        showMemberPrompt: false,
        saving: false
      })
      my.hideLoading()
      this.goMenu()
    } catch (e) {
      this.setData({ saving: false })
      my.hideLoading()
      my.showToast({ content: e.message || '保存失败', type: 'fail' })
    }
  },

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
