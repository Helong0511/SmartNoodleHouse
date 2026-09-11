const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const privacy = require('../../utils/privacy')
const {
  resolveApiBase,
  getEnvDebugInfo,
  shouldShowDevTips,
  appendDevApiHint
} = require('../../config/env')
const app = getApp()
const phoneAuth = require('../../utils/phoneAuth')
const clientConfig = require('../../utils/clientConfig')
const avatarUtil = require('../../utils/avatar')
const { normalizeLaunchQuery } = require('../../utils/scan')

Page({
  data: {
    loading: true,
    ready: false,
    hasTable: false,
    showMemberPrompt: false,
    saving: false,
    needPrivacyAgree: false,
    table: null,
    store: null,
    storeName: '',
    storeAddress: '',
    tableName: '',
    seatLabel: '桌号',
    nickname: '',
    avatarUrl: '',
    phone: '',
    phoneInput: '',
    canChooseAvatar: false,
    useWxNickname: false,
    canWxPhone: false,
    privacyTip: '',
    showDevTips: false,
    debugInfo: '',
    nicknameReviewFail: false,
    memberText: '开启智能会员后系统会记住您的口味爱好，支持一键复购；可领取欢迎券并享受会员等级权益。不影响先点餐。'
  },

  buildDebugInfo(extra) {
    const env = getEnvDebugInfo()
    const p = clientConfig.wxPrivacyPageData()
    const parts = [
      `环境:${env.envVersion}`,
      `头像:${p.canChooseAvatar ? '开' : '关'}`,
      `昵称:${p.useWxNickname ? '开' : '关'}`,
      `手机:${p.canWxPhone ? '开' : '关'}`,
      `隐私待同意:${this.data.needPrivacyAgree ? '是' : '否'}`
    ]
    if (extra) parts.push(extra)
    return `${parts.join(' | ')} | ${env.apiBase}`
  },

  onLoad(query) {
    const env = getEnvDebugInfo()
    this.setData({
      ...clientConfig.wxPrivacyPageData(),
      showDevTips: shouldShowDevTips(),
      debugInfo: env.isRelease ? '' : this.buildDebugInfo()
    })
    clientConfig.loadClientConfig().then(() => {
      this.setData(clientConfig.wxPrivacyPageData())
    })
    const q = normalizeLaunchQuery(query)
    if (q.token) {
      app.globalData.tableToken = q.token
      wx.setStorageSync('table_token', q.token)
    }
    if (q.channel) app.globalData.channel = q.channel
    try {
      if (typeof app.parseLaunch === 'function') {
        const launch = (wx.getLaunchOptionsSync && wx.getLaunchOptionsSync()) || {}
        app.parseLaunch(launch)
      }
    } catch (_) {}
    this.bootstrap()
  },

  onShow() {
    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
  },

  async refreshPrivacyState() {
    const setting = await privacy.setupPrivacy()
    const need = !!setting.needAuthorization
    const apiHint = app.globalData.apiBase || resolveApiBase()
    const baseTip = need
      ? '请先点击上方「同意隐私指引」，再获取微信头像/昵称/手机号；也可手输昵称与手机号，或从相册选头像。'
      : '微信头像、昵称需分开操作：点头像选头像，点昵称框后在键盘上方选「用微信昵称」。'
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

  async onPrivacyAgree(e) {
    const detail = (e && e.detail) || {}
    console.log('[privacyAgree]', detail)
    privacy.finishPrivacyAgree('agree-btn')
    this.setData({
      needPrivacyAgree: false,
      privacyTip: appendDevApiHint(
        '已同意隐私指引。请点头像选微信头像，点昵称框后在键盘上方选「用微信昵称」。',
        app.globalData.apiBase || resolveApiBase()
      ),
      debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('隐私已同意')
    })
    wx.showToast({ title: '已同意，请继续操作', icon: 'success' })
  },

  async bootstrap() {
    try {
      this.setData({ loading: true })
      await auth.purgeStaleSessionIfNeeded()
      const token = app.globalData.tableToken || wx.getStorageSync('table_token')
      if (token) {
        try {
          await dineCtx.bindTableFromToken(token)
        } catch (_) {
          this.setData({ hasTable: false, table: null })
        }
      }
      let login = null
      try {
        login = await auth.ensureLogin(token, { forceRelogin: true })
      } catch (loginErr) {
        console.error('[bootstrap login]', loginErr)
        wx.showToast({
          title: auth.formatError(loginErr, '登录失败'),
          icon: 'none',
          duration: 3500
        })
      }
      // 首次扫码：游客可直接点餐，不强制注册墙
      const loginOk = !!(login && login.customer_token)
      const nicknameFromServer = loginOk ? String(login.nickname || '') : ''
      const memberStatus = String((login && login.member_status) || '').toUpperCase()
      const isGuest = loginOk
        ? (login.is_guest === true || memberStatus === 'GUEST' || login.need_member_prompt === true)
        : true
      // 首页不再拦截；会员引导改到支付成功页 /「我的」
      const showMemberPrompt = false
      const ctx = dineCtx.currentDisplay()
      const nickname = auth.isPlaceholderNickname(nicknameFromServer) ? '' : nicknameFromServer
      const avatarUrl = loginOk ? String(login.avatar_url || '') : ''
      this.setData({
        loading: false,
        nickname,
        avatarUrl: avatarUtil.resolveDisplayUrl(avatarUrl) || avatarUrl,
        phone: loginOk ? String(login.phone || '') : '',
        phoneInput: '',
        showMemberPrompt,
        isGuest,
        ready: true,
        table: ctx.table || app.globalData.table,
        store: ctx.store || app.globalData.store,
        ...dineCtx.pageDineFields(ctx)
      })
      auth.fetchCustomerMeSilent().then((me) => {
        if (me && me.phone) this.setData({ phone: me.phone })
        if (me && me.avatar_url) {
          app.globalData.avatarUrl = me.avatar_url
          this.setData({ avatarUrl: avatarUtil.resolveDisplayUrl(me.avatar_url) })
        }
      })
      if (loginOk && nickname) {
        wx.setStorageSync('nickname', nickname)
      } else if (loginOk) {
        try { wx.removeStorageSync('nickname') } catch (_) {}
      }
      if (loginOk && avatarUrl) {
        app.globalData.avatarUrl = avatarUrl
        wx.setStorageSync('avatar_url', avatarUrl)
      } else if (loginOk) {
        app.globalData.avatarUrl = ''
        try { wx.removeStorageSync('avatar_url') } catch (_) {}
      }
      if (loginOk && login.customer_id) {
        app.globalData.customerId = login.customer_id
        wx.setStorageSync('customer_id', login.customer_id)
      }
      if (loginOk && login.customer_no) wx.setStorageSync('customer_no', login.customer_no)
      if (loginOk && login.member_status) {
        try { wx.setStorageSync('member_status', login.member_status) } catch (_) {}
      }
      await this.refreshPrivacyState()
      // 登录成功即进菜单（有无桌台均可；无桌台由菜单页提示扫码）
      if (loginOk) {
        this.goMenu()
        return
      }
    } catch (e) {
      this.setData({
        loading: false,
        ready: true,
        showMemberPrompt: true,
        nickname: '',
        avatarUrl: ''
      })
      const msg = auth.formatError(e, '登录失败')
      console.error('[bootstrap login]', e)
      wx.showModal({ title: '登录失败', content: msg, showCancel: false })
    }
  },

  scanTable() {
    dineCtx.scanAndBindTable()
      .then((ctx) => {
        if (ctx) {
          this.setData({
            table: ctx.table,
            store: ctx.store,
            ...dineCtx.pageDineFields(ctx)
          })
        }
      })
      .catch((e) => {
        wx.showToast({ title: (e && e.message) || '扫码失败', icon: 'none' })
      })
  },

  onChooseAvatar(e) {
    const detail = (e && e.detail) || {}
    const url = detail.avatarUrl || ''
    const errMsg = String(detail.errMsg || '')
    console.log('[chooseAvatar]', detail)
    if (!url) {
      if (/privacy|authorize|声明|agree|scope|declared/i.test(errMsg)) {
        this.setData({
          needPrivacyAgree: true,
          debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('头像:隐私未声明')
        })
        privacy.showPrivacyPopup()
        wx.showModal({
          title: '公众平台未声明头像能力',
          content: '请在 mp.weixin.qq.com → 设置 → 服务内容声明 → 用户隐私保护指引 中勾选「收集你的昵称、头像」，提交后等待审核/约5分钟生效。小程序内也需先点「同意隐私指引」。临时可用「从相册选择头像」+手输昵称。',
          showCancel: false
        })
        return
      }
      if (/cancel/i.test(errMsg)) return
      if (errMsg) {
        wx.showToast({ title: errMsg.replace(/^chooseAvatar:fail\s*/i, ''), icon: 'none' })
        return
      }
      wx.showToast({ title: '未选头像，可试相册选图', icon: 'none' })
      return
    }
    this.persistChosenAvatar(url)
  },

  onPickLocalAvatar() {
    const pick = () => {
      if (wx.chooseMedia) {
        wx.chooseMedia({
          count: 1,
          mediaType: ['image'],
          sourceType: ['album', 'camera'],
          success: (res) => {
            const f = (res.tempFiles && res.tempFiles[0]) || {}
            const url = f.tempFilePath || ''
            if (!url) {
              wx.showToast({ title: '未选择图片', icon: 'none' })
              return
            }
            this.persistChosenAvatar(url)
          },
          fail: (err) => {
            if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return
            wx.showToast({ title: '选图失败', icon: 'none' })
          }
        })
        return
      }
      wx.chooseImage({
        count: 1,
        sizeType: ['compressed'],
        sourceType: ['album', 'camera'],
        success: (res) => {
          const url = (res.tempFilePaths && res.tempFilePaths[0]) || ''
          if (!url) return
          this.persistChosenAvatar(url)
        }
      })
    }
    pick()
  },

  async persistChosenAvatar(localOrRemoteUrl) {
    wx.showLoading({ title: '上传头像' })
    try {
      const remote = await avatarUtil.ensureRemoteAvatar(localOrRemoteUrl)
      const display = avatarUtil.resolveDisplayUrl(remote) || remote
      this.setData({
        avatarUrl: display,
        debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('头像:已保存')
      })
      app.globalData.avatarUrl = remote
      wx.setStorageSync('avatar_url', remote)
      auth.writeRegDraft({ avatarUrl: remote })
      wx.hideLoading()
      wx.showToast({ title: '头像已保存', icon: 'success' })
    } catch (e) {
      wx.hideLoading()
      this.setData({
        avatarUrl: localOrRemoteUrl,
        debugInfo: getEnvDebugInfo().isRelease ? '' : this.buildDebugInfo('头像:待重试')
      })
      app.globalData.avatarUrl = localOrRemoteUrl
      wx.setStorageSync('avatar_url', localOrRemoteUrl)
      auth.writeRegDraft({ avatarUrl: localOrRemoteUrl })
      wx.showToast({
        title: avatarUtil.formatUploadError(e, '头像上传失败，注册时将重试'),
        icon: 'none',
        duration: 2500
      })
    }
  },

  onNicknameInput(e) {
    const nickname = (e.detail && e.detail.value) || ''
    this.setData({ nickname })
    app.globalData.nickname = nickname
    auth.writeRegDraft({ nickname })
  },

  onNicknameChange(e) {
    const nickname = (e.detail && e.detail.value) || ''
    if (!nickname) return
    this.setData({ nickname })
    app.globalData.nickname = nickname
    auth.writeRegDraft({ nickname })
  },

  onNicknameBlur(e) {
    const nickname = (e.detail && e.detail.value) || ''
    if (!nickname) return
    this.setData({ nickname })
    app.globalData.nickname = nickname
    auth.writeRegDraft({ nickname })
  },

  onNicknameReview(e) {
    const detail = (e.detail) || {}
    console.log('[nicknamereview]', detail)
    if (detail.pass === false) {
      this.setData({ nicknameReviewFail: true, nickname: '' })
      wx.showToast({ title: '昵称未通过审核，请修改', icon: 'none' })
    } else if (detail.pass === true) {
      this.setData({ nicknameReviewFail: false })
    }
  },

  onNickname(e) {
    this.onNicknameInput(e)
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async onGetPhoneNumber(e) {
    const detail = (e && e.detail) || {}
    console.log('[getPhoneNumber]', detail)
    phoneAuth.handleGetPhoneNumber(this, detail, async (code) => {
      try {
        wx.showLoading({ title: '绑定中' })
        await auth.ensureLogin()
        let res
        try {
          res = await api.post('/miniprogram/member/phone', { phone_code: code })
        } catch (bindErr) {
          if (Number(bindErr && bindErr.code) !== 40906) throw bindErr
          wx.hideLoading()
          const confirmed = await phoneAuth.confirmAccountMerge(bindErr.message)
          if (!confirmed) return
          wx.showLoading({ title: '合并中' })
          res = await api.post('/miniprogram/member/phone', { phone_code: code, confirm_merge: true })
        }
        if (res && res.customer_token) {
          auth.refreshCustomerToken(app, res.customer_token, {
            customer_id: res.customer_id,
            customer_no: res.customer_no
          })
        }
        wx.hideLoading()
        this.setData({ phone: res.phone || '', phoneInput: '' })
        wx.showToast({ title: '手机号已绑定', icon: 'success' })
      } catch (err) {
        wx.hideLoading()
        wx.showToast({ title: (err && err.message) || '绑定失败，请手输', icon: 'none' })
      }
    })
  },

  onFormSubmit(e) {
    if (this.data.saving) return
    const formVal = (e.detail && e.detail.value) || {}
    const formNick = String(formVal.nickname || '').trim()
    const nickname = formNick || String(this.data.nickname || '').trim()
    this.doSaveAndGo(nickname)
  },

  onSubmitRegister() {
    if (this.data.saving) return
    this.doSaveAndGo(String(this.data.nickname || '').trim())
  },

  async doSaveAndGo(nickname) {
    if (this.data.useWxNickname && this.data.nicknameReviewFail) {
      wx.showToast({ title: '昵称未通过审核，请修改', icon: 'none' })
      return
    }
    if (nickname && (nickname.length < 2 || nickname.length > 20)) {
      wx.showToast({ title: '昵称需 2-20 个字', icon: 'none' })
      return
    }
    const phoneInput = (this.data.phoneInput || '').trim()
    if (phoneInput && !/^1\d{10}$/.test(phoneInput)) {
      wx.showToast({ title: '请输入11位手机号', icon: 'none' })
      return
    }
    try {
      await auth.ensureLogin()
    } catch (e) {
      wx.showToast({ title: auth.formatError(e, '请先登录'), icon: 'none' })
      return
    }
    try {
      this.setData({ saving: true })
      wx.showLoading({ title: '保存中' })
      let avatarPath = this.data.avatarUrl || app.globalData.avatarUrl || ''
      const storedAvatar = wx.getStorageSync('avatar_url') || app.globalData.avatarUrl || ''
      if (avatarUtil.isPersistableAvatarUrl(storedAvatar)) {
        avatarPath = storedAvatar
      }
      try {
        avatarPath = await avatarUtil.ensureRemoteAvatar(avatarPath)
      } catch (upErr) {
        if (avatarUtil.needsUpload(avatarPath)) {
          throw upErr
        }
      }
      const res = await auth.registerMember({
        nickname: nickname || '面馆会员',
        avatar_url: avatarUtil.isPersistableAvatarUrl(avatarPath) ? avatarPath : null,
        phone: phoneInput || null
      })
      app.globalData.nickname = (res && res.nickname) || nickname || '面馆会员'
      app.globalData.avatarUrl = (res && res.avatar_url) || (avatarUtil.isPersistableAvatarUrl(avatarPath) ? avatarPath : '') || ''
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
      wx.setStorageSync('nickname', app.globalData.nickname)
      if (app.globalData.avatarUrl) wx.setStorageSync('avatar_url', app.globalData.avatarUrl)
      auth.clearRegDraft()
      this.setData({
        nickname: app.globalData.nickname,
        avatarUrl: avatarUtil.resolveDisplayUrl(app.globalData.avatarUrl),
        phone: (res && res.phone) || this.data.phone || phoneInput || '',
        phoneInput: '',
        showMemberPrompt: false,
        saving: false
      })
      wx.hideLoading()
      if (res && res.welcome_coupon_issued) {
        const cname = (res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'
        wx.showToast({ title: `已赠送「${cname}」`, icon: 'success' })
      }
      this.goMenu()
    } catch (e) {
      this.setData({ saving: false })
      wx.hideLoading()
      wx.showToast({ title: e.message || '保存失败', icon: 'none' })
    }
  },

  goMenu() {
    wx.switchTab({
      url: '/pages/menu/menu',
      fail: (err) => {
        wx.showToast({ title: (err && err.errMsg) || '无法打开点餐页', icon: 'none' })
      }
    })
  },

  goMember() {
    wx.switchTab({
      url: '/pages/member/member',
      fail: (err) => {
        wx.showToast({ title: (err && err.errMsg) || '无法打开会员页', icon: 'none' })
      }
    })
  }
})
