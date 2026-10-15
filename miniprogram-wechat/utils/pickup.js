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
      pickupFoot: '',
      hasCustomerDisplay: false,
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
  const hasCustomerDisplay = !!order.has_customer_display
  let pickupHint = ''
  let pickupFoot = ''
  if (showPickupBanner) {
    if (hasCustomerDisplay) {
      if (isReady) {
        pickupHint = isTakeaway
          ? `取餐号 ${code} 已在叫号屏显示，请到取餐台自取`
          : `${label} 已在叫号屏「请取餐」区，可到取餐台自取`
      } else if (isTakeaway) {
        pickupHint = `取餐号 ${code}，请对照大厅叫号屏查看进度`
      } else if (label) {
        pickupHint = `桌号 ${label}，请对照大厅叫号屏查看进度`
      }
      pickupFoot = isTakeaway
        ? '完整订单号末 4 位，与大厅叫号屏一致 · 点击复制'
        : '请在大厅叫号屏对照桌号、取餐号与餐品'
    } else if (isReady) {
      pickupHint = isTakeaway
        ? `取餐号 ${code} 已可取餐，请到取餐台自取`
        : `${label} 已可取餐，可到取餐台自取`
      pickupFoot = isTakeaway ? '完整订单号末 4 位 · 点击复制' : '请到取餐台或等候送餐到桌'
    } else if (isTakeaway) {
      pickupHint = `取餐号 ${code}，请留意取餐进度`
      pickupFoot = '完整订单号末 4 位 · 点击复制'
    } else if (label) {
      pickupHint = `桌号 ${label}，请留意出餐进度`
      pickupFoot = '请等候送餐到桌或到取餐台领取'
    }
  }
  return {
    showPickupBanner,
    pickupCode: code,
    pickupLabel: label,
    pickupSub: sub,
    customerStatusLabel: customerStatus,
    pickupHint,
    pickupFoot,
    hasCustomerDisplay,
    isTakeaway,
    isReady
  }
}

module.exports = {
  decorate,
  pickupCodeFromOrderNo
}
