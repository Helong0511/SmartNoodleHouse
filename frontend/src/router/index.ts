import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/Login.vue') },
    {
      path: '/',
      component: () => import('@/layouts/AdminLayout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '数据概览' } },
        { path: 'products', component: () => import('@/views/Products.vue'), meta: { title: '商品管理' } },
        { path: 'categories', component: () => import('@/views/Categories.vue'), meta: { title: '分类管理' } },
        { path: 'options', component: () => import('@/views/Options.vue'), meta: { title: '规格管理' } },
        { path: 'orders', component: () => import('@/views/Orders.vue'), meta: { title: '订单管理' } },
        { path: 'orders/:id', component: () => import('@/views/OrderDetail.vue'), meta: { title: '订单详情' } },
        { path: 'customers', component: () => import('@/views/Customers.vue'), meta: { title: '顾客管理' } },
        { path: 'tables', component: () => import('@/views/Tables.vue'), meta: { title: '桌台管理' } },
        { path: 'users', component: () => import('@/views/Users.vue'), meta: { title: '员工管理', permission: 'user:read' } },
        { path: 'store-stocks', component: () => import('@/views/StoreStocks.vue'), meta: { title: '分店库存', permission: 'product:read' } },
        { path: 'payments', component: () => import('@/views/Payments.vue'), meta: { title: '支付记录' } },
        { path: 'cameras', component: () => import('@/views/Cameras.vue'), meta: { title: '摄像头' } },
        { path: 'kitchen', component: () => import('@/views/Kitchen.vue'), meta: { title: '厨房 KDS', kitchen: true } },
        { path: 'delivery', component: () => import('@/views/Delivery.vue'), meta: { title: '外卖管理' } },
        { path: 'welcome-logs', component: () => import('@/views/WelcomeLogs.vue'), meta: { title: '迎宾日志' } },
        { path: 'settings', component: () => import('@/views/Settings.vue'), meta: { title: '系统设置', permission: 'system:manage' } },
      ]
    },
    { path: '/order', component: () => import('@/views/mobile/OrderMenu.vue') },
    { path: '/order/cart', component: () => import('@/views/mobile/OrderCart.vue') },
    { path: '/order/confirm', component: () => import('@/views/mobile/OrderConfirm.vue') },
    { path: '/order/result', component: () => import('@/views/mobile/OrderResult.vue') },
    { path: '/order/history', component: () => import('@/views/mobile/OrderHistory.vue') },
    { path: '/member', component: () => import('@/views/mobile/MemberCenter.vue') }
  ]
})

router.beforeEach(async (to, _from, next) => {
  const token =
    localStorage.getItem('snh_staff_token') || localStorage.getItem('token')
  const publicPaths = ['/login', '/order', '/order/cart', '/order/confirm', '/order/result', '/order/history', '/member']
  const isPublic = publicPaths.some((p) => to.path.startsWith(p))
  if (!isPublic && !token) {
    next('/login')
    return
  }
  const needPerm = to.meta?.permission as string | undefined
  if (needPerm && token) {
    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    if (!auth.user) {
      await auth.fetchMe()
    }
    if (auth.user && !auth.hasPermission(needPerm)) {
      next('/dashboard')
      return
    }
  }
  if (to.meta?.kitchen && token) {
    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    if (!auth.user) {
      await auth.fetchMe()
    }
    if (auth.user && !auth.canAccessKitchen()) {
      next('/dashboard')
      return
    }
  }
  next()
})

export default router
