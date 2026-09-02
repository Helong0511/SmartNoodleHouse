<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <img src="/logo.png" alt="肠旺面" class="logo-img" />
      </div>
      <el-menu :default-active="$route.path" router background-color="#1d1e1f" text-color="#bbb" active-text-color="#409eff">
        <el-menu-item v-if="auth.hasPermission('order:read') && !auth.isCashier" index="/dashboard">数据概览</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read') && !auth.isCashier" index="/categories">分类管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read') && !auth.isCashier" index="/options">规格管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read') && !auth.isCashier" index="/products">商品管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('order:read') && !auth.isCashier" index="/orders">订单管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('customer:read') && !auth.isCashier" index="/customers">顾客管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('customer:read') && !auth.isCashier" index="/member-levels">会员等级</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('coupon:read') && !auth.isCashier" index="/coupons">优惠券管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('table:manage')" index="/tables">桌台管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('user:read')" index="/users">员工管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read') && !auth.isCashier" index="/store-stocks">分店库存</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('payment:read') && !auth.isCashier" index="/payments">支付记录</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('camera:read')" index="/cameras">摄像头</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('welcome:manage')" index="/welcome-logs">迎宾日志</el-menu-item>
        <el-menu-item v-if="auth.canAccessKitchen()" index="/kitchen">厨房 KDS</el-menu-item>
        <el-menu-item v-if="auth.canAccessPos() && !auth.isCashier" index="/pos">收银点餐</el-menu-item>
        <el-menu-item v-if="auth.canManageSettlement" index="/pos-settlement">收银日结</el-menu-item>
        <el-menu-item v-if="auth.canManageSettlement" index="/pos-refunds">退款审批</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('order:read') && !auth.isCashier" index="/delivery">外卖管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('system:manage')" index="/settings">系统设置</el-menu-item>
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
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

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
.aside { background: #1d1e1f; }
.logo { padding: 16px 12px; text-align: center; }
.logo-img {
  display: block;
  width: 100%;
  max-width: 160px;
  height: auto;
  margin: 0 auto;
  border-radius: 8px;
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
