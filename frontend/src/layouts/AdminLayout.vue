<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <img src="/logo.png?v=202609080938" alt="肠旺面" class="logo-img" />
      </div>
      <el-menu
        :key="openGroup"
        :default-active="activeMenu"
        :default-openeds="openGroup ? [openGroup] : []"
        router
        background-color="#1d1e1f"
        text-color="#bbb"
        active-text-color="#409eff"
        class="side-menu"
      >
        <el-menu-item v-if="showDashboard" index="/dashboard">数据概览</el-menu-item>

        <el-sub-menu v-if="showCatalog" index="g-catalog">
          <template #title>菜品</template>
          <el-menu-item index="/categories">分类管理</el-menu-item>
          <el-menu-item index="/category-schedules">分类时段排序</el-menu-item>
          <el-menu-item index="/options">规格管理</el-menu-item>
          <el-menu-item index="/products">商品管理</el-menu-item>
          <el-menu-item index="/store-stocks">分店库存</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showOps" index="g-ops">
          <template #title>营业</template>
          <el-menu-item v-if="showPos" index="/pos">收银点餐</el-menu-item>
          <el-menu-item v-if="showKitchen" index="/kitchen">厨房 KDS</el-menu-item>
          <el-menu-item v-if="showOrders" index="/orders">订单管理</el-menu-item>
          <el-menu-item v-if="showPayments" index="/payments">支付记录</el-menu-item>
          <el-menu-item v-if="showPayments" index="/financial-ledger">财务账本</el-menu-item>
          <el-menu-item v-if="showSettlement" index="/pos-settlement">收银日结</el-menu-item>
          <el-menu-item v-if="showSettlement" index="/pos-refunds">退款审批</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showDelivery" index="g-delivery">
          <template #title>外卖</template>
          <el-menu-item index="/delivery/orders">外卖订单</el-menu-item>
          <el-menu-item index="/delivery/refunds">退款处理</el-menu-item>
          <el-menu-item index="/delivery/stores">门店与营业</el-menu-item>
          <el-menu-item index="/delivery/menu">外卖餐品</el-menu-item>
          <el-menu-item index="/delivery/sync">同步监控</el-menu-item>
          <el-menu-item index="/delivery/logs">回调日志</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showStore" index="g-store">
          <template #title>门店</template>
          <el-menu-item index="/store-info">门店信息</el-menu-item>
          <el-menu-item v-if="showTables" index="/tables">桌台管理</el-menu-item>
          <el-menu-item v-if="showUsers" index="/users">员工管理</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showMember" index="g-member">
          <template #title>会员</template>
          <el-menu-item v-if="showCustomers" index="/customers">顾客管理</el-menu-item>
          <el-menu-item v-if="showCustomers" index="/member-levels">会员等级</el-menu-item>
          <el-menu-item v-if="showCoupons" index="/coupons">优惠券</el-menu-item>
        </el-sub-menu>

        <el-sub-menu v-if="showSmart" index="g-smart">
          <template #title>迎宾</template>
          <el-menu-item v-if="showCameras" index="/cameras">摄像头</el-menu-item>
          <el-menu-item v-if="showWelcome" index="/welcome-logs">迎宾日志</el-menu-item>
        </el-sub-menu>

        <el-menu-item v-if="showSettings" index="/settings">系统设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span>{{ ($route.meta.title as string) || '后台管理' }}</span>
        <div class="right">
          <span v-if="auth.user" class="user">{{ auth.user.display_name || auth.user.username }}</span>
          <el-button type="danger" link @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main><router-view /></el-main>
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
const showDelivery = computed(() => auth.hasPermission('order:read') && notCashier.value)
const showOrders = computed(() => auth.hasPermission('order:read') && notCashier.value)
const showPayments = computed(() => auth.hasPermission('payment:read') && notCashier.value)
const showSettlement = computed(() => auth.canManageSettlement)
const showCatalog = computed(() => auth.hasPermission('product:read') && notCashier.value)
const showCustomers = computed(() => auth.hasPermission('customer:read') && notCashier.value)
const showCoupons = computed(() => auth.hasPermission('coupon:read') && notCashier.value)
const showMember = computed(() => showCustomers.value || showCoupons.value)
const showTables = computed(() => auth.hasPermission('table:manage'))
const showUsers = computed(() => auth.hasPermission('user:read'))
const showStore = computed(() => showTables.value || showUsers.value || auth.hasPermission('system:manage'))
const showCameras = computed(() => auth.hasPermission('camera:read'))
const showWelcome = computed(() => auth.hasPermission('welcome:manage'))
const showSmart = computed(() => showCameras.value || showWelcome.value)
const showSettings = computed(() => auth.hasPermission('system:manage'))
const showOps = computed(
  () =>
    showPos.value
    || showKitchen.value
    || showDelivery.value
    || showOrders.value
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
  if (['/pos', '/kitchen', '/orders', '/payments', '/financial-ledger', '/pos-settlement', '/pos-refunds'].includes(p)) {
    return 'g-ops'
  }
  if (['/products', '/categories', '/category-schedules', '/options', '/store-stocks'].includes(p)) return 'g-catalog'
  if (['/customers', '/member-levels', '/coupons'].includes(p)) return 'g-member'
  if (['/tables', '/users', '/store-info'].includes(p)) return 'g-store'
  if (['/cameras', '/welcome-logs'].includes(p)) return 'g-smart'
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
.layout { height: 100vh; }
.aside {
  background: #1d1e1f;
  overflow-y: auto;
}
.logo { padding: 16px 12px; text-align: center; }
.logo-img {
  display: block;
  width: 120px;
  height: 120px;
  object-fit: contain;
  margin: 0 auto;
  border-radius: 12px;
  background: #fff;
}
.side-menu {
  border-right: none;
  padding-bottom: 24px;
}
.side-menu :deep(.el-sub-menu__title) {
  font-weight: 600;
  color: #ddd !important;
}
.side-menu :deep(.el-menu-item) {
  min-width: 0;
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #eee;
}
.right { display: flex; align-items: center; gap: 12px; }
.user { color: #666; font-size: 14px; }
</style>
