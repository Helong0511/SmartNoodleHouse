<template>
  <div class="scan-page">
    <div v-if="loading" class="card">
      <div class="spinner" />
      <p>正在识别扫码环境…</p>
    </div>

    <div v-else-if="error" class="card error">
      <h2>无法打开桌台</h2>
      <p>{{ error }}</p>
    </div>

    <div v-else class="card">
      <p class="store" v-if="storeName">{{ storeName }}</p>
      <h1>{{ tableLabel }}</h1>
      <p class="sub">扫码点餐 · 桌号 {{ info?.table?.table_no }}</p>

      <p v-if="autoHint" class="hint">{{ autoHint }}</p>

      <div class="actions">
        <button type="button" class="btn wechat" @click="openWechat">微信点餐</button>
        <button type="button" class="btn alipay" @click="openAlipay">支付宝点餐</button>
        <button type="button" class="btn h5" @click="openH5">浏览器点餐</button>
      </div>

      <p class="foot">若未自动跳转，请点击上方按钮</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'

type ScanInfo = {
  table: { table_no: string; table_name?: string; store_id: number }
  store?: { name?: string }
  wechat_scheme: string
  alipay_scheme: string
  h5_order_url: string
}

const route = useRoute()
const loading = ref(true)
const error = ref('')
const info = ref<ScanInfo | null>(null)
const autoHint = ref('')
const tableLabel = ref('加载中…')
const storeName = ref('')

function ua() {
  return navigator.userAgent || ''
}

function isWechat() {
  return /MicroMessenger/i.test(ua())
}

function isAlipay() {
  return /AlipayClient|AliApp/i.test(ua())
}

function tryOpen(url: string) {
  if (!url) return
  window.location.href = url
}

function openWechat() {
  if (!info.value) return
  tryOpen(info.value.wechat_scheme)
}

function openAlipay() {
  if (!info.value) return
  tryOpen(info.value.alipay_scheme)
}

function openH5() {
  if (!info.value) return
  const url = info.value.h5_order_url
  const channel = isAlipay() ? 'alipay' : 'wechat'
  const sep = url.includes('?') ? '&' : '?'
  window.location.href = `${url}${sep}channel=${channel}`
}

function autoRedirect() {
  if (!info.value) return
  if (isWechat()) {
    autoHint.value = '检测到微信，正在打开小程序…'
    openWechat()
    return
  }
  if (isAlipay()) {
    autoHint.value = '检测到支付宝，正在打开小程序…'
    openAlipay()
    return
  }
  autoHint.value = '请选择微信或支付宝打开小程序点餐'
}

onMounted(async () => {
  const token = String(route.query.token || '').trim()
  if (!token) {
    loading.value = false
    error.value = '缺少桌台参数，请重新扫描桌码'
    return
  }
  try {
    const res = await api.get('/miniprogram/scan-landing', { params: { token } })
    if (!res.data) {
      error.value = res.message || '桌台无效或已停用'
      return
    }
    info.value = res.data
    tableLabel.value = res.data.table.table_name || res.data.table.table_no || '堂食'
    storeName.value = res.data.store?.name || ''
    loading.value = false
    setTimeout(autoRedirect, 300)
  } catch (e: any) {
    error.value = e.message || '加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.scan-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  background: linear-gradient(160deg, #fff8f5 0%, #f5f7fa 55%);
}
.card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border-radius: 16px;
  padding: 28px 22px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
  text-align: center;
}
.card.error h2 { color: #c45656; margin-bottom: 8px; }
.store { color: #888; font-size: 14px; margin-bottom: 4px; }
h1 { font-size: 28px; margin: 0 0 8px; color: #222; }
.sub { color: #666; font-size: 14px; margin-bottom: 20px; }
.hint { color: #ff6b35; font-size: 13px; margin-bottom: 16px; }
.actions { display: flex; flex-direction: column; gap: 12px; }
.btn {
  border: none;
  border-radius: 10px;
  padding: 14px 16px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
}
.btn.wechat { background: #07c160; color: #fff; }
.btn.alipay { background: #1677ff; color: #fff; }
.btn.h5 { background: #f5f5f5; color: #333; }
.foot { margin-top: 18px; color: #999; font-size: 12px; }
.spinner {
  width: 36px;
  height: 36px;
  margin: 0 auto 12px;
  border: 3px solid #eee;
  border-top-color: #ff6b35;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
