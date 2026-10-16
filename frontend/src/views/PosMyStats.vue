<template>
  <div class="stats-page">
    <header class="head">
      <el-button size="large" @click="goBack">返回收银</el-button>
      <h1>我的收银 · 今日</h1>
      <el-button type="danger" link @click="logout">退出</el-button>
    </header>
    <div v-if="loading" class="tip">加载中…</div>
    <div v-else class="cards">
      <div class="card"><div class="label">备用金</div><div class="val">¥{{ data.opening_float || '0.00' }}</div></div>
      <div class="card"><div class="label">现金收款</div><div class="val cash">¥{{ data.total_cash || '0.00' }}</div></div>
      <div class="card"><div class="label">微信收款</div><div class="val wx">¥{{ data.total_wechat || '0.00' }}</div></div>
      <div class="card"><div class="label">支付宝收款</div><div class="val ali">¥{{ data.total_alipay || '0.00' }}</div></div>
      <div class="card total"><div class="label">今日合计</div><div class="val">¥{{ data.total_amount || '0.00' }}</div></div>
    </div>
    <p class="hint">仅显示您本人今日收款数据</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const data = ref<any>({})

function goBack() {
  router.push('/pos-terminal')
}

function logout() {
  auth.logout()
  router.push('/login')
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  try {
    const res = await api.get('/pos/my-summary')
    data.value = res.data || {}
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.stats-page { min-height: 100dvh; background: linear-gradient(180deg, #0e1e3c 0%, #0a1730 100%); padding: 16px; color: #dbeaff; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 20px; }
.head h1 { margin: 0; font-size: 22px; flex: 1; text-align: center; color: #e8f4ff; text-shadow: 0 0 12px rgba(127, 200, 255, 0.45); }
.tip { text-align: center; padding: 40px; color: #7a9cc4; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 14px; max-width: 720px; margin: 0 auto; }
.card { background: rgba(26, 50, 92, 0.62); border: 1px solid rgba(120, 200, 255, 0.32); border-radius: 14px; padding: 20px; box-shadow: 0 0 24px rgba(80, 165, 245, 0.2); backdrop-filter: blur(12px); }
.card.total { grid-column: 1 / -1; }
.label { font-size: 15px; color: #7a9cc4; margin-bottom: 8px; }
.val { font-size: 28px; font-weight: 800; color: #7fc8ff; text-shadow: 0 0 12px rgba(127, 200, 255, 0.45); }
.val.cash { color: #f5c87c; }
.val.wx { color: #6ee7a7; }
.val.ali { color: #5ba8f5; }
.hint { text-align: center; color: #7a9cc4; margin-top: 24px; font-size: 14px; }
</style>
