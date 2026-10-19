/** 订单催单 / 顾客自助退款（订单详情与订单列表共用） */
const api = require('./api')

const URGE_STATUSES = ['CONFIRMED', 'PREPARING', 'COOKING']
const ADD_DISH_STATUSES = ['PAID', 'CONFIRMED', 'PREPARING', 'COOKING', 'READY']
const URGE_COOLDOWN_MS = 5 * 60 * 1000
const ONLINE_CHANNELS = ['WECHAT', 'ALIPAY']

function urgeKey(orderId) {
    return `urge_at_${orderId}`
}

function markUrged(orderId) {
    try {
        my.setStorageSync({ key: urgeKey(orderId), data: Date.now() })
    } catch (_) { }
}

function isUrgeOnCooldown(orderId) {
    try {
        const at = Number(my.getStorageSync({ key: urgeKey(orderId) }).data || 0)
        return at > 0 && Date.now() - at < URGE_COOLDOWN_MS
    } catch (_) {
        return false
    }
}

/** 按钮可见性：{ canUrge, urgeOnCooldown, canSelfRefund, canAddDish } */
function buildActionFlags(order) {
    const o = order || {}
    const status = String(o.status || '').toUpperCase()
    const payStatus = String(o.payment_status || '').toUpperCase()
    const channel = String(o.channel || '').toUpperCase()
    const diningType = String(o.dining_type || '').toUpperCase()
    const isOnline = ONLINE_CHANNELS.indexOf(channel) >= 0
    const isDineIn = diningType !== 'TAKEAWAY' && diningType !== 'DELIVERY'
    return {
        canUrge: URGE_STATUSES.indexOf(status) >= 0,
        urgeOnCooldown: isUrgeOnCooldown(o.id),
        canSelfRefund: payStatus === 'PAID' && status === 'CONFIRMED' && isOnline,
        canAddDish: isDineIn && ADD_DISH_STATUSES.indexOf(status) >= 0
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
