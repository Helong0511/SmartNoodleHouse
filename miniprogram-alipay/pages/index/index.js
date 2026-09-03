const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const privacy = require('../../utils/privacy')
const phoneAuth = require('../../utils/phoneAuth')
const clientConfig = require('../../utils/clientConfig')
const {
  resolveApiBase,
  getEnvDebugInfo,
  shouldShowDevTips,
  appendDevApiHint
} = require('../../config/env')

function appRef() {
  return auth.getAppSafe()
}

Page({
  data: {
    loading: true,
    ready: false,
    hasTable: false,
    showMemberPrompt: false,
    saving: false,
    needPrivacyAgree: false,
    table: null,
    storeName: '',
    nickname: '',
    avatarUrl: '',
    phone: '',
    phoneInput: '',
    canChooseAvatar: false,
    useAlipayNickname: false,
    canAlipayPhone: false,
    privacyTip: '',
    showDevTips: false,
    debugInfo: '',
    nicknameReviewFail: false,
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购；完成注册可领取欢迎券并享受会员等级权益。'
  },

  buildDebugInfo(extra) {
    const env = getEnvDebugInfo()
    const p = clientConfig.alipayPrivacyPageData()
    const parts = [
      `环境:${env.envVersion}`,
      `头像:${p.canChooseAvatar ? '开' : '关'}`,
      `昵称:${p.useAlipayNickname ? '开' : '关'}`,
      `手机:${p.canAlipayPhone ? '开' : '关'}`,
      `隐私待同意:${this.data.needPrivacyAgree ? '是' : '否'}`
    ]
    if (extra) parts.push(extra)
    return `${parts.join(' | ')} | ${env.apiBase}`
  },

  onLoad(query) {
    const env = getEnvDebugInfo()
    this.setData({
      ...clientConfig.alipayPrivacyPageData(),
      showDevTips: shouldShowDevTips(),
      debugInfo: env.isRelease ? '' : this.buildDebugInfo()
    })
    clientConfig.loadClientConfig().then(() => {
      this.setData(clientConfig.alipayPrivacyPageData())
    })
    const app = appRef()
    if (query.token && app) {
      app.globalData.tableToken = query.token
      my.setStorageSync({ key: 'table_token', data: query.token })
    }
    if (query.channel && app) app.globalData.channel = query.channel
    this.bootstrap()
  },

  onShow() {
    const app = appRef()
    if (app && app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
  },

  async refreshPrivacyState() {
    const setting = await privacy.setupPrivacy()
    const need = !!setting.needAuthorization
    const apiHint = (appRef() && appRef().globalData.apiBase) || resolveApiBase()
    const baseTip = need
      ? '请先点击上方「同意隐私政策」，再获取头像/昵称/手机号；也可手输昵称与手机号，或从相册选头像。'
      : '点头像选支付宝头像，点昵称框后在键盘上方选支付宝昵称。'
    this.setData({
      needPrivacyAgree: need,
      privacyTip: appendDevApiHint(baseTip, apiHint),
      debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo()
    })
    return !need
  },

  openPrivacyContract() {
    privacy.openPrivacyContract()
  },

  onPrivacyAgree() {
    privacy.finishPrivacyAgree('agree-btn')
    this.setData({
      needPrivacyAgree: false,
      privacyTip: appendDevApiHint(
        '已同意隐私政策。请点头像选头像，点昵称框获取支付宝昵称。',
        (appRef() && appRef().globalData.apiBase) || resolveApiBase()
      ),
      debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('隐私已同意')
    })
    my.showToast({ content: '已同意，请继续操作', type: 'success' })
  },

  onPrivacyAgreeForPhone() {
    privacy.finishPrivacyAgree('agree-phone-btn')
    this.setData({
      needPrivacyAgree: false,
      privacyTip: appendDevApiHint(
        '已同意隐私政策，请再次点击「支付宝授权手机号」。',
        (appRef() && appRef().globalData.apiBase) || resolveApiBase()
      ),
      debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('隐私已同意(手机号)')
    })
    my.showToast({ content: '已同意，请再点授权手机号', type: 'success' })
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
            this.setData({ memberText: entry.prompts.member_consent_text })
          }
        } catch (_) {}
      }
      let login = null
      try {
        login = await auth.ensureLogin(token)
      } catch (loginErr) {
        console.error('[bootstrap login]', loginErr)
        my.showToast({
          content: auth.formatError(loginErr, '登录失败'),
          type: 'none',
          duration: 3500
        })
      }
      const nickname = (login && login.nickname) || gd.nickname || ''
      const showMemberPrompt = !!(login && login.need_member_prompt) || auth.isPlaceholderNickname(nickname)
      const avatarUrl = (login && login.avatar_url) || gd.avatarUrl || ''
      this.setData({
        loading: false,
        nickname: auth.isPlaceholderNickname(nickname) ? '' : nickname,
        avatarUrl,
        phone: '',
        showMemberPrompt,
        ready: true,
        table: ctx.table,
        storeName: ctx.storeName,
        hasTable: ctx.hasTable
      })
      api.get('/miniprogram/customers/me')
        .then((me) => {
          if (me && me.phone) this.setData({ phone: me.phone })
        })
        .catch(() => {})
      if (login && login.nickname && !auth.isPlaceholderNickname(login.nickname)) {
        my.setStorageSync({ key: 'nickname', data: login.nickname })
      }
      if (login && login.customer_id && app) {
        app.globalData.customerId = login.customer_id
        my.setStorageSync({ key: 'customer_id', data: login.customer_id })
      }
      if (login && login.customer_no) {
        my.setStorageSync({ key: 'customer_no', data: login.customer_no })
      }
      await this.refreshPrivacyState()
      if (login && auth.isProfileComplete(login)) {
        this.goMenu()
        return
      }
    } catch (e) {
      this.setData({ loading: false, ready: true, hasTable: false })
      my.alert({ title: '初始化失败', content: auth.formatError(e, '初始化失败') })
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

  onChooseAvatar(e) {
    const detail = (e && e.detail) || {}
    const url = detail.avatarUrl || detail.avatar || ''
    const errMsg = String(detail.errMsg || detail.errorMessage || '')
    if (!url) {
      if (/privacy|authorize|协议|声明|agree|scope/i.test(errMsg)) {
        this.setData({
          needPrivacyAgree: true,
          debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('头像:隐私')
        })
        privacy.showPrivacyPopup()
        my.alert({
          title: '需配置隐私政策',
          content: '请在开放平台配置小程序隐私政策并声明头像能力；临时可用相册选图+手输昵称。'
        })
        return
      }
      if (/cancel/i.test(errMsg)) return
      my.showToast({ content: errMsg || '未选头像', type: 'none' })
      return
    }
    this.applyProfile('', url)
    my.showToast({ content: '头像已选择', type: 'success' })
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

  onNicknameInput(e) {
    const nickname = (e.detail && e.detail.value) || ''
    this.setData({ nickname })
    const app = appRef()
    if (app) app.globalData.nickname = nickname
  },

  onNicknameBlur(e) {
    const nickname = (e.detail && e.detail.value) || ''
    if (!nickname) return
    this.setData({ nickname })
    const app = appRef()
    if (app) app.globalData.nickname = nickname
  },

  onNicknameReview(e) {
    const detail = (e.detail) || {}
    if (detail.pass === false) {
      this.setData({ nicknameReviewFail: true, nickname: '' })
      my.showToast({ content: '昵称未通过审核，请修改', type: 'none' })
    } else if (detail.pass === true) {
      this.setData({ nicknameReviewFail: false })
    }
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  onGetPhoneAuthorize(e) {
    phoneAuth.handleGetAuthorizeSuccess(this, e || {}, async (encrypted) => {
      try {
        my.showLoading({ content: '绑定中' })
        await auth.ensureLogin()
        const res = await api.post('/miniprogram/member/phone', { alipay_phone_encrypted: encrypted })
        const app = appRef()
        if (res && res.customer_token && app) {
          auth.refreshCustomerToken(app, res.customer_token, {
            customer_id: res.customer_id,
            customer_no: res.customer_no
          })
        }
        my.hideLoading()
        this.setData({ phone: res.phone || '', phoneInput: '' })
        my.showToast({ content: '手机号已绑定', type: 'success' })
      } catch (err) {
        my.hideLoading()
        my.showToast({ content: (err && err.message) || '绑定失败，请手输', type: 'none' })
      }
    })
  },

  onGetPhoneAuthorizeFail(e) {
    phoneAuth.handleAuthError(this, e || { errorMessage: '用户拒绝授权' })
  },

  onFormSubmit(e) {
    if (this.data.saving) return
    const formVal = (e.detail && e.detail.value) || {}
    const formNick = String(formVal.nickname || '').trim()
    const nickname = formNick || String(this.data.nickname || '').trim()
    this.doSaveAndGo(nickname)
  },

  async doSaveAndGo(nickname) {
    const app = appRef()
    if (!app) {
      my.showToast({ content: '应用未就绪', type: 'fail' })
      return
    }
    if (this.data.useAlipayNickname && this.data.nicknameReviewFail) {
      my.showToast({ content: '昵称未通过审核，请修改', type: 'fail' })
      return
    }
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
      const res = await auth.registerMember({
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
      if (res && res.welcome_coupon_issued) {
        const cname = (res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'
        my.showToast({ content: `已赠送「${cname}」`, type: 'success' })
      }
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
