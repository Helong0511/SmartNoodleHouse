import { createRouter, createWebHistory } from 'vue-router'
import { getStaffToken } from '@/utils/staffAuth'

const CASHIER_ALLOWED = new Set(['/pos-terminal', '/pos/my-stats', '/login'])

function cashierMayAccess(path: string) {
  if (CASHIER_ALLOWED.has(path)) return true
  return false
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/Login.vue') },
    {
      path: '/',
      component: () => import('@/layouts/AdminLayout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '数据概览', permission: 'order:read', denyCashier: true } },
        { path: 'categories', component: () => import('@/views/Categories.vue'), meta: { title: '分类管理', permission: 'product:read', denyCashier: true } },
        { path: 'category-schedules', component: () => import('@/views/CategorySchedules.vue'), meta: { title: '分类时段排序', permission: 'product:read', denyCashier: true } },
        { path: 'options', component: () => import('@/views/Options.vue'), meta: { title: '规格管理', permission: 'product:read', denyCashier: true } },
        { path: 'products', component: () => import('@/views/Products.vue'), meta: { title: '餐品管理', permission: 'product:read', denyCashier: true } },
        { path: 'orders', component: () => import('@/views/Orders.vue'), meta: { title: '订单管理', anyPermission: ['order:read', 'payment:read'], denyCashier: true } },
        { path: 'queue-manage', component: () => import('@/views/QueueManage.vue'), meta: { title: '排队管理', permission: 'table:manage', denyCashier: true } },
        { path: 'invoices', component: () => import('@/views/InvoiceRequests.vue'), meta: { title: '发票申请', permission: 'invoice:manage', denyCashier: true } },
        { path: 'orders/:id', component: () => import('@/views/OrderDetail.vue'), meta: { title: '订单详情', permission: 'order:read', denyCashier: true } },
        { path: 'customers', component: () => import('@/views/Customers.vue'), meta: { title: '顾客管理', permission: 'customer:read', denyCashier: true } },
        { path: 'member-levels', component: () => import('@/views/MemberLevels.vue'), meta: { title: '会员等级', permission: 'customer:read', denyCashier: true } },
        { path: 'wallet', component: () => import('@/views/Wallet.vue'), meta: { title: '会员储值', permission: 'wallet:read', denyCashier: true } },
        { path: 'coupons', component: () => import('@/views/Coupons.vue'), meta: { title: '优惠券管理', permission: 'coupon:read', denyCashier: true } },
        { path: 'social-benefits', component: () => import('@/views/SocialBenefits.vue'), meta: { title: '社会贡献', permission: 'customer:read', denyCashier: true } },
        { path: 'marketing/segments', component: () => import('@/views/MarketingSegments.vue'), meta: { title: '客户分群', permission: 'customer:read', denyCashier: true } },
        { path: 'marketing/campaigns', component: () => import('@/views/MarketingCampaigns.vue'), meta: { title: '营销活动', permission: 'coupon:read', denyCashier: true } },
        { path: 'marketing/flash-sales', component: () => import('@/views/MarketingFlashSales.vue'), meta: { title: '限量抢购', permission: 'coupon:read', denyCashier: true } },
        { path: 'marketing/report', component: () => import('@/views/MarketingReport.vue'), meta: { title: '营销报表', permission: 'coupon:read', denyCashier: true } },
        { path: 'tables', component: () => import('@/views/Tables.vue'), meta: { title: '桌台管理', permission: 'table:manage', denyCashier: true } },
        { path: 'users', component: () => import('@/views/Users.vue'), meta: { title: '员工管理', permission: 'user:read', denyCashier: true } },
        { path: 'role-permissions', component: () => import('@/views/RolePermissions.vue'), meta: { title: '角色权限', permission: 'system:manage', denyCashier: true } },
        { path: 'store-stocks', component: () => import('@/views/StoreStocks.vue'), meta: { title: '分店库存', permission: 'product:read', denyCashier: true } },
        // 支付记录已并入订单管理 Tab；旧链接保留跳转
        { path: 'payments', redirect: { path: '/orders', query: { tab: 'payments' } } },
        { path: 'financial-ledger', component: () => import('@/views/FinancialLedger.vue'), meta: { title: '财务账本', permission: 'payment:read', denyCashier: true } },
        { path: 'payment-reconcile', component: () => import('@/views/PaymentReconcile.vue'), meta: { title: '支付对账', anyPermission: ['reconcile:read', 'reconcile:manage'], denyCashier: true } },
        { path: 'order-archive', component: () => import('@/views/OrderArchive.vue'), meta: { title: '订单归档', permission: 'archive:manage', denyCashier: true } },
        { path: 'role-delegations', component: () => import('@/views/RoleDelegations.vue'), meta: { title: '临时授权', permission: 'role:delegate', denyCashier: true } },
        { path: 'recommend-report', component: () => import('@/views/RecommendReport.vue'), meta: { title: '推荐效果', permission: 'order:read', denyCashier: true } },
        { path: 'product-feedbacks', component: () => import('@/views/ProductFeedbacks.vue'), meta: { title: '顾客评价', permission: 'order:read', denyCashier: true } },
        { path: 'cameras', component: () => import('@/views/Cameras.vue'), meta: { title: '摄像头', permission: 'camera:read', denyCashier: true } },
        { path: 'kitchen', component: () => import('@/views/Kitchen.vue'), meta: { title: '厨房 KDS', kitchen: true, denyCashier: true } },
        { path: 'pos', component: () => import('@/views/PosOrder.vue'), meta: { title: '收银点餐', pos: true } },
        { path: 'pos-settlement', component: () => import('@/views/PosSettlement.vue'), meta: { title: '收银日结', settlement: true, denyCashier: true } },
        { path: 'pos-refunds', component: () => import('@/views/PosRefundApprove.vue'), meta: { title: '退款审批', settlement: true, denyCashier: true } },
        { path: 'delivery', redirect: '/delivery/orders' },
        { path: 'delivery/orders', component: () => import('@/views/delivery/DeliveryOrders.vue'), meta: { title: '外卖订单', permission: 'order:read', denyCashier: true } },
        { path: 'delivery/refunds', component: () => import('@/views/delivery/DeliveryRefunds.vue'), meta: { title: '外卖退款处理', permission: 'order:read', denyCashier: true } },
        { path: 'delivery/stores', redirect: { path: '/store-info', query: { tab: 'delivery' } } },
        { path: 'delivery/menu', component: () => import('@/views/delivery/DeliveryMaps.vue'), meta: { title: '外卖餐品', permission: 'product:read', denyCashier: true } },
        { path: 'delivery/sync', component: () => import('@/views/delivery/DeliverySync.vue'), meta: { title: '外卖同步监控', permission: 'product:read', denyCashier: true } },
        { path: 'delivery/logs', component: () => import('@/views/delivery/DeliveryLogs.vue'), meta: { title: '外卖回调日志', permission: 'order:read', denyCashier: true } },
        { path: 'store-info', component: () => import('@/views/StoreInfo.vue'), meta: { title: '门店信息', anyPermission: ['table:manage', 'order:read', 'product:read', 'settings:store', 'system:manage'], allowStoreAdmin: true, denyCashier: true } },
        { path: 'welcome-logs', component: () => import('@/views/WelcomeLogs.vue'), meta: { title: '迎宾日志', permission: 'welcome:manage', denyCashier: true } },
        { path: 'audit-logs', component: () => import('@/views/AuditLogs.vue'), meta: { title: '操作日志', permission: 'audit:read', denyCashier: true } },
        { path: 'settings', component: () => import('@/views/Settings.vue'), meta: { title: '系统设置', permission: 'system:manage', denyCashier: true } },
      ]
    },
    { path: '/scan', component: () => import('@/views/mobile/ScanLanding.vue') },
    { path: '/order', component: () => import('@/views/mobile/OrderMenu.vue') },
    { path: '/order/cart', component: () => import('@/views/mobile/OrderCart.vue') },
    { path: '/order/confirm', component: () => import('@/views/mobile/OrderConfirm.vue') },
    { path: '/order/result', component: () => import('@/views/mobile/OrderResult.vue') },
    { path: '/order/history', component: () => import('@/views/mobile/OrderHistory.vue') },
    { path: '/member', component: () => import('@/views/mobile/MemberCenter.vue') },
    { path: '/display', component: () => import('@/views/CustomerDisplay.vue'), meta: { title: '取餐叫号' } },
    { path: '/kds', component: () => import('@/views/Kitchen.vue'), meta: { title: '厨房 KDS', kitchen: true, standalone: true } },
    { path: '/pos-terminal', component: () => import('@/views/PosOrder.vue'), meta: { title: '收银点餐', pos: true, standalone: true } },
    { path: '/pos/my-stats', component: () => import('@/views/PosMyStats.vue'), meta: { title: '我的收银', cashierOnly: true, standalone: true } },
  ]
})

async function ensureUser() {
  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore()
  if (!auth.user) await auth.fetchMe()
  auth.assertSessionNotStale()
  return auth
}

router.beforeEach(async (to, _from, next) => {
  // 公开页精确匹配，避免前缀误伤后台路由（如 /order 前缀命中 /orders、/member 命中 /member-levels）
  const publicPaths = new Set(['/login', '/scan', '/order', '/order/cart', '/order/confirm', '/order/result', '/order/history', '/member', '/display'])
  const isPublic = publicPaths.has(to.path)
  if (to.path === '/login' && getStaffToken()) {
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().logout()
  }
  const token = getStaffToken()
  if (!isPublic && !token) {
    next('/login')
    return
  }
  if (token && !isPublic) {
    const auth = await ensureUser()
    if (!auth.user) {
      next('/login')
      return
    }
    if (auth.isCashier) {
      if (!cashierMayAccess(to.path)) {
        next('/pos-terminal')
        return
      }
    }
    if (to.meta?.denyCashier && auth.isCashier) {
      next('/pos-terminal')
      return
    }
    if (to.meta?.cashierOnly && !auth.isCashier) {
      next('/dashboard')
      return
    }
    const needPerm = to.meta?.permission as string | undefined
    const anyPerm = to.meta?.anyPermission as string[] | undefined
    const allowStoreAdmin = !!to.meta?.allowStoreAdmin && auth.isStoreAdmin
    if (anyPerm?.length) {
      const ok = anyPerm.some((code) => auth.hasPermission(code)) || allowStoreAdmin
      if (!ok) {
        next(auth.isCashier ? '/pos-terminal' : '/dashboard')
        return
      }
    } else if (needPerm && !auth.hasPermission(needPerm)) {
      next(auth.isCashier ? '/pos-terminal' : '/dashboard')
      return
    }
    if (to.meta?.kitchen && !auth.canAccessKitchen()) {
      next(auth.isCashier ? '/pos-terminal' : '/dashboard')
      return
    }
    if (to.meta?.pos && !auth.canAccessPos()) {
      next('/dashboard')
      return
    }
    if (to.meta?.settlement && !auth.canManageSettlement) {
      next('/dashboard')
      return
    }
  }
  next()
})

/** 顾客 H5 保持浅色；其余后台 / 登录 / KDS / POS / 叫号屏启用科技风主题 */
function isCustomerH5(path: string) {
  return path === '/scan' || path === '/member' || path === '/order' || path.startsWith('/order/')
}

function syncTechTheme(path: string) {
  document.documentElement.classList.toggle('snh-tech', !isCustomerH5(path))
}

syncTechTheme(typeof window !== 'undefined' ? window.location.pathname : '/')
router.afterEach((to) => {
  syncTechTheme(to.path)
})

export default router
