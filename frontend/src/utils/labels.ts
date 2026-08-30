/** 后台展示用：枚举 → 中文 */

export const ORDER_STATUS: Record<string, string> = {
  PENDING_PAYMENT: '待支付',
  PAID: '已支付',
  CONFIRMED: '已确认',
  PREPARING: '制作中',
  COOKING: '烹饪中',
  READY: '待取餐',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

export const PAYMENT_STATUS: Record<string, string> = {
  UNPAID: '未支付',
  PAID: '已支付',
  REFUNDING: '退款中',
  REFUNDED: '已退款',
  PARTIAL_REFUND: '部分退款',
  CANCELLED: '已取消',
  FAILED: '失败',
}

/** 退款单 payment_refunds.status */
export const PAYMENT_REFUND_STATUS: Record<string, string> = {
  PENDING: '待处理',
  PROCESSING: '处理中',
  SUCCESS: '成功',
  FAILED: '失败',
  CLOSED: '已关闭',
  ABNORMAL: '异常',
}

export const PRODUCT_STATUS: Record<string, string> = {
  ON_SALE: '在售',
  OFF_SALE: '下架',
}

export const CHANNEL: Record<string, string> = {
  WECHAT: '微信堂食',
  ALIPAY: '支付宝堂食',
  MEITUAN: '美团外卖',
  ELEME: '饿了么',
  H5: 'H5点餐',
}

export const PAYMENT_PROVIDER: Record<string, string> = {
  WECHAT: '微信支付',
  ALIPAY: '支付宝',
  MOCK: '模拟支付',
}

export const DELIVERY_STATUS: Record<string, string> = {
  PENDING: '待接单',
  ACCEPTED: '已接单',
  CONFIRMED: '已接单',
  PREPARING: '备餐中',
  READY: '待取餐/待配送',
  DELIVERING: '配送中',
  DONE: '已完成',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

export const LOGISTICS_STATUS: Record<string, string> = {
  PENDING: '待调度',
  CONFIRMED: '已确认',
  RIDER_TAKE: '骑手已接单',
  PICKUP: '已取餐',
  DELIVERING: '配送中',
  DELIVERED: '已送达',
  CANCELLED: '已取消',
  EXCEPTION: '异常',
}

export const DELIVERY_REFUND_STATUS: Record<string, string> = {
  NONE: '无',
  PENDING: '待处理',
  APPROVED: '已同意',
  REJECTED: '已拒绝',
  SUCCESS: '已退款',
  FAILED: '失败',
}

export const REFUND_APPLY_STATUS: Record<string, string> = {
  PENDING: '待审核',
  APPROVED: '已同意',
  REJECTED: '已拒绝',
  SUCCESS: '已完成',
  FAILED: '失败',
}

export function deliveryStatusTagType(status: string) {
  const m: Record<string, string> = {
    PENDING: 'danger',
    ACCEPTED: 'warning',
    CONFIRMED: 'warning',
    PREPARING: '',
    READY: 'success',
    DELIVERING: '',
    DONE: 'success',
    COMPLETED: 'success',
    CANCELLED: 'info',
  }
  return m[status] || 'info'
}

export function label(map: Record<string, string>, key: string | null | undefined, fallback = '-') {
  if (!key) return fallback
  return map[key] || key
}

export function orderStatusTagType(status: string) {
  const m: Record<string, string> = {
    PENDING_PAYMENT: 'info',
    PAID: 'warning',
    CONFIRMED: 'warning',
    PREPARING: '',
    COOKING: '',
    READY: 'success',
    COMPLETED: 'success',
    CANCELLED: 'danger',
  }
  return m[status] || 'info'
}

export function paymentStatusTagType(status: string) {
  const m: Record<string, string> = {
    UNPAID: 'info',
    PAID: 'success',
    REFUNDING: 'warning',
    REFUNDED: 'danger',
    PARTIAL_REFUND: 'warning',
    CANCELLED: 'info',
    FAILED: 'danger',
  }
  return m[status] || 'info'
}

export function paymentRefundStatusTagType(status: string) {
  const m: Record<string, string> = {
    PENDING: 'info',
    PROCESSING: 'warning',
    SUCCESS: 'success',
    FAILED: 'danger',
    CLOSED: 'info',
    ABNORMAL: 'danger',
  }
  return m[status] || 'info'
}

/** 后台可操作的下一状态（与 state machine / KDS 一致） */
export const ORDER_NEXT_ACTIONS: Record<string, { status: string; label: string; type: string }[]> = {
  PAID: [
    { status: 'PREPARING', label: '开始制作', type: 'warning' },
    { status: 'CONFIRMED', label: '确认订单', type: 'primary' },
  ],
  CONFIRMED: [
    { status: 'PREPARING', label: '开始制作', type: 'warning' },
    { status: 'COOKING', label: '直接烹饪', type: '' },
  ],
  PREPARING: [
    { status: 'COOKING', label: '进入烹饪', type: '' },
    { status: 'READY', label: '跳过烹饪', type: 'success' },
  ],
  COOKING: [{ status: 'READY', label: '制作完成', type: 'success' }],
  READY: [{ status: 'COMPLETED', label: '完成出餐', type: 'success' }],
}

/** KDS 主操作按钮（每状态一个主按钮 + 可选次要） */
export const KDS_ACTIONS: Record<string, { status: string; label: string; type: string; plain?: boolean }[]> = {
  PAID: [{ status: 'PREPARING', label: '开始制作', type: 'warning' }],
  CONFIRMED: [{ status: 'PREPARING', label: '开始制作', type: 'warning' }],
  PREPARING: [
    { status: 'COOKING', label: '进入烹饪', type: 'primary' },
    { status: 'READY', label: '跳过烹饪', type: 'success', plain: true },
  ],
  COOKING: [{ status: 'READY', label: '制作完成', type: 'success' }],
  READY: [{ status: 'COMPLETED', label: '出餐', type: 'primary' }],
}

export function cancelAllowed(status: string) {
  return ['PENDING_PAYMENT', 'PAID', 'CONFIRMED', 'PREPARING', 'COOKING', 'READY'].includes(status)
}
