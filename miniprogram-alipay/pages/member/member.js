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
const app = getApp()

function discountText(rate) {
  if (!rate) return ''
  const n = parseFloat(rate)
  if (!Number.isFinite(n) || n >= 1) return ''
  const zhe = Math.round(n * 100) / 10
  return `${zhe}折`
}

function formatExpire(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return `至 ${String(iso).slice(0, 10)}`
  const pad = (n) => (n < 10 ? `0${n}` : `${n}`)
  return `至 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
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
    hideMemberEmptyWallet: true,
    phone: '',
    canAlipayPhone: false,
    showDevTips: false,
    phoneInput: '',
    savingPhone: false,
    useAlipayNickname: false,
    needPrivacyAgree: false,
    nicknameReviewFail: false,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    storeLogo: '',
    tableName: '',
    tableNo: '',
    seatLabel: '桌号',
    recommendOptIn: 1,
    consentVersion: '',
    consentAt: '',
    showTasteProfile: false,
    tasteProfile: null,
    clearingProfile: false,
    updatingOptIn: false
  },

  onShow() {
    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
    this.setData({
      showDevTips: shouldShowDevTips()
    })
    clientConfig.loadClientConfig().then(() => {
      this.setData({
        ...clientConfig.alipayPrivacyPageData(),
        ...clientConfig.memberPageFlags()
      })
    })
    privacy.setupPrivacy().then((setting) => {
      this.setData({ needPrivacyAgree: !!(setting && setting.needAuthorization) })
    })
    this.loadPrivacySettings()
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    // 支付成功会 clearPrefsCache（prefsAt=0），此处强制重拉，避免 30s 资料缓存挡住智能口味
    // 充值成功回跳会置 walletDirty，必须强制刷新余额
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
  },

  applyDineContext(ctx) {
    if (!ctx) return
    const fields = dineCtx.pageDineFields(ctx)
    const d = this.data
    if (
      d.storeName === fields.storeName
      && d.storeNo === fields.storeNo
      && d.storeLogo === fields.storeLogo
      && d.tableName === fields.tableName
      && d.tableNo === fields.tableNo
      && d.seatLabel === fields.seatLabel
    ) {
      return
    }
    this.setData(fields)
  },

  onTableChange(ctx) {
    if (!ctx) return
    this.applyDineContext(ctx)
    this.loadWallet()
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
    if (profile.customer_no) app.globalData.customerNo = profile.customer_no
    try {
      my.setStorageSync({ key: 'member_status', data: isMember ? 'MEMBER' : 'GUEST' })
    } catch (_) {}
    const view = prefView || prefUtil.buildPrefDisplay({})
    const rawAvatar = profile.avatar_url || app.globalData.avatarUrl || ''
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || (isMember ? '会' : '访'),
      avatarUrl: avatarUtil.resolveDisplayUrl(rawAvatar),
      customerNo: profile.customer_no || app.globalData.customerNo || '',
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
      loading: false
    })
  },

  async loadProfile(force = false) {
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
      this.applyProfile(profile, prefView)
      this.applyWallet(couponRes, voucherRes)
      this._profileLoadedAt = now
    } catch (e) {
      this.setData({ loading: false, walletLoading: false })
      if (!hasCache) my.showToast({ content: e.message || '加载失败', type: 'fail' })
    }
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
  }
})
