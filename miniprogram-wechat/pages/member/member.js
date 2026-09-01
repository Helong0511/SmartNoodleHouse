const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const { enableWxPhoneBind, enableNicknameInput } = require('../../config/env')
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
    useWxNickname: false,
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
    canWxPhone: false,
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: ''
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    this.setData({
      canWxPhone: !!enableWxPhoneBind,
      useWxNickname: !!enableNicknameInput
    })
    this.applyDineContext(dineCtx.currentDisplay())
    dineCtx.ensureDineContext().then((ctx) => this.applyDineContext(ctx))
    this.loadProfile()
  },

  applyDineContext(ctx) {
    if (!ctx) return
    this.setData(dineCtx.pageDineFields(ctx))
  },

  applyWallet(couponRes, voucherRes) {
    const coupons = ((couponRes && couponRes.items) || []).map((c) => ({
      ...c,
      expireText: formatExpire(c.expires_at)
    }))
    const vouchers = ((voucherRes && voucherRes.items) || []).map((v) => ({
      ...v,
      expireText: formatExpire(v.expires_at)
    }))
    this.setData({ coupons, vouchers, walletLoading: false })
  },

  applyProfile(profile, prefView) {
    if (!profile || typeof profile !== 'object') {
      this.setData({ loading: false, walletLoading: false })
      return
    }
    const nickname = profile.nickname || app.globalData.nickname || ''
    const displayName = nickname || '面馆会员'
    if (profile.nickname) app.globalData.nickname = profile.nickname
    if (profile.avatar_url) app.globalData.avatarUrl = profile.avatar_url
    if (profile.customer_no) app.globalData.customerNo = profile.customer_no
    const view = prefView || prefUtil.buildPrefDisplay({})
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || '会',
      avatarUrl: profile.avatar_url || app.globalData.avatarUrl || '',
      customerNo: profile.customer_no || app.globalData.customerNo || '',
      prefs: view.prefs || [],
      prefsConfirmed: view.confirmed || [],
      prefsLearning: view.learning || [],
      prefsEmpty: !!view.isEmpty,
      isMember: !!profile.is_member,
      memberLevelName: profile.member_level_name || '',
      memberDiscountText: discountText(profile.member_discount_rate),
      memberPoints: profile.member_points || 0,
      nextLevelName: profile.next_level_name || '',
      pointsToNext: profile.points_to_next_level || 0,
      levelProgress: profile.level_progress_percent || 0,
      pointsRule: profile.points_rule || '实付1元=1积分，积分达标自动升级',
      memberLevels: profile.member_levels || [],
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
        api.get('/miniprogram/coupons/mine').catch(() => ({ items: [] })),
        api.get('/miniprogram/vouchers/mine').catch(() => ({ items: [] }))
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

  async saveNickname() {
    if (this.data.savingName) return
    const nickname = (this.data.nicknameDraft || '').trim()
    if (nickname.length < 2 || nickname.length > 20) {
      wx.showToast({ title: '昵称需 2-20 个字', icon: 'none' })
      return
    }
    try {
      this.setData({ savingName: true })
      const res = await api.post('/miniprogram/member/register', {
        nickname,
        avatar_url: this.data.avatarUrl || app.globalData.avatarUrl || null
      })
      const saved = (res && res.nickname) || nickname
      const savedAvatar = (res && res.avatar_url) || this.data.avatarUrl || app.globalData.avatarUrl || ''
      app.globalData.nickname = saved
      if (savedAvatar) {
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
        avatarUrl: savedAvatar,
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

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async bindPhone() {
    const phone = (this.data.phoneInput || '').trim()
    if (!/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: '请输入11位手机号', icon: 'none' })
      return
    }
    await this._savePhone({ phone })
  },

  async onGetPhoneNumber(e) {
    const detail = (e && e.detail) || {}
    if (detail.errMsg && detail.errMsg.indexOf('privacy agreement') >= 0) {
      wx.showModal({
        title: '需配置隐私指引',
        content: '请先在微信公众平台声明「手机号」收集项，并将 config/env.js 中 ENABLE_WX_PRIVACY_APIS 设为 true；当前请使用下方手动输入绑定。',
        showCancel: false
      })
      return
    }
    if (!detail.code) {
      if (detail.errMsg && detail.errMsg.indexOf('deny') >= 0) {
        wx.showToast({ title: '您拒绝了手机号授权', icon: 'none' })
      }
      return
    }
    await this._savePhone({ phone_code: detail.code })
  },

  async _savePhone(payload) {
    try {
      wx.showLoading({ title: '绑定中' })
      const res = await api.post('/miniprogram/member/phone', payload)
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      wx.hideLoading()
      wx.showToast({ title: '绑定成功', icon: 'success' })
      this.setData({ phone: res.phone || this.data.phoneInput, phoneInput: '' })
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: e.message || '绑定失败', icon: 'none' })
    }
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  },

  goOrders() {
    wx.switchTab({ url: '/pages/orders/orders' })
  }
})
