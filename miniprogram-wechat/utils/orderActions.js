/** 订单催单 / 顾客自助退款（订单详情与订单列表共用） */
const api = require('./api')

const URGE_STATUSES = ['CONFIRMED', 'PREPARING', 'COOKING']
const URGE_COOLDOWN_MS = 5 * 60 * 1000
const ONLINE_CHANNELS = ['WECHAT', 'ALIPAY']

function urgeKey(orderId) {
    return `urge_at_${orderId}`
}

function markUrged(orderId) {
    try {
        wx.setStorageSync(urgeKey(orderId), Date.now())
    } catch (_) { }
}

function isUrgeOnCooldown(orderId) {
    try {
        const at = Number(wx.getStorageSync(urgeKey(orderId)) || 0)
        return at > 0 && Date.now() - at < URGE_COOLDOWN_MS
    } catch (_) {
        return false
    }
}

/** 按钮可见性：{ canUrge, urgeOnCooldown, canSelfRefund } */
function buildActionFlags(order) {
    const o = order || {}
    const status = String(o.status || '').toUpperCase()
    const payStatus = String(o.payment_status || '').toUpperCase()
    const channel = String(o.channel || '').toUpperCase()
    const isOnline = ONLINE_CHANNELS.indexOf(channel) >= 0
    return {
        canUrge: URGE_STATUSES.indexOf(status) >= 0,
        urgeOnCooldown: isUrgeOnCooldown(o.id),
        canSelfRefund: payStatus === 'PAID' && status === 'CONFIRMED' && isOnline
    }
}

/** 催单：POST /orders/{id}/urge → { urge_count, urged_at }，本地 5 分钟内置灰 */
async function urgeOrder(orderId) {
    const data = await api.post(`/orders/${orderId}/urge`, {})
    markUrged(orderId)
    return data || {}
}

/** 自助退款：POST /orders/{id}/self-refund → { request_id, status }，40xxx 业务错误由 api 层抛出 message */
async function selfRefund(orderId, reason) {
    return api.post(`/orders/${orderId}/self-refund`, { reason: reason || '顾客自助申请' })
}

module.exports = {
    buildActionFlags,
    urgeOrder,
    selfRefund,
    isUrgeOnCooldown
}
