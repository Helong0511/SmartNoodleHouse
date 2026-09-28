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
  REFUND_PENDING: '退款中',
  REFUNDED: '已退款',
}

export const PAYMENT_STATUS: Record<string, string> = {
  UNPAID: '未支付',
  PARTIAL_PAID: '部分支付',
  PAID: '已支付',
  REFUNDING: '退款中',
  REFUNDED: '已退款',
  PARTIAL_REFUND: '部分退款',
  CANCELLED: '已取消',
  FAILED: '失败',
  UNKNOWN: '状态未知',
}

/** 退款单 payment_refunds.status */
export const PAYMENT_REFUND_STATUS: Record<string, string> = {
  PENDING: '待处理',
  PROCESSING: '处理中',
  SUCCESS: '成功',
  FAILED: '失败',
  CLOSED: '已关闭',
  ABNORMAL: '异常',
  UNKNOWN: '状态未知',
}

/** 顾客持券 customer_coupons.status */
export const COUPON_STATUS: Record<string, string> = {
  AVAILABLE: '可用',
  RESERVED: '下单预占',
  USED: '已使用',
  EXPIRED: '已过期',
}

/** 券模板类型 coupon_templates.coupon_type */
export const COUPON_TYPE: Record<string, string> = {
  FIXED: '满减券',
  PERCENT: '折扣券',
  VOUCHER: '代金券',
}

/** 支付流水 channel（通道形态，非渠道品牌） */
export const PAYMENT_CHANNEL: Record<string, string> = {
  jsapi: '小程序支付',
  miniprogram: '小程序支付',
  native: '扫码支付',
  qr: '扫码支付',
  barcode: '付款码',
  micropay: '付款码',
  pos: '收银POS',
  POS: '收银POS',
  RECHARGE: '充值',
  CASH: '现金',
  BALANCE: '余额',
  MOCK: '模拟',
}

/** 摄像头类型 / 在线状态 / 访客事件 */
export const CAMERA_TYPE: Record<string, string> = {
  USB: 'USB 摄像头',
  RTSP: '网络摄像头(RTSP)',
}

export const CAMERA_STATUS: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  RECONNECTING: '重连中',
  DISABLED: '停用',
}

export const VISITOR_EVENT_TYPE: Record<string, string> = {
  ENTER: '进店',
  LEAVE: '离店',
  MATCH: '识别匹配',
  UNKNOWN: '未知访客',
  CAPTURE: '抓拍',
}

/** 厨房打印任务 PrintJob.status */
export const PRINT_JOB_STATUS: Record<string, string> = {
  PENDING: '待打印',
  PRINTING: '打印中',
  SUCCESS: '已打印',
  RETRYING: '待重试',
  FAILED: '打印失败',
}

/** 外卖回调 / 同步日志常见 event_type */
export const DELIVERY_LOG_EVENT: Record<string, string> = {
  ORDER_NEW: '新订单',
  ORDER_CONFIRMED: '订单确认',
  ORDER_CANCEL: '订单取消',
  ORDER_REFUND: '订单退款',
  REFUND: '退款',
  LOGISTICS: '物流更新',
  STATUS: '状态变更',
  UNKNOWN: '未知事件',
}

/** 操作审计 action → 中文（未知 action 原样显示） */
export const AUDIT_ACTION: Record<string, string> = {
  'ab.experiment.delete': '删除 AB 实验',
  'ab.experiment.upsert': '保存 AB 实验',
  'auth.login': '员工登录',
  'auth.login_failed': '员工登录失败',
  'camera.create': '新增摄像头',
  'camera.delete': '删除摄像头',
  'camera.start': '启动摄像头',
  'camera.stop': '停止摄像头',
  'camera.update': '更新摄像头',
  'coupon.issue': '手动发券',
  'coupon.template.create': '创建券模板',
  'coupon.template.status': '启停券模板',
  'coupon.template.update': '更新券模板',
  'customer.logout': '顾客退出登录',
  'customer.member.activate': '激活会员',
  'customer.member.avatar_import': '导入会员头像',
  'customer.member.avatar_upload': '上传会员头像',
  'customer.member.phone_bind': '绑定手机号',
  'customer.member.profile_update': '更新会员资料',
  'customer.member.register': '注册会员',
  'customer.merge': '合并顾客',
  'customer.table.bind': '绑定桌台',
  'delivery.binding.auto_sync_open': '外卖绑定自动同步营业',
  'delivery.binding.create': '创建外卖门店绑定',
  'delivery.binding.delete': '删除外卖门店绑定',
  'delivery.binding.refresh_token': '刷新外卖 Token',
  'delivery.binding.status': '启停外卖绑定',
  'delivery.business_hours.update': '更新外卖营业时间',
  'delivery.category_map.create': '创建外卖分类映射',
  'delivery.category_map.delete': '删除外卖分类映射',
  'delivery.order.cancel': '取消外卖单',
  'delivery.order.confirm': '确认外卖单',
  'delivery.product_map.create': '创建外卖商品映射',
  'delivery.refund.action': '处理外卖退款',
  'delivery.store.close': '外卖关店',
  'delivery.store.open': '外卖开店',
  'delivery.sync.menu': '同步外卖菜单',
  'delivery.sync.menu_batch': '批量同步外卖菜单',
  'delivery.sync.prices': '同步外卖价格',
  'delivery.sync.product': '同步外卖商品',
  'delivery.sync.reconcile': '外卖对账同步',
  'delivery.sync.shelf': '同步外卖上下架',
  'delivery.sync.stock': '同步外卖库存',
  'delivery.sync_alert.resolve': '处理外卖同步告警',
  'face.consent': '人脸授权同意',
  'face.revoke': '撤销人脸授权',
  'intrusion.detected': '检测到异常访问',
  'member_level.update': '更新会员等级',
  'order.add_items': '订单加菜',
  'order.create': '创建订单',
  'order.create_pos': 'POS 开单',
  'order.kitchen_takeover': '厨房接管订单',
  'order.print': '打印订单小票',
  'order.reorder': '再来一单',
  'order.transfer_table': '转台',
  'order.update_status': '更新订单状态',
  'payment.cash': '现金收款',
  'payment.pay': '发起支付',
  'payment.pos_scan': 'POS 扫码支付',
  'payment.refund': '支付退款',
  'payment.sync': '同步支付状态',
  'pos.refund.apply': '申请 POS 退款',
  'pos.refund.approve': '批准 POS 退款',
  'pos.refund.cancel': '撤销 POS 退款申请',
  'pos.refund.reject': '拒绝 POS 退款',
  'pos.shift.close': 'POS 交班',
  'pos.shift.open': 'POS 开班',
  'print_job.retry': '重试打印任务',
  'privacy.clear_taste_profile': '清除口味画像',
  'privacy.consent': '隐私同意',
  'privacy.update_opt_in': '更新隐私选项',
  'product.category.create': '创建分类',
  'product.category.update': '更新分类',
  'product.category_schedule.auto_refresh': '刷新分类时段',
  'product.category_schedule.auto_refresh_store': '刷新门店分类时段',
  'product.category_schedule.create': '创建分类时段',
  'product.category_schedule.delete': '删除分类时段',
  'product.category_schedule.update': '更新分类时段',
  'product.create': '创建商品',
  'product.delete': '删除商品',
  'product.image.upload': '上传商品图',
  'product.menu.copy': '复制菜单到门店',
  'product.option.create': '创建规格选项',
  'product.option.delete': '删除规格选项',
  'product.option.set_default': '设置默认规格',
  'product.option.update': '更新规格选项',
  'product.option_group.create': '创建规格组',
  'product.option_group.delete': '删除规格组',
  'product.option_group.update': '更新规格组',
  'product.stock.delete': '删除库存',
  'product.stock.upsert': '更新库存',
  'product.taste_profile.rebuild': '重建口味画像',
  'product.taste_profile.update': '更新口味画像',
  'product.update': '更新商品',
  'role.permissions.update': '更新角色权限',
  'settings.business.update': '更新业务参数',
  'settings.scheduler.update': '更新定时任务',
  'settings.store.logo_upload': '上传门店 Logo',
  'settings.store.update': '更新门店信息',
  'store.business.clear': '清除门店业务覆盖',
  'store.business.update': '更新门店业务参数',
  'store.create': '创建门店',
  'table.create': '创建桌台',
  'table.regenerate_qr': '重新生成桌台码',
  'table.update': '更新桌台',
  'user.create': '创建员工',
  'user.logout': '员工退出',
  'user.permission_change': '变更员工权限',
  'user.reset_password': '重置员工密码',
  'user.update': '更新员工',
  'wallet.adjust': '储值调账',
  'wallet.package.create': '创建充值套餐',
  'wallet.package.update': '更新充值套餐',
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
  POS: '收银POS',
  CASH: '现金',
  MIXED: '组合支付',
  RECHARGE: '充值',
}

export const PAYMENT_PROVIDER: Record<string, string> = {
  WECHAT: '微信支付',
  ALIPAY: '支付宝',
  CASH: '现金',
  MIXED: '组合支付',
  MOCK: '模拟支付',
  BALANCE: '余额支付',
  MEITUAN: '美团',
  ELEME: '饿了么',
}

/** 财务账本分录类型 */
export const LEDGER_ENTRY_TYPE: Record<string, string> = {
  SALE: '收款',
  REFUND: '退款',
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
  if (key == null || key === '') return fallback
  const raw = String(key)
  if (map[raw]) return map[raw]
  const upper = raw.toUpperCase()
  if (map[upper]) return map[upper]
  const lower = raw.toLowerCase()
  if (map[lower]) return map[lower]
  return raw
}

export function couponStatusTagType(status: string) {
  const m: Record<string, string> = {
    AVAILABLE: 'success',
    RESERVED: 'warning',
    USED: 'info',
    EXPIRED: 'info',
  }
  return m[String(status || '').toUpperCase()] || 'info'
}

export function cameraStatusTagType(status: string) {
  const m: Record<string, string> = {
    ONLINE: 'success',
    OFFLINE: 'info',
    RECONNECTING: 'warning',
    DISABLED: 'info',
  }
  return m[String(status || '').toUpperCase()] || 'info'
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
    PARTIAL_PAID: 'warning',
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
  PENDING_PAYMENT: [{ status: 'CONFIRMED', label: '接单制作', type: 'warning' }],
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
