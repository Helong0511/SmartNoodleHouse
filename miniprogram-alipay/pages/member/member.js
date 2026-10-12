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
const { formatDate } = require('../../utils/date')
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

/** 解析支付宝 my.getOpenUserInfo 真机返回（常为 { response: '{"nickName":"..","avatar":".."}' }） */
function parseAlipayOpenUserInfo(res) {
  let raw = res
  if (typeof res === 'string') {
    try {
      raw = JSON.parse(res)
    } catch (_) {
      return { nickName: '', avatarUrl: '' }
    }
  }
  if (!raw || typeof raw !== 'object') return { nickName: '', avatarUrl: '' }

  let info = raw
  // 一层 / 两层 response 字符串或对象
  for (let i = 0; i < 2; i++) {
    if (!info || typeof info !== 'object') break
    if (typeof info.response === 'string') {
      try {
        info = JSON.parse(info.response)
      } catch (_) {
        break
      }
      continue
    }
    if (info.response && typeof info.response === 'object') {
      info = info.response
      continue
    }
    break
  }
  if (!info || typeof info !== 'object') return { nickName: '', avatarUrl: '' }
  return {
    nickName: String(info.nickName || info.nick || info.userName || info.user_name || '').trim(),
    avatarUrl: String(info.avatar || info.avatarUrl || info.avatar_url || '').trim()
  }
}

Page({
  data: {
    loading: true,
    nickname: '',
    displayName: '面馆会员',
    editingName: false,
    nicknameDraft: '',
    savingName: false,
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
    socialIdentities: [],
    socialLoading: false,
    socialApplying: false,
    socialExpanded: false,
    socialCanApply: true,
    socialApproved: false,
    socialRevoked: false,
    socialSummary: '点击展开申请',
    hideMemberEmptyWallet: true,
    phone: '',
    canAlipayPhone: false,
    showDevTips: false,
    phoneInput: '',
    savingPhone: false,
    useAlipayNickname: false,
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
    this.applyDineContext(dineCtx.currentDisplay())
  },

  onUnload() {
    if (typeof this._unsubDine === 'function') {
      this._unsubDine()
      this._unsubDine = null
    }
  },

  onReady() {
    this._ensurePainted()
  },

  onShow() {
    navBar.pinAlipayNavBar()
    this.applyDineContext(dineCtx.currentDisplay())
    this._ensurePainted()

    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
    this.setData({
      showDevTips: shouldShowDevTips()
    })

    const runHeavy = () => {
      clientConfig.loadClientConfig().then(() => {
        this.setData({
          ...clientConfig.alipayPrivacyPageData(),
          ...clientConfig.memberPageFlags(),
          showQueueEntry: clientConfig.boolFlag('queue_enabled')
        })
      }).catch(() => { })
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
      this.loadSocialIdentities()
    }
    this._showEpoch = (this._showEpoch || 0) + 1
    const epoch = this._showEpoch
    setTimeout(() => {
      if (this._showEpoch !== epoch) return
      runHeavy()
    }, 0)
    if (this._loadingWatchdog) clearTimeout(this._loadingWatchdog)
    this._loadingWatchdog = setTimeout(() => {
      if (this.data.loading) this.setData({ loading: false })
    }, 8000)
  },

  _ensurePainted() {
    if (this.data.loading && !this.data.customerNo && !this._paintNudgeAt) {
      this._paintNudgeAt = Date.now()
      this.setData({ loading: true })
    }
  },

  applyDineContext(ctx) {
    if (!ctx) return
    dineCtx.applyPageDineFields(this, ctx)
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    this.loadWallet()
    this.loadSocialIdentities()
  },

  _socialLabelMap() {
    return { SANITATION: '环卫工', RIDER: '骑手', ELDER: '长者', TEACHER: '教师', STUDENT: '学生' }
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
        displayLabel: labelMap[code] || code,
        statusLabel: statusMap[st] || st
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

  async applySanitationIdentity() {
    return this.applySocialIdentity({ currentTarget: { dataset: { code: 'SANITATION' } } })
  },

  async applySocialIdentity(e) {
    if (this.data.socialRevoked) {
      my.showToast({ content: '公益身份已取消，不可再申请', type: 'none' })
      return
    }
    if (this.data.socialApproved || !this.data.socialCanApply) {
      my.showToast({ content: '公益身份已通过，无需再申请', type: 'none' })
      return
    }
    try {
      await auth.ensureLogin()
    } catch (_) {
      my.showToast({ content: '请先登录', type: 'none' })
      return
    }
    if (!auth.getToken()) {
      my.showToast({ content: '请先登录', type: 'none' })
      return
    }
    const code = ((e && e.currentTarget && e.currentTarget.dataset && e.currentTarget.dataset.code) || 'SANITATION').toUpperCase()
    const labelMap = this._socialLabelMap()
    this.setData({ socialApplying: true })
    try {
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
      my.showToast({
        content: st === 'ACTIVE' ? `${labelMap[code] || code}已生效` : `${labelMap[code] || code}已提交，待店员审核`,
        type: 'success',
        duration: 2500
      })
      this.setData({ socialExpanded: true })
      this.loadSocialIdentities()
    } catch (err) {
      const msg = (err && err.message) || '申请失败'
      my.alert({
        title: '申请未成功',
        content: /无效公益身份/.test(msg)
          ? `${msg}\n请确认后台已导入社会贡献身份类型（social_identity_types）。`
          : msg
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
        my.setStorageSync({ key: 'customer_no', data: profile.customer_no })
      } catch (_) { }
    }
    try {
      my.setStorageSync({ key: 'member_status', data: isMember ? 'MEMBER' : 'GUEST' })
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
      birthday: profile.birthday || '',
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
        if (!profile || typeof profile !== 'object') {
          throw new Error('会员资料加载失败，请稍后重试')
        }
        if (profile.customer_id) {
          app.globalData.customerId = profile.customer_id
          my.setStorageSync({ key: 'customer_id', data: profile.customer_id })
        }
        if (profile.customer_no) {
          app.globalData.customerNo = profile.customer_no
          my.setStorageSync({ key: 'customer_no', data: profile.customer_no })
        }
        this.applyProfile(profile, prefView)
        this.applyWallet(couponRes, voucherRes)
        this._profileLoadedAt = Date.now()
      } catch (e) {
        this.setData({ loading: false, walletLoading: false })
        if (!hasCache) my.showToast({ content: e.message || '加载失败', type: 'fail' })
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
      my.showToast({ content: e.message || '刷新失败', type: 'fail' })
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
      my.showToast({ content: '昵称未通过审核，请修改', type: 'none' })
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
    if (this.data.useAlipayNickname && this.data.nicknameReviewFail) {
      my.showToast({ content: '昵称未通过审核，请修改', type: 'fail' })
      return
    }
    if (nickname.length < 2 || nickname.length > 20) {
      my.showToast({ content: '昵称需 2-20 个字', type: 'fail' })
      return
    }
    try {
      this.setData({ savingName: true })
      const res = await auth.patchMemberProfile({
        nickname,
        avatar_url: (() => {
          const raw = app.globalData.avatarUrl || auth.readStorage('avatar_url') || ''
          return avatarUtil.isPersistableAvatarUrl(raw) ? raw : null
        })()
      })
      const saved = (res && res.nickname) || nickname
      const savedAvatar = (res && res.avatar_url) || app.globalData.avatarUrl || ''
      app.globalData.nickname = saved
      if (savedAvatar && avatarUtil.isPersistableAvatarUrl(savedAvatar)) {
        app.globalData.avatarUrl = savedAvatar
        my.setStorageSync({ key: 'avatar_url', data: savedAvatar })
      }
      my.setStorageSync({ key: 'nickname', data: saved })
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
      my.showToast({ content: '已更新昵称', type: 'success' })
    } catch (e) {
      this.setData({ savingName: false })
      my.showToast({ content: e.message || '保存失败', type: 'fail' })
    }
  },

  goRecharge() {
    my.navigateTo({ url: '/pages/recharge/recharge' })
  },

  async activateSmartMember() {
    if (this.data.activatingMember || this.data.isMember) return
    try {
      this.setData({ activatingMember: true })
      const res = await auth.activateMember({
        nickname: this.data.nickname || undefined,
        avatar_url: (() => {
          const raw = app.globalData.avatarUrl || auth.readStorage('avatar_url') || ''
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
      my.showToast({ content: tip, type: 'success' })
      if (res && res.welcome_coupon_issued) this.loadWallet()
    } catch (e) {
      my.showToast({ content: e.message || '开通失败', type: 'fail' })
    } finally {
      this.setData({ activatingMember: false })
    }
  },

  onPrivacyAgreeForPhone() {
    privacy.finishPrivacyAgree('agree-phone-btn')
    this.setData({ needPrivacyAgree: false })
    my.showToast({ content: '已同意，请再点授权手机号', type: 'success' })
  },

  onGetPhoneAuthorize(e) {
    phoneAuth.handleGetAuthorizeSuccess(this, e || {}, (encrypted) => {
      this._savePhone({ alipay_phone_encrypted: encrypted })
    })
  },

  onGetPhoneAuthorizeFail(e) {
    phoneAuth.handleAuthError(this, e || { errorMessage: '用户拒绝授权' })
  },

  // 调试用：个人主体/未开通取号能力时模拟官方一键授权，走与生产相同的绑定链路
  mockPhoneAuthorize() {
    this._savePhone({ alipay_phone_encrypted: 'mock:debug' })
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async bindManualPhone() {
    const phone = String(this.data.phoneInput || '').trim()
    if (!/^1\d{10}$/.test(phone)) {
      my.showToast({ content: '请输入正确的11位手机号', type: 'fail' })
      return
    }
    await this._savePhone({ phone })
  },

  async _savePhone(payload, retried = false) {
    try {
      my.showLoading({ content: '绑定中' })
      await auth.ensureLogin()
      const res = await api.post('/miniprogram/member/phone', payload)
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      my.hideLoading()
      if (res && res.welcome_coupon_issued) {
        const cname = (res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'
        my.showToast({ content: `绑定成功，已赠送「${cname}」`, type: 'success' })
      } else {
        my.showToast({ content: res.phone_verified ? '验证成功' : '已保存，尚未验证', type: 'success' })
      }
      this.setData({ phone: res.phone || '' })
      if (res && res.welcome_coupon_issued) {
        this._profileLoadedAt = 0
        this.loadWallet()
      }
    } catch (e) {
      const msg = String((e && e.message) || '')
      if (Number(e && e.code) === 40906 && !payload.confirm_merge && (payload.phone_code || payload.alipay_phone_encrypted)) {
        my.hideLoading()
        const confirmed = await phoneAuth.confirmAccountMerge(msg)
        if (confirmed) return this._savePhone({ ...payload, confirm_merge: true }, retried)
        return
      }
      if (!retried && /顾客不存在|已禁用|令牌已失效|请先登录|无效顾客|401/.test(msg)) {
        try {
          my.hideLoading()
          await auth.ensureLogin()
          return this._savePhone(payload, true)
        } catch (_) { /* fallthrough */ }
      }
      my.hideLoading()
      my.showToast({ content: msg || '绑定失败', type: 'fail' })
    }
  },

  goMenu() {
    my.switchTab({ url: '/pages/menu/menu' })
  },

  async onGetUserInfoAuthorize() {
    try {
      my.showLoading({ content: '获取资料' })
      await auth.ensureLogin()
      // 支付宝 getAuthorize scope=userInfo 后用 my.getOpenUserInfo 获取用户信息
      const res = await new Promise((resolve, reject) => {
        my.getOpenUserInfo({
          success: (r) => resolve(r),
          fail: (e) => reject(e)
        })
      })
      const parsed = parseAlipayOpenUserInfo(res)
      const nickName = parsed.nickName
      const avatarUrl = parsed.avatarUrl
      if (!nickName && !avatarUrl) {
        my.hideLoading()
        my.showToast({ content: '未获取到头像或昵称，请重试授权', type: 'none' })
        return
      }
      // 头像转存到服务端（真机多为 CDN 地址，须 avatar-from-url）
      let persistedAvatar = ''
      let avatarErr = ''
      if (avatarUrl) {
        try {
          persistedAvatar = await avatarUtil.ensureRemoteAvatar(avatarUrl)
        } catch (e) {
          avatarErr = avatarUtil.formatUploadError(e, '头像转存失败')
        }
      }
      const patchData = {}
      if (nickName) patchData.nickname = nickName
      if (persistedAvatar) patchData.avatar_url = persistedAvatar
      if (Object.keys(patchData).length > 0) {
        await auth.patchMemberProfile(patchData)
        if (nickName) {
          app.globalData.nickname = nickName
          my.setStorageSync({ key: 'nickname', data: nickName })
        }
        if (persistedAvatar) {
          app.globalData.avatarUrl = persistedAvatar
          my.setStorageSync({ key: 'avatar_url', data: persistedAvatar })
          this.setData({
            avatarUrl: avatarUtil.resolveDisplayUrl(persistedAvatar)
          })
        }
      }
      my.hideLoading()
      this._profileLoadedAt = 0
      await this.loadProfile(true)
      if (avatarUrl && !persistedAvatar) {
        my.showToast({ content: avatarErr || '昵称已保存，头像转存失败请重试', type: 'none', duration: 2500 })
        return
      }
      if (!this.data.isMember && nickName && nickName.length >= 2) {
        my.showToast({ content: '资料已更新，正在开通会员...', type: 'none' })
        await this.activateSmartMember()
      } else {
        my.showToast({ content: '资料已更新', type: 'success' })
      }
    } catch (err) {
      my.hideLoading()
      my.showToast({ content: (err && err.message) || '授权失败', type: 'fail' })
    }
  },

  onGetUserInfoFail(e) {
    const msg = (e && (e.errorMessage || e.message)) || '用户拒绝授权'
    my.showToast({ content: msg, type: 'fail' })
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
      my.setStorageSync({ key: 'recommend_opt_in', data: opt })
      this._privacyLoadedAt = Date.now()
    } catch (_) { /* 静默 */ }
  },

  async onToggleRecommend(e) {
    if (this.data.updatingOptIn) return
    const v = e && e.detail
    const val = typeof v === 'boolean' ? (v ? 1 : 0) : (typeof v === 'number' ? v : (this.data.recommendOptIn ? 0 : 1))
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
      my.setStorageSync({ key: 'recommend_opt_in', data: opt })
      this._privacyLoadedAt = Date.now()
      my.showToast({
        content: opt ? '已开启个性化推荐' : '已关闭，后续不会记录你的行为',
        type: 'none'
      })
    } catch (err) {
      this.setData({ recommendOptIn: val ? 0 : 1 })
      my.showToast({ content: (err && err.message) || '更新失败', type: 'fail' })
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
      my.showLoading({ content: '加载画像' })
      await auth.ensureLogin()
      const data = await api.get('/miniprogram/customers/me/taste-profile')
      my.hideLoading()
      this.setData({ tasteProfile: data || null, showTasteProfile: true })
    } catch (err) {
      my.hideLoading()
      my.showToast({ content: (err && err.message) || '加载失败', type: 'fail' })
    }
  },

  closeTasteProfile() {
    this.setData({ showTasteProfile: false })
  },

  async onClearTasteProfile() {
    if (this.data.clearingProfile) return
    const confirm = await new Promise((resolve) => {
      my.confirm({
        title: '清除口味画像',
        content: '将清除你的行为记录、口味画像、轻量评价与规格偏好。订单本体保留，不影响积分与权益。此操作不可恢复。',
        confirmButtonText: '确认清除',
        cancelButtonText: '取消',
        success: (r) => resolve(r.confirm)
      })
    })
    if (!confirm) return
    this.setData({ clearingProfile: true })
    try {
      await auth.ensureLogin()
      const res = await api.post('/miniprogram/customers/me/taste-profile/clear', {})
      my.showToast({
        content: `已清除 ${res && res.events_deleted ? res.events_deleted : 0} 条记录`,
        type: 'none'
      })
      this.setData({ tasteProfile: null, showTasteProfile: false })
      this._profileLoadedAt = 0
      await this.loadProfile(true)
    } catch (err) {
      my.showToast({ content: (err && err.message) || '清除失败', type: 'fail' })
    } finally {
      this.setData({ clearingProfile: false })
    }
  },

  async onToggleFace(e) {
    if (!this.data.showFaceWelcome) return
    if (this.data.updatingFace) return
    const v = e && e.detail
    const wantOn = typeof v === 'boolean' ? v : !!(v && v.value)
    if (wantOn === this.data.faceEnabled) return
    this.setData({ updatingFace: true })
    try {
      if (wantOn) {
        await faceUtil.enrollFaceWelcome()
        this.setData({ faceEnabled: true })
        my.showToast({ content: '人脸迎宾已开启', type: 'success' })
      } else {
        const ok = await new Promise((resolve) => {
          my.confirm({
            title: '关闭人脸迎宾',
            content: '将删除已保存的人脸特征，到店不再自动欢迎。',
            confirmButtonText: '确认关闭',
            cancelButtonText: '取消',
            success: (r) => resolve(!!r.confirm),
          })
        })
        if (!ok) {
          this.setData({ faceEnabled: true, updatingFace: false })
          return
        }
        await faceUtil.revokeFaceWelcome()
        this.setData({ faceEnabled: false })
        my.showToast({ content: '已关闭人脸迎宾', type: 'none' })
      }
    } catch (err) {
      this.setData({ faceEnabled: !wantOn })
      const msg = (err && err.message) || '操作失败'
      if (!/取消/.test(msg)) my.showToast({ content: msg, type: 'fail' })
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
        birthday: d.birthday || this.data.birthday || '',
        birthdayCalendar: d.birthday_calendar === 'LUNAR' ? 'LUNAR' : birthdayCalendar,
        birthdayDisplay: d.birthday_display || d.birthday || this.data.birthday || '',
        birthdayBenefitText: (d.birthday_benefit && d.birthday_benefit.text) || this.data.birthdayBenefitText
      })
      this._profileLoadedAt = 0
      my.showToast({
        content: birthdayCalendar === 'LUNAR' ? '已设为农历（同日）' : '已设为公历（同日）',
        type: 'none'
      })
    } catch (err) {
      this.setData({ birthdayCalendar: prevCal })
      my.showToast({ content: (err && err.message) || '保存失败', type: 'fail' })
    } finally {
      this.setData({ savingBirthday: false })
    }
  },

  async onBirthdayChange(e) {
    if (this.data.savingBirthday) return
    const birthday = (e && e.detail && e.detail.value) || ''
    if (!/^\d{4}-\d{2}-\d{2}$/.test(birthday)) return
    this.setData({ savingBirthday: true })
    try {
      const d = (await auth.patchMemberProfile({
        birthday,
        birthday_calendar: this.data.birthdayCalendar || 'SOLAR'
      })) || {}
      const tip = (d.birthday_benefit && d.birthday_benefit.text) || ''
      this.setData({
        birthday: d.birthday || birthday,
        birthdayCalendar: d.birthday_calendar === 'LUNAR' ? 'LUNAR' : (this.data.birthdayCalendar || 'SOLAR'),
        birthdayDisplay: d.birthday_display || d.birthday || birthday,
        birthdayBenefitText: tip || this.data.birthdayBenefitText || ''
      })
      this._profileLoadedAt = 0
      my.showToast({ content: '生日已保存', type: 'success' })
    } catch (err) {
      my.showToast({ content: (err && err.message) || '保存失败', type: 'fail' })
    } finally {
      this.setData({ savingBirthday: false })
    }
  },

  // ---------- 排队取号入口 ----------
  goQueue() {
    my.navigateTo({ url: '/packageOrder/queue/queue' })
  }
})
