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

const PROFILE_CACHE_MS = 30 * 1000

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
    memberLevelName: '',
    memberDiscountText: '',
    memberPoints: 0,
    nextLevelName: '',
    pointsToNext: 0,
    levelProgress: 0,
    pointsRule: '',
    memberLevels: [],
    coupons: [],
    vouchers: [],
    walletLoading: false,
    phone: '',
    phoneInput: '',
    canAlipayPhone: false,
    showDevTips: false,
    useAlipayNickname: false,
    needPrivacyAgree: false,
    nicknameReviewFail: false,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: ''
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
      this.setData(clientConfig.alipayPrivacyPageData())
    })
    privacy.setupPrivacy().then((setting) => {
      this.setData({ needPrivacyAgree: !!(setting && setting.needAuthorization) })
    })
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    this.loadProfile()
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  onTableChange(ctx) {
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
    const pending = !!profile.need_member_prompt
    const draft = pending ? auth.readRegDraft() : { nickname: '', avatarUrl: '' }
    const nickname = pending
      ? String(draft.nickname || '')
      : (profile.nickname || app.globalData.nickname || '')
    const displayName = nickname || '面馆会员'
    if (!pending && profile.nickname) app.globalData.nickname = profile.nickname
    if (!pending && profile.avatar_url) app.globalData.avatarUrl = profile.avatar_url
    if (profile.customer_no) app.globalData.customerNo = profile.customer_no
    const view = prefView || prefUtil.buildPrefDisplay({})
    const rawAvatar = pending
      ? String(draft.avatarUrl || '')
      : (profile.avatar_url || app.globalData.avatarUrl || '')
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || '会',
      avatarUrl: avatarUtil.resolveDisplayUrl(rawAvatar),
      customerNo: profile.customer_no || app.globalData.customerNo || '',
      prefs: view.prefs || [],
      prefsConfirmed: view.confirmed || [],
      prefsLearning: view.learning || [],
      prefsEmpty: !!view.isEmpty,
      isMember: pending ? false : !!profile.is_member,
      memberLevelName: pending ? '' : (profile.member_level_name || ''),
      memberDiscountText: pending ? '' : discountText(profile.member_discount_rate),
      memberPoints: pending ? 0 : (profile.member_points || 0),
      nextLevelName: pending ? '' : (profile.next_level_name || ''),
      pointsToNext: pending ? 0 : (profile.points_to_next_level || 0),
      levelProgress: pending ? 0 : (profile.level_progress_percent || 0),
      pointsRule: profile.points_rule || '实付1元=1积分，积分达标自动升级',
      memberLevels: profile.member_levels || [],
      phone: pending ? '' : (profile.phone || ''),
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
      const [profile, prefRes, couponRes, voucherRes] = await Promise.all([
        api.get('/miniprogram/customers/me'),
        api.get('/miniprogram/customers/me/preferences').catch(() => ({ preferences: [], all: [] })),
        api.get('/miniprogram/coupons/mine').catch(() => ({ items: [] })),
        api.get('/miniprogram/vouchers/mine').catch(() => ({ items: [] }))
      ])
      const prefView = prefUtil.buildPrefDisplay(prefRes)
      app.globalData.prefs = prefView.prefs
      app.globalData.prefsView = prefView
      app.globalData.prefsAt = now
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
        api.get('/miniprogram/coupons/mine').catch(() => ({ items: [] })),
        api.get('/miniprogram/vouchers/mine').catch(() => ({ items: [] }))
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
      const res = await auth.registerMember({
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
      const tip = (res && res.welcome_coupon_issued)
        ? `已赠送「${(res.welcome_coupon && res.welcome_coupon.name) || res.welcome_coupon_name || '新客券'}」`
        : '已更新昵称'
      my.showToast({ content: tip, type: 'success' })
      if (res && res.welcome_coupon_issued) this.loadWallet()
    } catch (e) {
      this.setData({ savingName: false })
      my.showToast({ content: e.message || '保存失败', type: 'fail' })
    }
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
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

  async bindPhone() {
    const phone = (this.data.phoneInput || '').trim()
    if (!/^1\d{10}$/.test(phone)) {
      my.showToast({ content: '请输入11位手机号', type: 'fail' })
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
        my.showToast({ content: '绑定成功', type: 'success' })
      }
      this.setData({ phone: res.phone || this.data.phoneInput, phoneInput: '' })
      if (res && res.welcome_coupon_issued) {
        this._profileLoadedAt = 0
        this.loadWallet()
      }
    } catch (e) {
      const msg = String((e && e.message) || '')
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

  goOrders() {
    my.switchTab({ url: '/pages/orders/orders' })
  }
})
