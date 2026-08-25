<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">🍜 智能面馆</div>
      <el-menu :default-active="$route.path" router background-color="#1d1e1f" text-color="#bbb" active-text-color="#409eff">
        <el-menu-item index="/dashboard">数据概览</el-menu-item>
        <el-menu-item index="/products">商品管理</el-menu-item>
        <el-menu-item index="/categories">分类管理</el-menu-item>
        <el-menu-item index="/options">规格管理</el-menu-item>
        <el-menu-item index="/orders">订单管理</el-menu-item>
        <el-menu-item index="/customers">顾客管理</el-menu-item>
        <el-menu-item index="/tables">桌台管理</el-menu-item>
        <el-menu-item index="/payments">支付记录</el-menu-item>
        <el-menu-item index="/cameras">摄像头</el-menu-item>
        <el-menu-item index="/kitchen">厨房 KDS</el-menu-item>
        <el-menu-item index="/delivery">外卖管理</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span>{{ $route.meta.title || '后台管理' }}</span>
        <el-button type="danger" link @click="logout">退出</el-button>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1d1e1f; }
.logo { color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center; }
.header { display: flex; justify-content: space-between; align-items: center; background: #fff; border-bottom: 1px solid #eee; }
</style>
