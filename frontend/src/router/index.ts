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
        { path: 'dashboard', component: () => import('@/views/Dashboard.vue') },
        { path: 'products', component: () => import('@/views/Products.vue') },
        { path: 'categories', component: () => import('@/views/Categories.vue') },
        { path: 'options', component: () => import('@/views/Options.vue') },
        { path: 'orders', component: () => import('@/views/Orders.vue') },
        { path: 'orders/:id', component: () => import('@/views/OrderDetail.vue') },
        { path: 'customers', component: () => import('@/views/Customers.vue') },
        { path: 'tables', component: () => import('@/views/Tables.vue') },
        { path: 'payments', component: () => import('@/views/Payments.vue') },
        { path: 'cameras', component: () => import('@/views/Cameras.vue') },
        { path: 'kitchen', component: () => import('@/views/Kitchen.vue') },
        { path: 'delivery', component: () => import('@/views/Delivery.vue') },
        { path: 'settings', component: () => import('@/views/Settings.vue') }
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

router.beforeEach((to, _from, next) => {
  const token =
    localStorage.getItem('snh_staff_token') || localStorage.getItem('token')
  const publicPaths = ['/login', '/order', '/order/cart', '/order/confirm', '/order/result', '/order/history', '/member']
  const isPublic = publicPaths.some(p => to.path.startsWith(p))
  if (!isPublic && !token) next('/login')
  else next()
})

export default router
