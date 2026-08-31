const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const prefUtil = require('../../utils/preferences')
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
    storeName: '',
    storeNo: '',
    storeAddress: '',
    storePhone: '',
    tableName: '',
    tableNo: ''
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
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
    if (profile.customer_no) app.globalData.customerNo = profile.customer_no
    const face = (profile && profile.face_status) || {}
    const view = prefView || prefUtil.buildPrefDisplay({})
    this.setData({
      nickname,
      displayName,
      avatarLetter: displayName.charAt(0) || '会',
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

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async bindPhone() {
    const phone = (this.data.phoneInput || '').trim()
    if (!/^1\d{10}$/.test(phone)) {
      my.showToast({ content: '请输入11位手机号', type: 'fail' })
      return
    }
    await this._savePhone({ phone })
  },

  async _savePhone(payload) {
    try {
      my.showLoading({ content: '绑定中' })
      const res = await api.post('/miniprogram/member/phone', payload)
      if (res && res.customer_token) {
        auth.refreshCustomerToken(app, res.customer_token, {
          customer_id: res.customer_id,
          customer_no: res.customer_no
        })
      }
      my.hideLoading()
      my.showToast({ content: '绑定成功', type: 'success' })
      this.setData({ phone: res.phone || this.data.phoneInput, phoneInput: '' })
    } catch (e) {
      my.hideLoading()
      my.showToast({ content: e.message || '绑定失败', type: 'fail' })
    }
  },

  onFaceSwitch(e) {
    const on = e.detail.value
    if (on) {
      my.navigateTo({ url: '/pages/face/face' })
    } else {
      my.confirm({
        title: '关闭人脸迎宾',
        content: '关闭后将立即删除人脸特征，不再到店识别。确定关闭吗？',
        confirmButtonText: '确定关闭',
        cancelButtonText: '取消',
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
      my.showToast({ content: '已关闭人脸迎宾', type: 'success' })
    } catch (e) {
      this.setData({ faceOn: true })
      my.showToast({ content: e.message || '操作失败', type: 'fail' })
    }
  },

  goMenu() {
    my.switchTab({ url: '/pages/menu/menu' })
  },

  goOrders() {
    my.switchTab({ url: '/pages/orders/orders' })
  }
})
