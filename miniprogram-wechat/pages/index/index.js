const auth = require('../../utils/auth')
const dineCtx = require('../../utils/context')
const { normalizeLaunchQuery } = require('../../utils/scan')

/**
 * 扫码落地页：只做「识别桌台 + 静默登录」，随后直接进入点餐页。
 * 不收集头像/昵称/手机号，会员开通与手机号绑定统一在「会员」页完成。
 * 弱网兜底 6 秒：即使登录未完成也以游客身份进入菜单（下单前各页会再次 ensureLogin）。
 * 12 秒对「扫码 → 点餐」太长；6 秒足够覆盖正常弱网，断网时提前进菜单更好。
 */
const BOOT_FALLBACK_MS = 6000

Page({
  data: {
    storeName: '',
    tableName: '',
    seatLabel: '桌号',
    hasTable: false
  },

  onLoad(query) {
    const app = auth.getAppSafe()
    const q = normalizeLaunchQuery(query)
    if (q.token && app) {
      app.globalData.tableToken = q.token
      wx.setStorageSync('table_token', q.token)
    }
    if (q.channel && app) app.globalData.channel = q.channel
    // IDE/真机：补一次启动参数（Scheme 打开时 onLoad query 偶发为空）
    try {
      if (app && typeof app.parseLaunch === 'function') {
        const launch = (wx.getLaunchOptionsSync && wx.getLaunchOptionsSync()) || {}
        app.parseLaunch(launch)
      }
    } catch (_) {}
    this.bootstrap()
  },

  onShow() {
    const app = auth.getAppSafe()
    if (app && app._privacyPopup && app.globalData.needPrivacyPopup) {
      app._privacyPopup.show()
    }
  },

  async bootstrap() {
    const app = auth.getAppSafe()
    const token = (app && app.globalData.tableToken) || auth.readStorage('table_token') || ''

    // 绑桌（公开接口）与静默登录并行；两者失败都不阻断进入点餐
    const tablePromise = token
      ? dineCtx.bindTableFromToken(token).catch((e) => {
        console.warn('[bootstrap table]', auth.formatError(e, '绑桌失败'))
      })
      : Promise.resolve()
    const loginPromise = auth.ensureLogin(token).catch((e) => {
      console.warn('[bootstrap login]', auth.formatError(e, '登录未完成'))
    })

    let fromFallback = false
    const fallback = new Promise((resolve) => {
      setTimeout(() => {
        fromFallback = true
        resolve()
      }, BOOT_FALLBACK_MS)
    })
    await Promise.race([Promise.all([tablePromise, loginPromise]), fallback])

    const dine = dineCtx.currentDisplay()
    this.setData(dineCtx.pageDineFields(dine))

    if (fromFallback || !app || !app.globalData.customerToken) {
      wx.showToast({ title: '网络较慢，可先浏览点餐', icon: 'none', duration: 2000 })
    }
    this.goMenu()
  },

  goMenu() {
    wx.switchTab({
      url: '/pages/menu/menu',
      fail: () => wx.reLaunch({ url: '/pages/menu/menu' })
    })
  }
})
