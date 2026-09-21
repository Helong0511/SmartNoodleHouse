const api = require('../../utils/api')
const auth = require('../../utils/auth')
const payUtil = require('../../utils/pay')
const dineCtx = require('../../utils/context')
const tabCartBadge = require('../../utils/tabCartBadge')

Page({
  data: {
    packages: [],
    selectedId: 0,
    balanceTotal: '0.00',
    balancePrincipal: '0.00',
    balanceGift: '0.00',
    loading: false,
    storeId: null
  },

  onShow() {
    tabCartBadge.syncTabCartBadge()
    this.bootstrap()
  },

  async bootstrap() {
    try {
      await auth.ensureLogin()
      const ctx = dineCtx.currentDisplay() || {}
      const storeId = (ctx.store && ctx.store.id) || appStoreId()
      this.setData({ storeId: storeId || null })
      const [wallet, packages] = await Promise.all([
        api.get('/wallet/me'),
        api.get('/wallet/packages', { store_id: storeId || undefined })
      ])
      this.setData({
        balanceTotal: (wallet && wallet.balance_total) || '0.00',
        balancePrincipal: (wallet && wallet.balance_principal) || '0.00',
        balanceGift: (wallet && wallet.balance_gift) || '0.00',
        packages: Array.isArray(packages) ? packages : [],
        selectedId: (packages && packages[0] && packages[0].id) || 0
      })
    } catch (e) {
      my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
    }
  },

  onSelect(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (id) this.setData({ selectedId: id })
  },

  async submit() {
    if (this.data.loading) return
    const packageId = this.data.selectedId
    const storeId = this.data.storeId
    if (!packageId) {
      my.showToast({ content: '请选择档位', type: 'none' })
      return
    }
    if (!storeId) {
      my.showToast({ content: '请先选择门店或扫桌码', type: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      await auth.ensureLogin()
      const payRes = await api.post('/wallet/recharge', {
        package_id: packageId,
        store_id: storeId,
        provider: 'ALIPAY',
        channel_type: 'miniprogram',
        idempotency_key: `rc${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
      })
      if (payRes && (payRes.status === 'PAID' || payRes.already_paid || payRes.mock_settled)) {
        my.showToast({ content: '充值成功', type: 'success' })
        await this.bootstrap()
        return
      }
      const rechargeId = payRes && payRes.id
      await payUtil.requestAlipayPay(payRes)
      if (rechargeId) {
        const synced = await api.post(`/wallet/recharge/${rechargeId}/sync`, {})
        if (synced && (synced.status === 'PAID' || synced.recharge_status === 'PAID')) {
          my.showToast({ content: '充值成功', type: 'success' })
          await this.bootstrap()
          return
        }
      }
      my.showToast({ content: '支付结果确认中', type: 'none' })
      await this.bootstrap()
    } catch (e) {
      if (!(e && e.payCancel)) {
        my.showToast({ content: (e && e.message) || '充值失败', type: 'fail' })
      }
    } finally {
      this.setData({ loading: false })
    }
  }
})

function appStoreId() {
  try {
    const app = getApp()
    return (app.globalData && app.globalData.storeId) || null
  } catch (_) {
    return null
  }
}
