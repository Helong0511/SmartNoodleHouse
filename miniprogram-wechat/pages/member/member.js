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
const tabCartBadge = require('../../utils/tabCartBadge')
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
    phone: '',
    canWxPhone: false,
    showDevTips: false,
    phoneInput: '',
    savingPhone: false,
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
    tabCartBadge.syncTabCartBadge()
    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
    this.setData({
      showDevTips: shouldShowDevTips()
    })
    clientConfig.loadClientConfig().then(() => {
      this.setData({
        ...clientConfig.wxPrivacyPageData(),
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
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onTableChange(e) {
    const ctx = e.detail
    if (!ctx) return
    this.applyDineContext(ctx)
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
      wx.setStorageSync('member_status', isMember ? 'MEMBER' : 'GUEST')
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
        wx.setStorageSync('customer_id', profile.customer_id)
      }
      if (profile.customer_no) {
        app.globalData.customerNo = profile.customer_no
        wx.setStorageSync('customer_no', profile.customer_no)
      }
      this.applyProfile(profile, prefView)
      this.applyWallet(couponRes, voucherRes)
      this._profileLoadedAt = now
    } catch (e) {
      this.setData({ loading: false, walletLoading: false })
      if (!hasCache) wx.showToast({ title: e.message || '加载失败', icon: 'none' })
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

  async loadPrivacySettings() {
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
  }
})
