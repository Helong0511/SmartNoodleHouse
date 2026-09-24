const api = require('../../utils/api')
const auth = require('../../utils/auth')
const payUtil = require('../../utils/pay')
const dineCtx = require('../../utils/context')

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function markMemberWalletDirty(wallet) {
  try {
    const app = getApp()
    if (!app || !app.globalData) return
    app.globalData.walletDirty = true
    app.globalData.prefsAt = 0
    if (wallet && wallet.balance_total != null) {
      app.globalData.walletSnapshot = wallet
    }
  } catch (_) { /* ignore */ }
}

function applyWalletLocal(page, wallet) {
  if (!wallet) return
  page.setData({
    balanceTotal: wallet.balance_total || '0.00',
    balancePrincipal: wallet.balance_principal || '0.00',
    balanceGift: wallet.balance_gift || '0.00'
  })
}

async function confirmRechargePaid(rechargeId, maxTries) {
  const n = maxTries || 8
  let last = null
  for (let i = 0; i < n; i++) {
    last = await api.post(`/wallet/recharge/${rechargeId}/sync`, {})
    if (last && (last.status === 'PAID' || last.recharge_status === 'PAID' || last.already_paid)) {
      return last
    }
    await sleep(700 + i * 200)
  }
  return last
}

function goMemberAfterSuccess() {
  markMemberWalletDirty()
  wx.showToast({ title: '充值成功', icon: 'success' })
  setTimeout(() => {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: '/pages/member/member' })
      }
    })
  }, 500)
}

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
    this.bootstrap()
  },

  async bootstrap() {
    try {
      await auth.ensureLogin()
      const ctx = dineCtx.currentDisplay() || {}
      const storeId = ctx.storeId || (ctx.store && ctx.store.id) || null
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
      wx.showToast({ title: (e && e.message) || '加载失败', icon: 'none' })
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
      wx.showToast({ title: '请选择档位', icon: 'none' })
      return
    }
    if (!storeId) {
      wx.showToast({ title: '请先选择门店或扫桌码', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      await auth.ensureLogin()
      const payRes = await api.post('/wallet/recharge', {
        package_id: packageId,
        store_id: storeId,
        provider: 'WECHAT',
        channel_type: 'miniprogram',
        idempotency_key: `rc${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
      })
      if (payRes && (payRes.status === 'PAID' || payRes.already_paid || payRes.mock_settled)) {
        if (payRes.wallet) applyWalletLocal(this, payRes.wallet)
        else await this.bootstrap()
        markMemberWalletDirty(payRes.wallet)
        goMemberAfterSuccess()
        return
      }
      const rechargeId = payRes && payRes.id
      await payUtil.requestWechatPay(payRes)
      if (!rechargeId) {
        wx.showToast({ title: '支付结果确认中', icon: 'none' })
        await this.bootstrap()
        return
      }
      const synced = await confirmRechargePaid(rechargeId)
      if (synced && (synced.status === 'PAID' || synced.recharge_status === 'PAID' || synced.already_paid)) {
        if (synced.wallet) applyWalletLocal(this, synced.wallet)
        else await this.bootstrap()
        markMemberWalletDirty(synced.wallet)
        goMemberAfterSuccess()
        return
      }
      markMemberWalletDirty(synced && synced.wallet)
      wx.showToast({ title: '支付已提交，余额稍后更新', icon: 'none' })
      setTimeout(() => {
        wx.navigateBack({
          fail: () => wx.switchTab({ url: '/pages/member/member' })
        })
      }, 600)
    } catch (e) {
      if (!(e && e.payCancel)) {
        wx.showToast({ title: (e && e.message) || '充值失败', icon: 'none' })
      }
    } finally {
      this.setData({ loading: false })
    }
  }
})

