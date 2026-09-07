/** 支付宝小程序支付：mock 环境跳过收银台，生产调 my.tradePay；支持待确认查单 */

const PENDING_KEY = 'pending_pay'

function readStorage(key) {
  try {
    const res = my.getStorageSync({ key })
    return res && Object.prototype.hasOwnProperty.call(res, 'data') ? res.data : res
  } catch (e) {
    return null
  }
}

function writeStorage(key, data) {
  try {
    my.setStorageSync({ key, data })
  } catch (e) {
    /* ignore */
  }
}

function removeStorage(key) {
  try {
    my.removeStorageSync({ key })
  } catch (e) {
    /* ignore */
  }
}

function isMockPay(payData) {
  if (!payData) return false
  return !!(payData.mock_settled || payData._mock)
}

function savePending(info) {
  if (!info || !info.orderId) return
  writeStorage(PENDING_KEY, {
    orderId: String(info.orderId),
    paymentNo: info.paymentNo ? String(info.paymentNo) : '',
    provider: info.provider || 'ALIPAY',
    at: Date.now()
  })
}

function readPending() {
  return readStorage(PENDING_KEY) || null
}

function clearPending(orderId) {
  const cur = readPending()
  if (!orderId || (cur && String(cur.orderId) === String(orderId))) {
    removeStorage(PENDING_KEY)
  }
}

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
    return { paid: false, data: null, error: e }
  }
}

function requestAlipayPay(payData) {
  return new Promise((resolve, reject) => {
    if (!payData) {
      reject(new Error('支付参数为空'))
      return
    }
    if (isMockPay(payData)) {
      resolve({ ok: true, mock: true, payData })
      return
    }
    const tradeNO = payData.trade_no || payData.tradeNO
    if (!tradeNO) {
      reject(new Error('支付参数不完整，请确认已配置支付宝支付'))
      return
    }
    my.tradePay({
      tradeNO: String(tradeNO),
      success: (res) => {
        const code = res && String(res.resultCode)
        if (code === '9000') {
          resolve({ ok: true, res, payData })
          return
        }
        // 8000 处理中 / 6004 结果不明：交给查单
        const uncertain = code === '8000' || code === '6004' || code === '99'
        const cancel = code === '6001'
        const e = new Error((res && res.memo) || (cancel ? '您已取消支付' : '支付未完成'))
        e.payCancel = cancel
        e.payUncertain = uncertain || !cancel
        e.payData = payData
        e.resultCode = code
        reject(e)
      },
      fail: (err) => {
        const msg = (err && err.errorMessage) || (err && err.memo) || '支付已取消'
        const cancel = /cancel|6001/i.test(msg)
        const e = new Error(cancel ? '您已取消支付' : msg)
        e.payCancel = cancel
        e.payUncertain = !cancel
        e.payData = payData
        reject(e)
      }
    })
  })
}

async function payAndConfirm(api, payRes, orderId) {
  const paymentNo = payRes && payRes.payment_no
  savePending({
    orderId,
    paymentNo,
    provider: (payRes && payRes.provider) || 'ALIPAY'
  })
  if (isMockPay(payRes) || (payRes && payRes.status === 'PAID') || (payRes && payRes.already_paid)) {
    clearPending(orderId)
    return { paid: true, payRes, sync: null }
  }
  try {
    await requestAlipayPay(payRes)
  } catch (e) {
    const synced = await confirmPending(api, { orderId, paymentNo })
    if (synced.paid) {
      return { paid: true, payRes, sync: synced.data, recovered: true }
    }
    throw e
  }
  const synced = await confirmPending(api, { orderId, paymentNo })
  if (synced.paid) clearPending(orderId)
  return { paid: true, confirmed: !!synced.paid, payRes, sync: synced.data }
}

module.exports = {
  isMockPay,
  requestAlipayPay,
  savePending,
  readPending,
  clearPending,
  confirmPending,
  payAndConfirm
}
