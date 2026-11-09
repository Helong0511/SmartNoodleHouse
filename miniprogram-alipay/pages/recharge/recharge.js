const api = require('../../utils/api')
const auth = require('../../utils/auth')
const payUtil = require('../../utils/pay')
const dineCtx = require('../../utils/context')
const subscribe = require('../../utils/subscribe')
const rechargeIdempotency = require('../../utils/rechargeIdempotency')

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

/** 支付后查单可能略滞后，短轮询确认入账 */
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

function goMemberAfterSuccess(idempotencyKey) {
  rechargeIdempotency.clearRechargeIdempotencyKey(idempotencyKey)
  markMemberWalletDirty()
  my.showToast({ content: '充值成功', type: 'success' })
  setTimeout(() => {
    my.navigateBack({
      fail: () => {
        my.switchTab({ url: '/pages/member/member' })
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
    canSubscribeBalanceLow: false,
    subscribeLoading: false,
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
      // 仅预加载模板；真正拉起订阅弹窗必须由用户点击按钮触发。
      try {
        const templates = await subscribe.loadTemplates()
        this.setData({ canSubscribeBalanceLow: !!(templates && templates.alipay_balance_low) })
      } catch (_) { this.setData({ canSubscribeBalanceLow: false }) }
    } catch (e) {
      my.showToast({ content: (e && e.message) || '加载失败', type: 'fail' })
    }
  },

  onSelect(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (id) this.setData({ selectedId: id })
  },

  async subscribeBalanceLow() {
    if (this.data.subscribeLoading || !this.data.canSubscribeBalanceLow) return
    // 平台订阅 API 必须在点击回调中立即调用，不能在 bootstrap 异步流程中自动拉起。
    const request = subscribe.requestBalanceLowSubscribe()
    this.setData({ subscribeLoading: true })
    try {
      const result = await request
      my.showToast({ content: result ? '订阅操作已完成' : '未完成订阅，可稍后重试', type: 'none' })
    } catch (_) {
      my.showToast({ content: '未完成订阅，可稍后重试', type: 'none' })
    } finally {
      this.setData({ subscribeLoading: false })
    }
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
      const idempotencyKey = rechargeIdempotency.getRechargeIdempotencyKey(
        auth.readStorage('customer_id'), packageId, storeId
      )
      const payRes = await api.post('/wallet/recharge', {
        package_id: packageId,
        store_id: storeId,
        provider: 'ALIPAY',
        channel_type: 'miniprogram',
        idempotency_key: idempotencyKey
      })
      if (payRes && (payRes.status === 'PAID' || payRes.already_paid)) {
        if (payRes.wallet) applyWalletLocal(this, payRes.wallet)
        else await this.bootstrap()
        markMemberWalletDirty(payRes.wallet)
        goMemberAfterSuccess(idempotencyKey)
        return
      }
      const rechargeId = payRes && payRes.id
      await payUtil.requestAlipayPay(payRes)
      if (!rechargeId) {
        my.showToast({ content: '支付结果确认中', type: 'none' })
        await this.bootstrap()
        return
      }
      const synced = await confirmRechargePaid(rechargeId)
      if (synced && (synced.status === 'PAID' || synced.recharge_status === 'PAID' || synced.already_paid)) {
        if (synced.wallet) applyWalletLocal(this, synced.wallet)
        else await this.bootstrap()
        markMemberWalletDirty(synced.wallet)
        goMemberAfterSuccess(idempotencyKey)
        return
      }
      // 收银台已成功但查单未落账：仍回会员中心，强制下次刷新
      markMemberWalletDirty(synced && synced.wallet)
      my.showToast({ content: '支付已提交，余额稍后更新', type: 'none' })
      setTimeout(() => {
        my.switchTab({ url: '/pages/member/member' })
      }, 600)
    } catch (e) {
      if (!(e && e.payCancel)) {
        my.showToast({ content: (e && e.message) || '充值失败', type: 'fail' })
      }
    } finally {
      this.setData({ loading: false })
    }
  }
})

