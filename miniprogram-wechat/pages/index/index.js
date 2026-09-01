const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const privacy = require('../../utils/privacy')
const {
  enableChooseAvatar,
  enableNicknameInput,
  enableWxPhoneBind
} = require('../../config/env')
const app = getApp()

const PLACEHOLDER_NICKS = new Set(['联调员', '面馆会员', '微信用户', '支付宝用户'])

function isPlaceholderNickname(name) {
  const n = String(name || '').trim()
  return !n || PLACEHOLDER_NICKS.has(n)
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
    store: null,
    storeName: '',
    storeAddress: '',
    nickname: '',
    avatarUrl: '',
    phone: '',
    phoneInput: '',
    canChooseAvatar: false,
    useWxNickname: false,
    canWxPhone: false,
    privacyTip: '',
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购；完成注册可领取欢迎券并享受会员等级权益。'
  },

  onLoad(query) {
    this.setData({
      canChooseAvatar: !!enableChooseAvatar,
      useWxNickname: !!enableNicknameInput,
      canWxPhone: !!enableWxPhoneBind
    })
    if (query.token) {
      app.globalData.tableToken = query.token
      wx.setStorageSync('table_token', query.token)
    }
    if (query.channel) app.globalData.channel = query.channel
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
    this.setData({
      needPrivacyAgree: need,
      privacyTip: need
        ? '请先点击「同意隐私指引」，再获取微信头像/昵称/手机号；也可手输昵称与手机号，或从相册选头像。'
        : '微信头像、昵称需分开点选；手机号可一键授权。若微信接口不可用，请用手输或相册。'
    })
    if (need) {
      // 主动触发，配合 privacy-popup / 页内同意按钮
      privacy.requirePrivacyAuthorize()
    }
    return !need
  },

  openPrivacyContract() {
    privacy.openPrivacyContract()
  },

  onPrivacyAgree() {
    const resolve = app.globalData && app.globalData.privacyResolve
    if (app.globalData) {
      app.globalData.needPrivacyPopup = false
      app.globalData.privacyResolve = null
    }
    if (app._privacyPopup && app._privacyPopup.hide) app._privacyPopup.hide()
    if (typeof resolve === 'function') {
      resolve({ buttonId: 'agree-btn', event: 'agree' })
    }
    this.setData({
      needPrivacyAgree: false,
      privacyTip: '已同意隐私指引。请点头像选微信头像，点输入框取微信昵称，并可授权手机号。'
    })
    wx.showToast({ title: '已同意，请继续授权', icon: 'success' })
  },

  async bootstrap() {
    try {
      this.setData({ loading: true })
      const token = app.globalData.tableToken || wx.getStorageSync('table_token')
      if (token) {
        try {
          await dineCtx.bindTableFromToken(token)
        } catch (_) {
          this.setData({ hasTable: false, table: null })
        }
      }
      const login = await auth.ensureLogin(token)
      if (!login) {
        throw new Error('登录失败，请检查网络或稍后重试')
      }
      const nickname = login.nickname || app.globalData.nickname || ''
      const showMemberPrompt = !!login.need_member_prompt || isPlaceholderNickname(nickname)
      const ctx = dineCtx.currentDisplay()
      let phone = ''
      try {
        const me = await api.get('/miniprogram/customers/me').catch(() => null)
        if (me && me.phone) phone = me.phone
      } catch (_) {}
      this.setData({
        loading: false,
        nickname: isPlaceholderNickname(nickname) ? '' : nickname,
        avatarUrl: login.avatar_url || app.globalData.avatarUrl || '',
        phone,
        showMemberPrompt,
        ready: true,
        table: ctx.table || app.globalData.table,
        store: ctx.store || app.globalData.store,
        storeName: ctx.storeName,
        storeAddress: ctx.storeAddress,
        hasTable: ctx.hasTable
      })
      if (login.nickname && !isPlaceholderNickname(login.nickname)) {
        wx.setStorageSync('nickname', login.nickname)
      }
      if (login.avatar_url) {
        app.globalData.avatarUrl = login.avatar_url
        wx.setStorageSync('avatar_url', login.avatar_url)
      }
      if (login.customer_id) {
        app.globalData.customerId = login.customer_id
        wx.setStorageSync('customer_id', login.customer_id)
      }
      if (login.customer_no) wx.setStorageSync('customer_no', login.customer_no)
      await this.refreshPrivacyState()
    } catch (e) {
      this.setData({ loading: false, ready: true })
      const msg = (e && e.message) || '登录失败'
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
            storeName: ctx.storeName,
            storeAddress: ctx.storeAddress,
            hasTable: true
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
    console.log('[chooseAvatar]', detail)
    if (!url) {
      const errMsg = String(detail.errMsg || '')
      if (/privacy|authorize|声明|agree|scope/i.test(errMsg)) {
        this.setData({ needPrivacyAgree: true })
        privacy.requirePrivacyAuthorize()
        wx.showModal({
          title: '无法获取微信头像',
          content: '请先同意隐私指引；若仍失败，请确认公众平台已声明「用户信息」，或改用「从相册选择头像」。',
          showCancel: false
        })
        return
      }
      // 微信头像接口失败时自动降级相册
      this.onPickLocalAvatar()
      return
    }
    this.setData({ avatarUrl: url })
    app.globalData.avatarUrl = url
    wx.setStorageSync('avatar_url', url)
    wx.showToast({ title: '头像已选择', icon: 'success' })
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
            this.setData({ avatarUrl: url })
            app.globalData.avatarUrl = url
            wx.setStorageSync('avatar_url', url)
            wx.showToast({ title: '头像已选择', icon: 'success' })
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
          this.setData({ avatarUrl: url })
          app.globalData.avatarUrl = url
          wx.setStorageSync('avatar_url', url)
          wx.showToast({ title: '头像已选择', icon: 'success' })
        }
      })
    }
    if (this.data.needPrivacyAgree) {
      privacy.requirePrivacyAuthorize().then((ok) => {
        if (ok) this.setData({ needPrivacyAgree: false })
        pick()
      })
      return
    }
    pick()
  },

  onNickname(e) {
    const nickname = (e.detail && e.detail.value) || ''
    this.setData({ nickname })
    app.globalData.nickname = nickname
  },

  onPhoneInput(e) {
    this.setData({ phoneInput: (e.detail && e.detail.value) || '' })
  },

  async onGetPhoneNumber(e) {
    const detail = (e && e.detail) || {}
    console.log('[getPhoneNumber]', detail)
    if (detail.errMsg && detail.errMsg.indexOf('privacy') >= 0) {
      this.setData({ needPrivacyAgree: true })
      privacy.requirePrivacyAuthorize()
      wx.showModal({
        title: '需先同意隐私指引',
        content: '请先点击「同意隐私指引」。若仍失败，请在公众平台声明「手机号」，或手动输入手机号。',
        showCancel: false
      })
      return
    }
    if (!detail.code) {
      if (detail.errMsg && /deny|cancel/i.test(detail.errMsg)) {
        wx.showToast({ title: '已取消手机号授权', icon: 'none' })
      } else {
        wx.showToast({ title: '未拿到手机号，请手输', icon: 'none' })
      }
      return
    }
    try {
      wx.showLoading({ title: '绑定中' })
      const res = await api.post('/miniprogram/member/phone', { phone_code: detail.code })
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
  },

  async onSaveAndGo() {
    if (this.data.saving) return
    const nickname = (this.data.nickname || '').trim()
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
      const res = await api.post('/miniprogram/member/register', {
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
      wx.setStorageSync('nickname', app.globalData.nickname)
      if (app.globalData.avatarUrl) wx.setStorageSync('avatar_url', app.globalData.avatarUrl)
      this.setData({
        nickname: app.globalData.nickname,
        phone: (res && res.phone) || this.data.phone || phoneInput || '',
        phoneInput: '',
        showMemberPrompt: false,
        saving: false
      })
      wx.hideLoading()
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
