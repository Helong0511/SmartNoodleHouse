const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
const { enableWxPhoneBind } = require('../../config/env')
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
    avatarLetter: '会',
    avatarUrl: '',
    customerNo: '',
    faceOn: false,
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
    this.setData({ canWxPhone: !!enableWxPhoneBind })
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
    const face = (profile && profile.face_status) || {}
    const view = prefView || prefUtil.buildPrefDisplay({})
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || '会',
      avatarUrl: profile.avatar_url || app.globalData.avatarUrl || '',
      customerNo: profile.customer_no || app.globalData.customerNo || '',
      faceOn: !!(face && face.has_face),
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
      if (!app.globalData.customerToken) await auth.ensureLogin()
      const cid = app.globalData.customerId
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

  onFaceSwitch(e) {
    const on = e.detail.value
    if (on) {
      wx.navigateTo({ url: '/packageOrder/face/face' })
    } else {
      wx.showModal({
        title: '关闭人脸迎宾',
        content: '关闭后将立即删除人脸特征，不再到店识别。确定关闭吗？',
        success: (res) => {
          if (res.confirm) this.revokeFace()
          else this.setData({ faceOn: true })
        }
      })
    }
  },

  async revokeFace() {
    try {
      await api.post('/miniprogram/face/revoke', {})
      app.globalData.faceStatus = 'REVOKED'
      this.setData({ faceOn: false })
      wx.showToast({ title: '已关闭人脸迎宾', icon: 'success' })
    } catch (e) {
      this.setData({ faceOn: true })
      wx.showToast({ title: e.message || '操作失败', icon: 'none' })
    }
  },

  goMenu() {
    wx.switchTab({ url: '/pages/menu/menu' })
  },

  goOrders() {
    wx.switchTab({ url: '/pages/orders/orders' })
  }
})
