<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">🍜 智能面馆</div>
      <el-menu :default-active="$route.path" router background-color="#1d1e1f" text-color="#bbb" active-text-color="#409eff">
        <el-menu-item v-if="auth.hasPermission('order:read')" index="/dashboard">数据概览</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read')" index="/products">商品管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read')" index="/categories">分类管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read')" index="/options">规格管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('order:read')" index="/orders">订单管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('customer:read')" index="/customers">顾客管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('coupon:read')" index="/coupons">优惠券管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('table:manage')" index="/tables">桌台管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('user:read')" index="/users">员工管理</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('product:read')" index="/store-stocks">分店库存</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('payment:read')" index="/payments">支付记录</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('camera:read')" index="/cameras">摄像头</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('welcome:manage')" index="/welcome-logs">迎宾日志</el-menu-item>
        <el-menu-item v-if="auth.canAccessKitchen()" index="/kitchen">厨房 KDS</el-menu-item>
        <el-menu-item v-if="auth.hasPermission('order:read')" index="/delivery">外卖管理</el-menu-item>
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

onMounted(() => {
  auth.fetchMe()
})
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1d1e1f; }
.logo { color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center; }
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
