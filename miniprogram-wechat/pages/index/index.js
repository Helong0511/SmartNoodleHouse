const api = require('../../utils/api')
const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const { enableChooseAvatar, enableNicknameInput } = require('../../config/env')
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
    table: null,
    store: null,
    storeName: '',
    storeAddress: '',
    nickname: '',
    avatarUrl: '',
    canChooseAvatar: false,
    useWxNickname: false,
    memberText: '注册会员后系统会智能记录您的口味爱好，支持一键复购；完成注册可领取欢迎券并享受会员等级权益。'
  },

  onLoad(query) {
    this.setData({
      canChooseAvatar: !!enableChooseAvatar,
      useWxNickname: !!enableNicknameInput
    })
    if (query.token) {
      app.globalData.tableToken = query.token
      wx.setStorageSync('table_token', query.token)
    }
    if (query.channel) app.globalData.channel = query.channel
    this.bootstrap()
  },

  onShow() {
    // 页面显示时挂上隐私弹窗，避免冷启动时点击头像无人响应
    if (app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
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
      this.setData({
        loading: false,
        nickname: isPlaceholderNickname(nickname) ? '' : nickname,
        avatarUrl: login.avatar_url || app.globalData.avatarUrl || '',
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

  onAvatarDisabledTap() {
    wx.showModal({
      title: '暂无法获取微信头像',
      content: '请在微信公众平台声明「用户信息（头像昵称）」，正式版还需将 config/env.js 中 ENABLE_WX_PRIVACY_APIS 设为 true。当前可手动输入昵称。',
      showCancel: false
    })
  },

  onChooseAvatar(e) {
    const detail = (e && e.detail) || {}
    const url = detail.avatarUrl || ''
    console.log('[chooseAvatar]', detail)
    if (!url) {
      const errMsg = detail.errMsg || ''
      if (/privacy|authorize|声明|agree/i.test(errMsg)) {
        wx.showModal({
          title: '需要先同意隐私指引',
          content: '请点击同意《用户隐私保护指引》后，再选择微信头像。',
          showCancel: false
        })
        return
      }
      wx.showToast({ title: '未获取到头像，请重试', icon: 'none' })
      return
    }
    this.setData({ avatarUrl: url })
    app.globalData.avatarUrl = url
    wx.setStorageSync('avatar_url', url)
    wx.showToast({ title: '头像已选择', icon: 'success' })
  },

  onNickname(e) {
    const nickname = (e.detail && e.detail.value) || ''
    this.setData({ nickname })
    app.globalData.nickname = nickname
  },

  async onSaveAndGo() {
    if (this.data.saving) return
    const nickname = (this.data.nickname || '').trim()
    if (nickname && (nickname.length < 2 || nickname.length > 20)) {
      wx.showToast({ title: '昵称需 2-20 个字', icon: 'none' })
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
        avatar_url: this.data.avatarUrl || null
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
