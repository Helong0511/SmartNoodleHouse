const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const privacy = require('../../utils/privacy')
const phoneAuth = require('../../utils/phoneAuth')
const voucherUtil = require('../../utils/voucher')
const clientConfig = require('../../utils/clientConfig')
const avatarUtil = require('../../utils/avatar')
const { shouldShowDevTips } = require('../../config/env')
const navBar = require('../../utils/navBar')
const faceUtil = require('../../utils/face')
const { formatDate, normalizePickerDate } = require('../../utils/date')
const app = getApp()

function discountText(rate) {
  if (!rate) return ''
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  const zhe = Math.round(n * 100) / 10
  return `${zhe}折`
}

function formatExpire(iso) {
  const date = formatDate(iso)
  return date ? `至 ${date}` : ''
}

/** 等级列表 → 横向阶梯节点 + 进度填充比例 */
function buildLevelLadder(levels) {
  const list = (levels || []).map((lv) => {
    const name = String((lv && lv.name) || '')
    const shortName = name.replace(/会员$/, '') || name || '等级'
    return {
      ...lv,
      shortName,
      discLabel: (lv && lv.discount_text) || '无折扣',
      ptsLabel: `${(lv && lv.min_points) != null ? lv.min_points : 0}起`
    }
  })
  let fill = 0
  if (list.length <= 1) {
    fill = list[0] && list[0].unlocked ? 100 : 0
  } else {
    let idx = list.findIndex((l) => l.is_current)
    if (idx < 0) {
      idx = -1
      for (let i = 0; i < list.length; i++) {
        if (list[i].unlocked) idx = i
      }
    }
    fill = Math.max(0, Math.min(100, (Math.max(idx, 0) / (list.length - 1)) * 100))
  }
  return { memberLevels: list, levelLadderFill: Math.round(fill) }
}

const PROFILE_CACHE_MS = 30 * 1000
const PRIVACY_CACHE_MS = 30 * 1000

Page({
  data: {
    loading: true,
    nickname: '',
    displayName: '面馆会员',
    editingName: false,
    nicknameDraft: '',
    savingName: false,
    useWxNickname: false,
    avatarLetter: '会',
    avatarUrl: '',
    customerNo: '',
    prefs: [],
    prefsConfirmed: [],
    prefsLearning: [],
    prefsEmpty: true,
    isMember: false,
    needActivate: false,
    activatingMember: false,
    memberLevelName: '',
    memberDiscountText: '',
    memberPoints: 0,
    balanceTotal: '0.00',
    nextLevelName: '',
    pointsToNext: 0,
    levelProgress: 0,
    pointsRule: '',
    memberLevels: [],
    levelLadderFill: 0,
    coupons: [],
    vouchers: [],
    walletLoading: false,
    hideMemberEmptyWallet: true,
    showSocialBenefit: true,
    socialIdentities: [],
    socialApplyTypes: [],
    socialLoading: false,
    socialApplying: false,
    socialExpanded: false,
    socialCanApply: true,
    socialApproved: false,
    socialRevoked: false,
    socialSummary: '点击展开申请',
    phone: '',
    canWxPhone: false,
    showDevTips: false,
    phoneInput: '',
    savingPhone: false,
    needPrivacyAgree: false,
    nicknameReviewFail: false,
    storeId: null,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    storeLogo: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    hasTable: false,
    isTakeawayChannel: false,
    recommendOptIn: 1,
    consentVersion: '',
    consentAt: '',
    showTasteProfile: false,
    tasteProfile: null,
    clearingProfile: false,
    updatingOptIn: false,
    faceEnabled: false,
    updatingFace: false,
    showFaceWelcome: false,
    birthday: '',
    birthdayCalendar: 'SOLAR',
    birthdayDisplay: '',
    birthdayBenefitText: '',
    birthdayEnd: '',
    savingBirthday: false,
    showQueueEntry: false
  },

  onLoad() {
    this.setData({
      birthdayEnd: formatDate(new Date())
    })
    this._unsubDine = dineCtx.subscribeDineContext((ctx) => {
      this.applyDineContext(ctx)
    })
    // 首屏先用本地桌台上下文铺顶栏，避免等 ensureDineContext 才画
    this.applyDineContext(dineCtx.currentDisplay())
  },

  onUnload() {
    if (typeof this._unsubDine === 'function') {
      this._unsubDine()
      this._unsubDine = null
    }
  },

  onReady() {
    // 部分机型 Tab 首切内容层未绘：就绪后再轻推一次差分 setData 触发重绘
    this._ensurePainted()
  },

  onShow() {
    navBar.pinWechatNavBar()
    this.applyDineContext(dineCtx.currentDisplay())
    this._ensurePainted()

    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
    this.setData({
      showDevTips: shouldShowDevTips()
    })

    // 重活延后到下一帧，先让 store-bar / 骨架完成首绘（扫码后立刻切会员时尤其重要）
    const runHeavy = () => {
      // force：门店开关改完后切 Tab 须重新拉 config，避免关了再开仍隐藏公益区
      this.refreshMemberFlags(true)
      privacy.setupPrivacy().then((setting) => {
        this.setData({ needPrivacyAgree: !!(setting && setting.needAuthorization) })
      }).catch(() => { })
      this.loadPrivacySettings()
      dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx)).catch(() => { })
      const forceWallet = !!app.globalData.walletDirty
      if (forceWallet) {
        app.globalData.walletDirty = false
        this._profileLoadedAt = 0
        const snap = app.globalData.walletSnapshot
        if (snap && snap.balance_total != null) {
          this.setData({ balanceTotal: snap.balance_total || '0.00' })
          app.globalData.walletSnapshot = null
        }
      }
      const forcePrefs = !app.globalData.prefsAt
      this.loadProfile(forcePrefs || forceWallet)
    }
    this._showEpoch = (this._showEpoch || 0) + 1
    const epoch = this._showEpoch
    const schedule = typeof wx !== 'undefined' && typeof wx.nextTick === 'function'
      ? (fn) => wx.nextTick(fn)
      : (fn) => setTimeout(fn, 0)
    schedule(() => {
      if (this._showEpoch !== epoch) return
      runHeavy()
    })
    // 兜底：登录/接口卡住时也要结束骨架，避免长期白屏感
    if (this._loadingWatchdog) clearTimeout(this._loadingWatchdog)
    this._loadingWatchdog = setTimeout(() => {
      if (this.data.loading) this.setData({ loading: false })
    }, 8000)
  },

  /** 拉取门店配置并刷新会员页开关（公益区等） */
  refreshMemberFlags(force = false) {
    const sid = this.data.storeId || null
    return clientConfig.loadClientConfig(!!force, sid).then(() => {
      const flags = clientConfig.memberPageFlags()
      this.setData({
        ...clientConfig.wxPrivacyPageData(),
        ...flags,
        showQueueEntry: !!clientConfig.boolFlag('queue_enabled')
      })
      if (flags.showSocialBenefit) this.loadSocialIdentities()
      else this.setData({ ...this._buildSocialState([]), socialLoading: false, socialExpanded: false })
    }).catch(() => { })
  },

  onPullDownRefresh() {
    this._profileLoadedAt = 0
    Promise.all([
      dineCtx.ensureDineContext({ force: true }).then((ctx) => {
        this.applyDineContext(ctx)
        return this.refreshMemberFlags(true)
      }),
      this.loadProfile(true)
    ]).finally(() => wx.stopPullDownRefresh())
  },

  _ensurePainted() {
    // 仅在仍停在 loading 且尚无会员编号时轻推，避免无意义整页 setData
    if (this.data.loading && !this.data.customerNo && !this._paintNudgeAt) {
      this._paintNudgeAt = Date.now()
      this.setData({ loading: true })
    }
  },

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
  },

  onTableChange(e) {
    const ctx = e.detail
    if (!ctx) return
    this.applyDineContext(ctx)
    this.loadWallet()
    this.refreshMemberFlags(true)
  },

  _socialLabelMap() {
    const map = {
      SANITATION: '环卫工',
      RIDER: '外卖骑手',
      ELDER: '长者',
      TEACHER: '教师',
      STUDENT: '学生',
      MEDICAL: '医护人员',
      DISABLED: '残障人士',
      VETERAN: '退役军人',
      VOLUNTEER: '志愿者'
    }
    ;(this.data.socialApplyTypes || []).forEach((t) => {
      if (t && t.code) {
        const code = String(t.code).toUpperCase()
        const name = (t.name && String(t.name).trim()) || map[code]
        if (name) map[code] = name
      }
    })
    return map
  },

  async loadSocialIdentityTypes() {
    try {
      const res = await api.get('/social-benefits/identity-types', {}, false)
      const items = ((res && res.items) || []).map((t) => ({
        code: String((t && t.code) || '').toUpperCase(),
        name: (t && t.name) || ''
      })).filter((t) => t.code && t.name)
      this.setData({ socialApplyTypes: items })
    } catch (_) {
      // 弱网兜底：仍展示常见身份，避免申请区空白
      if (!(this.data.socialApplyTypes || []).length) {
        this.setData({
          socialApplyTypes: [
            { code: 'SANITATION', name: '环卫工' },
            { code: 'RIDER', name: '外卖骑手' },
            { code: 'ELDER', name: '长者' },
            { code: 'TEACHER', name: '教师' },
            { code: 'STUDENT', name: '学生' },
            { code: 'MEDICAL', name: '医护人员' },
            { code: 'DISABLED', name: '残障人士' },
            { code: 'VETERAN', name: '退役军人' },
            { code: 'VOLUNTEER', name: '志愿者' }
          ]
        })
      }
    }
  },

  _socialStatusMap() {
    return {
      PENDING: '待审核',
      ACTIVE: '已生效',
      REJECTED: '已拒绝',
      REVOKED: '已取消',
      EXPIRED: '已过期'
    }
  },

  _buildSocialState(items) {
    const labelMap = this._socialLabelMap()
    const statusMap = this._socialStatusMap()
    const list = (items || []).map((item) => {
      const code = String(item.identity_code || '').toUpperCase()
      const st = String(item.status || '').toUpperCase()
      return {
        ...item,
        identity_code: code,
        status: st,
        displayLabel: labelMap[code] || '公益身份',
        statusLabel: statusMap[st] || '—'
      }
    })
    const active = list.find((i) => i.status === 'ACTIVE')
    const pending = list.find((i) => i.status === 'PENDING')
    const revoked = list.find((i) => i.status === 'REVOKED')
    let socialSummary = '点击展开申请'
    if (revoked) socialSummary = '已取消，不可再申请'
    else if (active) socialSummary = `已通过 · ${active.displayLabel}`
    else if (pending) socialSummary = `待审核 · ${pending.displayLabel}`
    return {
      socialIdentities: list,
      socialCanApply: !active && !revoked,
      socialApproved: !!active,
      socialRevoked: !!revoked,
      socialSummary
    }
  },

  toggleSocialExpand() {
    this.setData({ socialExpanded: !this.data.socialExpanded })
  },

  async loadSocialIdentities() {
    if (!clientConfig.isSocialBenefitEnabled()) {
      this.setData({ ...this._buildSocialState([]), socialLoading: false, showSocialBenefit: false })
      return
    }
    this.loadSocialIdentityTypes()
    if (!auth.getToken()) {
      try { await auth.ensureLogin() } catch (_) { return }
    }
    if (!auth.getToken()) return
    this.setData({ socialLoading: true })
    try {
      const res = await api.get('/social-benefits/identities/mine')
      this.setData({
        ...this._buildSocialState((res && res.items) || []),
        socialLoading: false
      })
    } catch (e) {
      this.setData({
        ...this._buildSocialState([]),
        socialLoading: false
      })
    }
  },

  async applySocialIdentity(e) {
    if (this.data.socialRevoked) {
      wx.showToast({ title: '公益身份已取消，不可再申请', icon: 'none' })
      return
    }
    if (this.data.socialApproved || !this.data.socialCanApply) {
      wx.showToast({ title: '公益身份已通过，无需再申请', icon: 'none' })
      return
    }
    try {
      await auth.ensureLogin()
    } catch (_) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    if (!auth.getToken()) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    const code = ((e && e.currentTarget && e.currentTarget.dataset && e.currentTarget.dataset.code) || 'SANITATION').toUpperCase()
    const labelMap = this._socialLabelMap()
    this.setData({ socialApplying: true })
    try {
      // 优先桌台上下文门店，避免 store_id 为空导致后台难按店归集
      let storeId = this.data.storeId || null
      try {
        const ctx = dineCtx.currentDisplay() || {}
        if (ctx.storeId) storeId = ctx.storeId
      } catch (_) { /* ignore */ }
      const row = await api.post('/social-benefits/identities/apply', {
        identity_code: code,
        store_id: storeId,
        credential_note: '小程序申请'
      })
      const st = (row && row.status) || 'PENDING'
      wx.showToast({
        title: st === 'ACTIVE' ? `${labelMap[code] || '公益身份'}已生效` : `${labelMap[code] || '公益身份'}已提交，待店员审核`,
        icon: 'success',
        duration: 2500
      })
      this.setData({ socialExpanded: true })
      this.loadSocialIdentities()
    } catch (err) {
      const msg = (err && err.message) || '申请失败'
      wx.showModal({
        title: '申请未成功',
        content: /无效公益身份/.test(msg)
          ? `${msg}\n请确认后台已导入社会贡献身份类型（social_identity_types）。`
          : msg,
        showCancel: false
      })
    } finally {
      this.setData({ socialApplying: false })
    }
  },

  applyWallet(couponRes, voucherRes) {
    const coupons = ((couponRes && couponRes.items) || []).map((c) => ({
      ...c,
      displayName: voucherUtil.holdDisplayName(c),
      expireText: formatExpire(c.expires_at)
    }))
    const vouchers = ((voucherRes && voucherRes.items) || []).map((v) => ({
      ...v,
      displayName: voucherUtil.holdDisplayName(v),
      expireText: formatExpire(v.expires_at)
    }))
    this.setData({ coupons, vouchers, walletLoading: false })
  },

  applyProfile(profile, prefView) {
    if (!profile || typeof profile !== 'object') {
      this.setData({ loading: false, walletLoading: false })
      return
    }
    const memberStatus = String(profile.member_status || '').toUpperCase()
    const isMember = profile.is_member === true || memberStatus === 'MEMBER'
    const needActivate = !isMember
    const nickname = profile.nickname || app.globalData.nickname || ''
    const displayName = nickname || (isMember ? '面馆会员' : '面馆访客')
    if (profile.nickname) app.globalData.nickname = profile.nickname
    if (profile.avatar_url) app.globalData.avatarUrl = profile.avatar_url
    if (profile.customer_no) {
      app.globalData.customerNo = profile.customer_no
      try {
        wx.setStorageSync('customer_no', profile.customer_no)
      } catch (_) { }
    }
    try {
      wx.setStorageSync('member_status', isMember ? 'MEMBER' : 'GUEST')
    } catch (_) { }
    const view = prefView || prefUtil.buildPrefDisplay({})
    const rawAvatar = profile.avatar_url || app.globalData.avatarUrl || ''
    const face = profile.face_status || {}
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || (isMember ? '会' : '访'),
      avatarUrl: avatarUtil.resolveDisplayUrl(rawAvatar),
      customerNo: profile.customer_no || app.globalData.customerNo || '',
      faceEnabled: !!face.has_face,
      prefs: view.prefs || [],
      prefsConfirmed: view.confirmed || [],
      prefsLearning: view.learning || [],
      prefsEmpty: !!view.isEmpty,
      isMember,
      needActivate,
      memberLevelName: isMember ? (profile.member_level_name || '') : '',
      memberDiscountText: isMember ? discountText(profile.member_discount_rate) : '',
      memberPoints: isMember ? (profile.member_points || 0) : 0,
      balanceTotal: isMember ? (profile.balance_total || '0.00') : '0.00',
      nextLevelName: isMember ? (profile.next_level_name || '') : '',
      pointsToNext: isMember ? (profile.points_to_next_level || 0) : 0,
      levelProgress: isMember ? (profile.level_progress_percent || 0) : 0,
      pointsRule: profile.points_rule || '实付1元=1积分，积分达标自动升级',
      ...buildLevelLadder(profile.member_levels || []),
      phone: profile.phone || '',
      // birthday 仅作日期控件 value：农历时为农历年-月-日数字，不是公历锚点
      birthday: profile.birthday_picker || profile.birthday || '',
      birthdayCalendar: profile.birthday_calendar === 'LUNAR' ? 'LUNAR' : 'SOLAR',
      birthdayDisplay: profile.birthday_display || profile.birthday || '',
      birthdayBenefitText: (profile.birthday_benefit && profile.birthday_benefit.text) || '',
      loading: false
    })
  },

  async loadProfile(force = false) {
    if (this._profileInflight) {
      if (force) this._profileNeedReload = true
      return this._profileInflight
    }
    const hasCache = !!this.data.customerNo
    if (!hasCache) this.setData({ loading: true })
    const now = Date.now()
    if (
      !force
      && hasCache
      && this._profileLoadedAt
      && now - this._profileLoadedAt < PROFILE_CACHE_MS
    ) {
      this.setData({ loading: false })
      return
    }
    const loadGen = (this._profileLoadGen = (this._profileLoadGen || 0) + 1)
    this._profileInflight = (async () => {
      try {
        await auth.ensureLogin()
        const cid = app.globalData.customerId || auth.readStorage('customer_id')
        if (cid) app.globalData.customerId = cid
        this.setData({ walletLoading: true })
        const storeId = dineCtx.currentDisplay().storeId
        const [profile, prefView, couponRes, voucherRes] = await Promise.all([
          api.get('/miniprogram/customers/me'),
          prefUtil.loadCustomerPrefs(app, api).catch(() => prefUtil.buildPrefDisplay({})),
          api.get('/miniprogram/coupons/mine', { store_id: storeId }).catch(() => ({ items: [] })),
          api.get('/miniprogram/vouchers/mine', { store_id: storeId }).catch(() => ({ items: [] }))
        ])
        if (loadGen !== this._profileLoadGen) return
        if (!profile || typeof profile !== 'object') {
          throw new Error('会员资料加载失败，请稍后重试')
        }
        if (profile.customer_id) {
          app.globalData.customerId = profile.customer_id
          wx.setStorageSync('customer_id', profile.customer_id)
        }
        if (profile.customer_no) {
          app.globalData.customerNo = profile.customer_no
          wx.setStorageSync('customer_no', profile.customer_no)
        }
        this.applyProfile(profile, prefView)
        this.applyWallet(couponRes, voucherRes)
        this._profileLoadedAt = Date.now()
      } catch (e) {
        this.setData({ loading: false, walletLoading: false })
        if (!hasCache) wx.showToast({ title: e.message || '加载失败', icon: 'none' })
      } finally {
        this._profileInflight = null
        if (this._profileNeedReload) {
          this._profileNeedReload = false
          this.loadProfile(true)
        }
      }
    })()
    return this._profileInflight
  },

  async loadWallet() {
    this.setData({ walletLoading: true })
    try {
      const [couponRes, voucherRes] = await Promise.all([
        api.get('/miniprogram/coupons/mine', { store_id: dineCtx.currentDisplay().storeId }).catch(() => ({ items: [] })),
        api.get('/miniprogram/vouchers/mine', { store_id: dineCtx.currentDisplay().storeId }).catch(() => ({ items: [] }))
      ])
      this.applyWallet(couponRes, voucherRes)
    } catch (e) {
      this.setData({ walletLoading: false })
      wx.showToast({ title: e.message || '刷新失败', icon: 'none' })
    }
  },

  startEditName() {
    this.setData({
      editingName: true,
      nicknameDraft: this.data.nickname || this.data.displayName || ''
    })
  },

  cancelEditName() {
    if (this.data.savingName) return
    this.setData({ editingName: false, nicknameDraft: '' })
  },

  onNicknameDraft(e) {
    this.setData({ nicknameDraft: (e.detail && e.detail.value) || '' })
  },

  onNicknameDraftReview(e) {
    const detail = (e.detail) || {}
    if (detail.pass === false) {
      this.setData({ nicknameReviewFail: true, nicknameDraft: '' })
      wx.showToast({ title: '昵称未通过审核，请修改', icon: 'none' })
    } else if (detail.pass === true) {
      this.setData({ nicknameReviewFail: false })
    }
  },

  onSaveNicknameForm(e) {
    if (this.data.savingName) return
    const formVal = (e.detail && e.detail.value) || {}
    const nickname = String(formVal.nickname || this.data.nicknameDraft || '').trim()
    this.saveNickname(nickname)
  },

  async saveNickname(nicknameArg) {
    if (this.data.savingName) return
    const nickname = String(nicknameArg != null ? nicknameArg : this.data.nicknameDraft || '').trim()
    if (this.data.useWxNickname && this.data.nicknameReviewFail) {
      wx.showToast({ title: '昵称未通过审核，请修改', icon: 'none' })
      return
    }
    if (nickname.length < 2 || nickname.length > 20) {
      wx.showToast({ title: '昵称需 2-20 个字', icon: 'none' })
      return
    }
    try {
      this.setData({ savingName: true })
      const res = await auth.patchMemberProfile({
        nickname,
        avatar_url: (() => {
          const raw = app.globalData.avatarUrl || wx.getStorageSync('avatar_url') || ''
          return avatarUtil.isPersistableAvatarUrl(raw) ? raw : null
        })()
      })
      const saved = (res && res.nickname) || nickname
      const savedAvatar = (res && res.avatar_url) || app.globalData.avatarUrl || ''
      app.globalData.nickname = saved
      if (savedAvatar && avatarUtil.isPersistableAvatarUrl(savedAvatar)) {
        app.globalData.avatarUrl = savedAvatar
        wx.setStorageSync('avatar_url', savedAvatar)
      }
      wx.setStorageSync('nickname', saved)
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      this._profileLoadedAt = 0
      this.setData({
        nickname: saved,
        displayName: saved,
        avatarLetter: saved.charAt(0) || '会',
        avatarUrl: avatarUtil.resolveDisplayUrl(savedAvatar),
        editingName: false,
        nicknameDraft: '',
        savingName: false
      })
      wx.showToast({ title: '已更新昵称', icon: 'success' })
    } catch (e) {
      this.setData({ savingName: false })
      wx.showToast({ title: e.message || '保存失败', icon: 'none' })
    }
  },

  goRecharge() {
    wx.navigateTo({ url: '/pages/recharge/recharge' })
  },

  async activateSmartMember() {
    if (this.data.activatingMember || this.data.isMember) return
    try {
      this.setData({ activatingMember: true })
      const res = await auth.activateMember({
        nickname: this.data.nickname || undefined,
        avatar_url: (() => {
          const raw = app.globalData.avatarUrl || wx.getStorageSync('avatar_url') || ''
          return avatarUtil.isPersistableAvatarUrl(raw) ? raw : null
        })()
      })
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      this._profileLoadedAt = 0
      await this.loadProfile(true)
      const tip = (res && res.welcome_coupon_issued)
        ? `已开启会员，赠送「${(res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'}」`
        : '已开启智能会员'
      wx.showToast({ title: tip, icon: 'success' })
      if (res && res.welcome_coupon_issued) this.loadWallet()
    } catch (e) {
      wx.showToast({ title: e.message || '开通失败', icon: 'none' })
    } finally {
      this.setData({ activatingMember: false })
    }
  },

  async onGetPhoneNumber(e) {
    const detail = (e && e.detail) || {}
    phoneAuth.handleGetPhoneNumber(this, detail, (code) => {
      this._savePhone({ phone_code: code })
    })
  },

  onPrivacyAgree() {
    privacy.finishPrivacyAgree('agree-btn-phone')
    this.setData({ needPrivacyAgree: false })
  },

  // 调试用：个人主体/未开通取号能力时模拟官方一键授权，走与生产相同的绑定链路
  mockPhoneAuthorize() {
    this._savePhone({ phone_code: 'mock:debug' })
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async bindManualPhone() {
    const phone = String(this.data.phoneInput || '').trim()
    if (!/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: '请输入正确的11位手机号', icon: 'none' })
      return
    }
    await this._savePhone({ phone })
  },

  async _savePhone(payload, retried = false) {
    try {
      wx.showLoading({ title: '绑定中' })
      await auth.ensureLogin()
      const res = await api.post('/miniprogram/member/phone', payload)
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      wx.hideLoading()
      if (res && res.welcome_coupon_issued) {
        const cname = (res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'
        wx.showToast({ title: `绑定成功，已赠送「${cname}」`, icon: 'success' })
      } else {
        wx.showToast({ title: res.phone_verified ? '验证成功' : '已保存，尚未验证', icon: 'success' })
      }
      this.setData({ phone: res.phone || '' })
      if (res && res.welcome_coupon_issued) {
        this._profileLoadedAt = 0
        this.loadWallet()
      }
    } catch (e) {
      const msg = String((e && e.message) || '')
      if (Number(e && e.code) === 40906 && !payload.confirm_merge && (payload.phone_code || payload.alipay_phone_encrypted)) {
        wx.hideLoading()
        const confirmed = await phoneAuth.confirmAccountMerge(msg)
        if (confirmed) return this._savePhone({ ...payload, confirm_merge: true }, retried)
        return
      }
      if (!retried && /顾客不存在|已禁用|令牌已失效|请先登录|无效顾客|401/.test(msg)) {
        try {
          wx.hideLoading()
          await auth.ensureLogin()
          return this._savePhone(payload, true)
        } catch (_) { /* fallthrough */ }
      }
      wx.hideLoading()
      wx.showToast({ title: msg || '绑定失败', icon: 'none' })
    }
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  },

  async onChooseAvatar(e) {
    const tempUrl = e && e.detail && e.detail.avatarUrl
    if (!tempUrl) return
    try {
      wx.showLoading({ title: '更新头像' })
      await auth.ensureLogin()
      // 上传临时头像到服务端
      const persistedUrl = await avatarUtil.ensureRemoteAvatar(tempUrl)
      if (!persistedUrl) throw new Error('头像上传未返回地址')
      // 保存到 profile
      const res = await auth.patchMemberProfile({ avatar_url: persistedUrl })
      app.globalData.avatarUrl = persistedUrl
      wx.setStorageSync('avatar_url', persistedUrl)
      this.setData({
        avatarUrl: avatarUtil.resolveDisplayUrl(persistedUrl)
      })
      wx.hideLoading()
      // 如果非会员且已有昵称，自动注册为会员
      if (!this.data.isMember && this.data.nickname && this.data.nickname.length >= 2) {
        wx.showToast({ title: '头像已更新，正在开通会员...', icon: 'none' })
        await this.activateSmartMember()
      } else {
        wx.showToast({ title: '头像已更新', icon: 'success' })
      }
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: (err && err.message) || '头像更新失败', icon: 'none' })
    }
  },

  // ---------- TR-13 隐私合规 ----------

  async loadPrivacySettings(force = false) {
    const now = Date.now()
    if (
      !force
      && this._privacyLoadedAt
      && now - this._privacyLoadedAt < PRIVACY_CACHE_MS
    ) {
      return
    }
    try {
      await auth.ensureLogin()
      const s = await api.get('/miniprogram/customers/me/privacy-settings')
      if (!s) return
      const opt = Number(s.recommend_opt_in || 0)
      this.setData({
        recommendOptIn: opt,
        consentVersion: s.consent_version || '',
        consentAt: s.consented_at || ''
      })
      app.globalData.recommendOptIn = opt
      app.globalData.recommend_opt_in = opt
      wx.setStorageSync('recommend_opt_in', opt)
      this._privacyLoadedAt = Date.now()
    } catch (_) { /* 静默，不打扰用户 */ }
  },

  async onToggleRecommend(e) {
    if (this.data.updatingOptIn) return
    const val = e && e.detail && typeof e.detail.value === 'boolean'
      ? (e.detail.value ? 1 : 0)
      : (this.data.recommendOptIn ? 0 : 1)
    this.setData({ updatingOptIn: true })
    try {
      await auth.ensureLogin()
      const s = await api.put('/miniprogram/customers/me/privacy-settings', { recommend_opt_in: val })
      const opt = Number((s && s.recommend_opt_in) || val)
      this.setData({
        recommendOptIn: opt,
        consentVersion: (s && s.consent_version) || this.data.consentVersion,
        consentAt: (s && s.consented_at) || this.data.consentAt
      })
      app.globalData.recommendOptIn = opt
      app.globalData.recommend_opt_in = opt
      wx.setStorageSync('recommend_opt_in', opt)
      this._privacyLoadedAt = Date.now()
      try { prefUtil.clearPrefsCache(app) } catch (_) { }
      wx.showToast({
        title: opt ? '已开启个性化推荐' : '已关闭，后续不会记录你的行为',
        icon: 'none'
      })
    } catch (err) {
      // 回滚开关
      this.setData({ recommendOptIn: val ? 0 : 1 })
      wx.showToast({ title: (err && err.message) || '更新失败', icon: 'none' })
    } finally {
      this.setData({ updatingOptIn: false })
    }
  },

  async onViewTasteProfile() {
    if (this.data.tasteProfile) {
      this.setData({ showTasteProfile: true })
      return
    }
    try {
      wx.showLoading({ title: '加载画像' })
      await auth.ensureLogin()
      const data = await api.get('/miniprogram/customers/me/taste-profile')
      wx.hideLoading()
      this.setData({
        tasteProfile: data || null,
        showTasteProfile: true
      })
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: (err && err.message) || '加载失败', icon: 'none' })
    }
  },

  closeTasteProfile() {
    this.setData({ showTasteProfile: false })
  },

  async onClearTasteProfile() {
    if (this.data.clearingProfile) return
    const confirm = await new Promise((resolve) => {
      wx.showModal({
        title: '清除口味画像',
        content: '将清除你的行为记录、口味画像、轻量评价与规格偏好。订单本体保留，不影响积分与权益。此操作不可恢复。',
        confirmText: '确认清除',
        confirmColor: '#e6433a',
        success: (r) => resolve(r.confirm)
      })
    })
    if (!confirm) return
    this.setData({ clearingProfile: true })
    try {
      await auth.ensureLogin()
      const res = await api.post('/miniprogram/customers/me/taste-profile/clear', {})
      wx.showToast({
        title: `已清除 ${res && res.events_deleted ? res.events_deleted : 0} 条记录`,
        icon: 'none'
      })
      // 刷新画像/偏好展示
      this.setData({ tasteProfile: null, showTasteProfile: false })
      this._profileLoadedAt = 0
      await this.loadProfile(true)
    } catch (err) {
      wx.showToast({ title: (err && err.message) || '清除失败', icon: 'none' })
    } finally {
      this.setData({ clearingProfile: false })
    }
  },

  async onToggleFace(e) {
    if (!this.data.showFaceWelcome) return
    if (this.data.updatingFace) return
    const wantOn = !!(e && e.detail && e.detail.value)
    if (wantOn === this.data.faceEnabled) return
    this.setData({ updatingFace: true })
    try {
      if (wantOn) {
        await faceUtil.enrollFaceWelcome()
        this.setData({ faceEnabled: true })
        wx.showToast({ title: '人脸迎宾已开启', icon: 'success' })
      } else {
        const ok = await new Promise((resolve) => {
          wx.showModal({
            title: '关闭人脸迎宾',
            content: '将删除已保存的人脸特征，到店不再自动欢迎。',
            confirmText: '确认关闭',
            confirmColor: '#e6433a',
            success: (r) => resolve(!!r.confirm),
          })
        })
        if (!ok) {
          this.setData({ faceEnabled: true, updatingFace: false })
          return
        }
        await faceUtil.revokeFaceWelcome()
        this.setData({ faceEnabled: false })
        wx.showToast({ title: '已关闭人脸迎宾', icon: 'none' })
      }
    } catch (err) {
      this.setData({ faceEnabled: !wantOn })
      const msg = (err && err.message) || '操作失败'
      if (!/取消/.test(msg)) wx.showToast({ title: msg, icon: 'none' })
    } finally {
      this.setData({ updatingFace: false })
    }
  },

  // ---------- 生日填写 ----------
  async onBirthdayCalendarChange(e) {
    if (this.data.savingBirthday) return
    const birthdayCalendar = (e && e.detail && e.detail.value) || 'SOLAR'
    if (birthdayCalendar !== 'SOLAR' && birthdayCalendar !== 'LUNAR') return
    const prevCal = this.data.birthdayCalendar || 'SOLAR'
    this.setData({ birthdayCalendar, savingBirthday: true })
    try {
      // api 层已解包 data；auth.patchMemberProfile 走 PUT，兼容支付宝
      const d = (await auth.patchMemberProfile({ birthday_calendar: birthdayCalendar })) || {}
      this.setData({
        birthday: d.birthday_picker || d.birthday || this.data.birthday || '',
        birthdayCalendar: d.birthday_calendar === 'LUNAR' ? 'LUNAR' : birthdayCalendar,
        birthdayDisplay: d.birthday_display || d.birthday || this.data.birthday || '',
        birthdayBenefitText: (d.birthday_benefit && d.birthday_benefit.text) || this.data.birthdayBenefitText
      })
      this._profileLoadGen = (this._profileLoadGen || 0) + 1
      this._profileLoadedAt = Date.now()
      wx.showToast({
        title: birthdayCalendar === 'LUNAR' ? '已设为农历（同日）' : '已设为公历（同日）',
        icon: 'none'
      })
    } catch (err) {
      this.setData({ birthdayCalendar: prevCal })
      wx.showToast({ title: (err && err.message) || '保存失败', icon: 'none' })
    } finally {
      this.setData({ savingBirthday: false })
    }
  },

  async onBirthdayChange(e) {
    if (this.data.savingBirthday) return
    const birthday = normalizePickerDate((e && e.detail && e.detail.value) || '')
    if (!birthday) {
      wx.showToast({ title: '生日格式无效，请重选', icon: 'none' })
      return
    }
    this.setData({ savingBirthday: true })
    try {
      const d = (await auth.patchMemberProfile({
        birthday,
        birthday_calendar: this.data.birthdayCalendar || 'SOLAR'
      })) || {}
      const tip = (d.birthday_benefit && d.birthday_benefit.text) || ''
      this.setData({
        birthday: d.birthday_picker || d.birthday || birthday,
        birthdayCalendar: d.birthday_calendar === 'LUNAR' ? 'LUNAR' : (this.data.birthdayCalendar || 'SOLAR'),
        birthdayDisplay: d.birthday_display || d.birthday || birthday,
        birthdayBenefitText: tip || this.data.birthdayBenefitText || ''
      })
      this._profileLoadGen = (this._profileLoadGen || 0) + 1
      this._profileLoadedAt = Date.now()
      wx.showToast({ title: '生日已保存', icon: 'success' })
    } catch (err) {
      wx.showToast({ title: (err && err.message) || '保存失败', icon: 'none' })
    } finally {
      this.setData({ savingBirthday: false })
    }
  },

  // ---------- 排队取号入口 ----------
  goQueue() {
    wx.navigateTo({ url: '/packageOrder/queue/queue' })
  }
})
