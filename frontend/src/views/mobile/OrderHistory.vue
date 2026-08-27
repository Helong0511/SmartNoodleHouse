<template>
  <div class="history">
    <div class="head">
      <h2>历史订单</h2>
      <el-button link type="primary" @click="$router.push('/member')">会员中心</el-button>
    </div>
    <p v-if="loading" class="tip">加载中...</p>
    <p v-else-if="!orders.length" class="tip">暂无历史订单，去点餐后再来看看。</p>
    <div v-for="o in orders" :key="o.id" class="order">
      <div class="row">
        <span class="ono">{{ o.order_no }}</span>
        <el-tag size="small">{{ o.status_label || o.status }}</el-tag>
      </div>
      <div class="summary">{{ o.item_summary }}</div>
      <div class="row foot">
        <span class="meta">{{ formatTime(o.created_at) }} · ¥{{ o.total_amount }}</span>
        <el-button
          size="small"
          type="primary"
          :disabled="!o.can_reorder || reorderingId === o.id"
          :loading="reorderingId === o.id"
          @click="reorder(o.id)"
        >再来一单</el-button>
      </div>
    </div>
    <el-button style="margin-top:16px;width:100%" @click="$router.push('/order')">去点餐</el-button>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'

const orders = ref<any[]>([])
const loading = ref(false)
const reorderingId = ref<number | null>(null)
const router = useRouter()

function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function ensureLogin() {
  if (localStorage.getItem('customer_token')) return
  const code = localStorage.getItem('h5_login_code') || `h5_member_${Date.now()}`
  localStorage.setItem('h5_login_code', code)
  const res = await api.post('/miniprogram/wechat/login', { code })
  localStorage.setItem('customer_token', res.data.customer_token)
  localStorage.setItem('customer_id', String(res.data.customer_id))
}

async function loadOrders() {
  loading.value = true
  try {
    await ensureLogin()
    const res = await api.get('/orders/mine', { params: { page: 1, page_size: 50 } })
    orders.value = res.data.items || []
  } catch (e: any) {
    orders.value = []
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function reorder(orderId: number) {
  reorderingId.value = orderId
  try {
    const res = await api.post(`/orders/${orderId}/reorder`)
    const items = res.data.cart_items || []
    if (!items.length) {
      ElMessage.warning('没有可复购商品')
      return
    }
    const cart = items.map((it: any) => ({
      product_id: it.product_id,
      name: it.name,
      quantity: it.quantity || 1,
      option_ids: it.option_ids || [],
      option_labels: it.option_labels || [],
      option_text: it.option_text || (it.option_labels || []).join('、'),
      unit_price: it.unit_price,
      subtotal: it.subtotal,
      imageUrl: it.imageUrl || it.main_image || '',
      main_image: it.main_image || '',
    }))
    localStorage.setItem('snh_cart', JSON.stringify(cart))
    localStorage.setItem('cart', JSON.stringify(cart))
    const skipped = res.data.skipped || []
    if (skipped.length) {
      await ElMessageBox.alert(`已加入 ${cart.length} 件；${skipped.length} 件不可用已跳过`, '已加入购物车')
    } else {
      ElMessage.success('已按历史订单加入购物车')
    }
    router.push('/order/cart')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || e?.message || '复购失败')
  } finally {
    reorderingId.value = null
  }
}

onMounted(loadOrders)
</script>
<style scoped>
.history { max-width:480px; margin:0 auto; padding:16px; }
.head { display:flex; justify-content:space-between; align-items:center; }
.tip { color:#999; font-size:13px; margin-top:12px; }
.order { margin-top:14px; padding-top:14px; border-top:1px solid #f0f0f0; }
.row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.ono { font-size:12px; color:#666; }
.summary { margin-top:6px; font-weight:600; }
.foot { margin-top:8px; }
.meta { color:#999; font-size:12px; }
</style>
