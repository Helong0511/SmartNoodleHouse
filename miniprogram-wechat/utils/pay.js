/** 微信小程序支付：mock 环境跳过收银台，生产调 wx.requestPayment；支持待确认查单 */

const PENDING_KEY = 'pending_pay'

function isMockPay(payData) {
  if (!payData) return false
  return !!(payData.mock_settled || payData._mock)
}

function savePending(info) {
  if (!info || !info.orderId) return
  try {
    wx.setStorageSync(PENDING_KEY, {
      orderId: String(info.orderId),
      paymentNo: info.paymentNo ? String(info.paymentNo) : '',
      provider: info.provider || 'WECHAT',
      at: Date.now()
    })
  } catch (e) {
    /* ignore */
  }
}

function readPending() {
  try {
    return wx.getStorageSync(PENDING_KEY) || null
  } catch (e) {
    return null
  }
}

function clearPending(orderId) {
  try {
    const cur = readPending()
    if (!orderId || (cur && String(cur.orderId) === String(orderId))) {
      wx.removeStorageSync(PENDING_KEY)
    }
  } catch (e) {
    /* ignore */
  }
}

/** 向后端主动查单；返回 { paid, data } */
async function confirmPending(api, opts) {
  const o = opts || {}
  const pending = readPending()
  const orderId = o.orderId || (pending && pending.orderId)
  const paymentNo = o.paymentNo || (pending && pending.paymentNo)
  if (!api || (!orderId && !paymentNo)) {
    return { paid: false, data: null }
  }
  try {
    let data = null
    if (paymentNo) {
      data = await api.post(`/payments/${paymentNo}/sync`, {})
    } else {
      data = await api.post(`/payments/orders/${orderId}/sync`, {})
    }
    const paid =
      !!(data && (data.status === 'PAID' || data.payment_status === 'PAID' || data.already_paid))
    if (paid && orderId) clearPending(orderId)
    return { paid, data }
  } catch (e) {
    // P1-12：查单错误不静默吞掉，记录日志并标记 networkError 供调用方区分
    try { console.warn('[confirmPending] 查单失败:', e && (e.message || e)) } catch (_) { }
    return { paid: false, data: null, error: e, networkError: !!(e && (e.status === undefined || e.code === 'NETWORK')) }
  }
}

function requestWechatPay(payData) {
  return new Promise((resolve, reject) => {
    if (!payData) {
      reject(new Error('支付参数为空'))
      return
    }
    if (isMockPay(payData)) {
      resolve({ ok: true, mock: true, payData })
      return
    }
    const { timeStamp, nonceStr, package: pkg, paySign, signType } = payData
    if (!timeStamp || !nonceStr || !pkg || !paySign) {
      reject(new Error('支付参数不完整，请确认已配置微信支付'))
      return
    }
    // signType 必须由后端显式下发：微信支付签名算法可能是 MD5 或 HMAC-SHA256，
    // 静默兜底成 RSA 会让「后端漏传 + 实际用 MD5」的组合直接调起失败，
    // 且错误信息不会指向根因。宁可在这里明确报错，也不猜。
    // 当前后端 wechat_pay_client.build_jsapi_pay_params 固定下发 signType=RSA。
    if (!signType) {
      reject(new Error('支付参数缺少 signType（signType 须由后端下发，不可省略）'))
      return
    }
    wx.requestPayment({
      timeStamp: String(timeStamp),
      nonceStr,
      package: pkg,
      signType,
      paySign,
      success: (res) => resolve({ ok: true, res, payData }),
      fail: (err) => {
        const msg = (err && err.errMsg) || '支付已取消'
        const cancel = /cancel/i.test(msg)
        const e = new Error(cancel ? '您已取消支付' : msg)
        e.payCancel = cancel
        e.payUncertain = !cancel
        e.payData = payData
        reject(e)
      }
    })
  })
}

/**
 * 拉起支付并在成功/不明结果后主动查单，避免回调丢失导致重复扣款或状态滞后。
 * @returns {{ paid: boolean, payRes: object, sync: object|null }}
 */
async function payAndConfirm(api, payRes, orderId) {
  const paymentNo = payRes && payRes.payment_no
  savePending({
    orderId,
    paymentNo,
    provider: (payRes && payRes.provider) || 'WECHAT'
  })
  if (isMockPay(payRes) || (payRes && payRes.status === 'PAID') || (payRes && payRes.already_paid)) {
    clearPending(orderId)
    return { paid: true, payRes, sync: null }
  }
  try {
    await requestWechatPay(payRes)
  } catch (e) {
    const synced = await confirmPending(api, { orderId, paymentNo })
    if (synced.paid) {
      return { paid: true, payRes, sync: synced.data, recovered: true }
    }
    throw e
  }
  const synced = await confirmPending(api, { orderId, paymentNo })
  // 必须以服务端查单为准；收银台 success 仅表示客户端调起成功，不算订单 PAID
  if (synced.paid) {
    clearPending(orderId)
    return { paid: true, confirmed: true, payRes, sync: synced.data }
  }
  return {
    paid: false,
    confirmed: false,
    clientOk: true,
    pendingConfirm: true,
    payRes,
    sync: synced.data
  }
}

module.exports = {
  isMockPay,
  requestWechatPay,
  savePending,
  readPending,
  clearPending,
  confirmPending,
  payAndConfirm
}
