<template>
  <div class="login-page">
    <el-card class="login-card">
      <img src="/logo.png" alt="肠旺面" class="login-logo" />
      <h2>面馆智能经营系统</h2>
      <el-form @submit.prevent="handleLogin">
        <el-form-item><el-input v-model="username" placeholder="用户名" /></el-form-item>
        <el-form-item><el-input v-model="password" type="password" placeholder="密码" /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width:100%">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const username = ref(import.meta.env.DEV ? 'admin' : '')
const password = ref(import.meta.env.DEV ? 'admin123' : '')
const loading = ref(false)

async function handleLogin() {
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    if (auth.user?.role === 'CASHIER') {
      router.push('/pos-terminal')
    } else if (auth.user?.role === 'KITCHEN') {
      router.push('/kds')
    } else {
      router.push('/dashboard')
    }
  } catch (e: any) {
    ElMessage.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
.login-card { width: 400px; padding: 20px; }
.login-logo {
  display: block;
  width: 120px;
  height: auto;
  margin: 0 auto 16px;
  border-radius: 8px;
}
h2 { text-align: center; margin-bottom: 24px; }
</style>
