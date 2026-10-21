<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="aside-glow" aria-hidden="true"></div>
      <div class="logo">
        <div class="logo-ring"></div>
        <img src="/logo.png?v=202609080938" alt="肠旺面" class="logo-img" />
      </div>
      <p class="brand-sub">SPICY NOODLE · ADMIN</p>
      <el-menu :key="openGroup" :default-active="activeMenu" :default-openeds="openGroup ? [openGroup] : []" router
        class="side-menu">
        <el-menu-item v-if="showDashboard" index="/dashboard">数据概览</el-menu-item>

        <el-sub-menu v-if="showStore" index="g-store">
          <template #title>门店</template>
          <el-menu-item v-if="showStoreInfo" index="/store-info">门店信息</el-menu-item>
          <el-menu-item v-if="showTables" index="/tables">桌台管理</el-menu-item>
          <el-menu-item v-if="showUsers" index="/users">员工管理</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showCatalog" index="g-catalog">
          <template #title>菜品</template>
          <el-menu-item index="/categories">分类管理</el-menu-item>
          <el-menu-item index="/category-schedules">分类时段排序</el-menu-item>
          <el-menu-item index="/options">规格管理</el-menu-item>
          <el-menu-item index="/products">餐品管理</el-menu-item>
          <el-menu-item index="/store-stocks">分店库存</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showMember" index="g-member">
          <template #title>会员</template>
          <el-menu-item v-if="showCustomers" index="/customers">顾客管理</el-menu-item>
          <el-menu-item v-if="showCustomers" index="/member-levels">会员等级</el-menu-item>
          <el-menu-item v-if="showWallet" index="/wallet">会员储值</el-menu-item>
          <el-menu-item v-if="showCoupons" index="/coupons">优惠券</el-menu-item>
          <el-menu-item v-if="showCustomers" index="/social-benefits">社会贡献</el-menu-item>
          <el-menu-item v-if="showCustomers" index="/marketing/segments">客户分群</el-menu-item>
          <el-menu-item v-if="showCoupons" index="/marketing/campaigns">营销活动</el-menu-item>
          <el-menu-item v-if="showCoupons" index="/marketing/report">营销报表</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showOps" index="g-ops">
          <template #title>营业</template>
          <el-menu-item v-if="showPos" index="/pos">收银点餐</el-menu-item>
          <el-menu-item v-if="showKitchen" index="/kitchen">厨房 KDS</el-menu-item>
          <el-menu-item v-if="showOrders || showPayments" index="/orders">订单管理</el-menu-item>
          <el-menu-item v-if="showQueue" index="/queue-manage">排队管理</el-menu-item>
          <el-menu-item v-if="showInvoices" index="/invoices">发票申请</el-menu-item>
          <el-menu-item v-if="showOrders" index="/recommend-report">推荐效果</el-menu-item>
          <el-menu-item v-if="showOrders" index="/product-feedbacks">顾客评价</el-menu-item>
          <el-menu-item v-if="showPayments" index="/financial-ledger">财务账本</el-menu-item>
          <el-menu-item v-if="showSettlement" index="/pos-settlement">收银日结</el-menu-item>
          <el-menu-item v-if="showSettlement" index="/pos-refunds">退款审批</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showDelivery" index="g-delivery">
          <template #title>外卖</template>
          <el-menu-item v-if="showDeliveryOrders" index="/delivery/orders">外卖订单</el-menu-item>
          <el-menu-item v-if="showDeliveryOrders" index="/delivery/refunds">退款处理</el-menu-item>
          <el-menu-item v-if="showStoreInfo" index="/delivery/stores">门店外卖设置</el-menu-item>
          <el-menu-item v-if="showDeliveryMenu" index="/delivery/menu">外卖餐品</el-menu-item>
          <el-menu-item v-if="showDeliveryMenu" index="/delivery/sync">同步监控</el-menu-item>
          <el-menu-item v-if="showDeliveryOrders" index="/delivery/logs">回调日志</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showSmart" index="g-smart">
          <template #title>迎宾</template>
          <el-menu-item v-if="showCameras" index="/cameras">摄像头</el-menu-item>
          <el-menu-item v-if="showWelcome" index="/welcome-logs">迎宾日志</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showSystemGroup" index="g-system">
          <template #title>系统管理</template>
          <el-menu-item v-if="showRolePerms" index="/role-permissions">角色权限</el-menu-item>
          <el-menu-item v-if="showSettings" index="/settings">系统设置</el-menu-item>
          <el-menu-item v-if="showAudit" index="/audit-logs">操作日志</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container class="right-pane">
      <el-header class="header">
        <div class="header-title">
          <span class="title-beam" aria-hidden="true"></span>
          <span>{{ ($route.meta.title as string) || '后台管理' }}</span>
        </div>
        <div class="right">
          <span v-if="auth.user" class="user">{{ auth.user.display_name || auth.user.username }}</span>
          <el-button class="logout-btn" link @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const notCashier = computed(() => !auth.isCashier)

const showDashboard = computed(() => auth.hasPermission('order:read') && notCashier.value)
const showPos = computed(() => auth.canAccessPos() && notCashier.value)
const showKitchen = computed(() => auth.canAccessKitchen())
const showDeliveryOrders = computed(() => auth.hasPermission('order:read') && notCashier.value)
const showDeliveryMenu = computed(() => auth.hasPermission('product:read') && notCashier.value)
const showOrders = computed(() => auth.hasPermission('order:read') && notCashier.value)
const showQueue = computed(() => auth.hasPermission('table:manage') && notCashier.value)
const showInvoices = computed(() => auth.hasPermission('invoice:manage') && notCashier.value)
const showPayments = computed(() => auth.hasPermission('payment:read') && notCashier.value)
const showSettlement = computed(() => auth.canManageSettlement)
const showCatalog = computed(() => auth.hasPermission('product:read') && notCashier.value)
const showCustomers = computed(() => auth.hasPermission('customer:read') && notCashier.value)
const showCoupons = computed(() => auth.hasPermission('coupon:read') && notCashier.value)
const showWallet = computed(() => auth.hasPermission('wallet:read') && notCashier.value)
const showMember = computed(() => showCustomers.value || showCoupons.value || showWallet.value)
const showTables = computed(() => auth.hasPermission('table:manage'))
const showUsers = computed(() => auth.hasPermission('user:read'))
const showStoreInfo = computed(
  () =>
    auth.isSuperAdmin
    || auth.isStoreAdmin
    || auth.hasPermission('system:manage')
    || auth.hasPermission('settings:store')
    || auth.hasPermission('table:manage')
    || auth.hasPermission('order:read')
    || auth.hasPermission('product:read'),
)
const showStore = computed(() =>
  showTables.value
  || showUsers.value
  || showStoreInfo.value,
)
const showDelivery = computed(
  () => showDeliveryOrders.value || showDeliveryMenu.value || showStoreInfo.value,
)
const showCameras = computed(() => auth.hasPermission('camera:read'))
const showWelcome = computed(() => auth.hasPermission('welcome:manage'))
const showSmart = computed(() => showCameras.value || showWelcome.value)
const showSettings = computed(() => auth.hasPermission('system:manage'))
const showRolePerms = computed(() => auth.hasPermission('system:manage'))
const showAudit = computed(() => auth.hasPermission('audit:read'))
const showSystemGroup = computed(() => showSettings.value || showAudit.value || showRolePerms.value)
const showOps = computed(
  () =>
    showPos.value
    || showKitchen.value
    || showOrders.value
    || showQueue.value
    || showInvoices.value
    || showPayments.value
    || showSettlement.value,
)

const activeMenu = computed(() => {
  if (route.path.startsWith('/orders/')) return '/orders'
  return route.path
})

const openGroup = computed(() => {
  const p = activeMenu.value
  if (p === '/store-info') return 'g-store'
  if (p.startsWith('/delivery')) return 'g-delivery'
  if (['/pos', '/kitchen', '/orders', '/queue-manage', '/invoices', '/financial-ledger', '/pos-settlement', '/pos-refunds', '/recommend-report', '/product-feedbacks'].includes(p)) {
    return 'g-ops'
  }
  if (['/products', '/categories', '/category-schedules', '/options', '/store-stocks'].includes(p)) return 'g-catalog'
  if (
    ['/customers', '/member-levels', '/coupons', '/wallet', '/social-benefits'].includes(p)
    || p.startsWith('/marketing/')
  ) return 'g-member'
  if (['/tables', '/users', '/store-info'].includes(p)) return 'g-store'
  if (['/cameras', '/welcome-logs'].includes(p)) return 'g-smart'
  if (['/settings', '/audit-logs', '/role-permissions'].includes(p)) return 'g-system'
  return ''
})

function logout() {
  auth.logout()
  router.push('/login')
}

onMounted(async () => {
  await auth.fetchMe()
  if (auth.isCashier) {
    router.replace('/pos-terminal')
  }
})
</script>

<style scoped>
.layout {
  height: 100vh;
  background: transparent;
}

.aside {
  position: relative;
  overflow-y: auto;
  overflow-x: hidden;
  background:
    linear-gradient(180deg, rgba(12, 28, 57, 0.98) 0%, rgba(8, 20, 42, 0.98) 100%);
  border-right: 1px solid rgba(120, 200, 255, 0.22);
  box-shadow: inset -1px 0 0 rgba(127, 200, 255, 0.08), 8px 0 28px rgba(4, 14, 32, 0.35);
}

.aside-glow {
  position: absolute;
  top: -80px;
  left: -60px;
  width: 220px;
  height: 220px;
  border-radius: 50%;
  background: #4a8fe7;
  opacity: 0.22;
  filter: blur(70px);
  pointer-events: none;
}

.logo {
  position: relative;
  width: 96px;
  height: 96px;
  margin: 20px auto 8px;
}

.logo-img {
  position: relative;
  z-index: 2;
  display: block;
  width: 100%;
  height: 100%;
  padding: 6px;
  box-sizing: border-box;
  object-fit: contain;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 20px rgba(120, 200, 255, 0.55), 0 0 40px rgba(90, 170, 250, 0.28);
}

.logo-ring {
  position: absolute;
  inset: -8px;
  border-radius: 50%;
  border: 1px dashed rgba(127, 200, 255, 0.45);
  animation: spin 16s linear infinite;
  pointer-events: none;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.brand-sub {
  margin: 0 0 12px;
  text-align: center;
  font-size: 9px;
  letter-spacing: 2px;
  color: rgba(170, 215, 255, 0.55);
  user-select: none;
}

.side-menu {
  border-right: none;
  padding-bottom: 28px;
  background: transparent !important;
  --el-menu-bg-color: transparent;
  --el-menu-hover-bg-color: rgba(80, 165, 245, 0.14);
  --el-menu-active-color: #e8f4ff;
  --el-menu-text-color: #9bb8d8;
  --el-menu-hover-text-color: #e2f2ff;
}

.side-menu :deep(.el-sub-menu__title) {
  font-weight: 600;
  color: #cfe6ff !important;
}

.side-menu :deep(.el-sub-menu__title:hover),
.side-menu :deep(.el-menu-item:hover) {
  background: rgba(80, 165, 245, 0.14) !important;
}

.side-menu :deep(.el-menu-item) {
  min-width: 0;
  margin: 2px 10px;
  border-radius: 8px;
  color: #9bb8d8;
}

.side-menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, rgba(91, 168, 245, 0.35), rgba(91, 168, 245, 0.08)) !important;
  color: #e8f4ff !important;
  box-shadow: inset 2px 0 0 #7fc8ff, 0 0 16px rgba(91, 168, 245, 0.2);
}

.side-menu :deep(.el-sub-menu .el-menu-item) {
  min-width: 0;
  padding-left: 44px !important;
}

.right-pane {
  min-width: 0;
  background: transparent;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 56px;
  padding: 0 20px;
  background: rgba(16, 34, 66, 0.55);
  border-bottom: 1px solid rgba(120, 200, 255, 0.22);
  backdrop-filter: blur(12px) saturate(140%);
  -webkit-backdrop-filter: blur(12px) saturate(140%);
  box-shadow: 0 4px 20px rgba(4, 14, 32, 0.25);
}

.header-title {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #e8f4ff;
  text-shadow: 0 0 12px rgba(127, 200, 255, 0.45);
}

.title-beam {
  width: 3px;
  height: 18px;
  border-radius: 2px;
  background: linear-gradient(180deg, #7fd4ff, #4f9bf5);
  box-shadow: 0 0 10px rgba(127, 200, 255, 0.8);
}

.right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user {
  color: #9bb8d8;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.logout-btn {
  color: #ff9a9a !important;
}

.logout-btn:hover {
  color: #ffb3b3 !important;
}

.main {
  background: transparent;
  padding: 16px;
}

@media (prefers-reduced-motion: reduce) {
  .logo-ring {
    animation: none;
  }
}
</style>
