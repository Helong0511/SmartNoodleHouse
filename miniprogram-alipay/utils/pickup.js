const ACTIVE = ['PAID', 'CONFIRMED', 'PREPARING', 'COOKING', 'READY']

const STATUS_LABEL = {
  PAID: '排队中',
  CONFIRMED: '排队中',
  PREPARING: '制作中',
  COOKING: '制作中',
  READY: '请取餐'
}

function pickupCodeFromOrderNo(orderNo) {
  const s = String(orderNo || '').trim().toUpperCase()
  if (s.length === 27) return s.slice(23, 27)
  return s.length >= 4 ? s.slice(-4) : (s || '-')
}

function decorate(order) {
  if (!order) {
    return {
      showPickupBanner: false,
      pickupCode: '',
      pickupLabel: '',
      pickupSub: '',
      customerStatusLabel: '',
      pickupHint: '',
      isTakeaway: false,
      isReady: false
    }
  }
  const table = order.table || {}
  const isTakeaway = order.dining_type === 'TAKEAWAY'
  const code = order.pickup_code || pickupCodeFromOrderNo(order.order_no)
  const label = order.pickup_label || (isTakeaway ? code : (table.table_name || table.table_no || ''))
  const sub = order.pickup_sub || (isTakeaway ? '打包自取' : '堂食')
  const status = order.status || ''
  const customerStatus = order.customer_status_label || STATUS_LABEL[status] || ''
  const showPickupBanner = order.show_pickup_banner !== false
    && ACTIVE.includes(status)
    && (order.channel || '') !== 'MEITUAN'
    && (order.channel || '') !== 'ELEME'
  const isReady = status === 'READY'
  let pickupHint = ''
  if (showPickupBanner) {
    if (isReady) {
      pickupHint = isTakeaway
        ? `取餐号 ${code} 已在叫号屏显示，请到取餐台自取`
        : `${label} 已在叫号屏「请取餐」区，可到取餐台自取`
    } else if (isTakeaway) {
      pickupHint = `取餐号 ${code}，请对照大厅叫号屏查看进度`
    } else if (label) {
      pickupHint = `桌号 ${label}，请对照大厅叫号屏查看进度`
    }
  }
  return {
    showPickupBanner,
    pickupCode: code,
    pickupLabel: label,
    pickupSub: sub,
    customerStatusLabel: customerStatus,
    pickupHint,
    isTakeaway,
    isReady
  }
}

module.exports = {
  decorate,
  pickupCodeFromOrderNo
}
