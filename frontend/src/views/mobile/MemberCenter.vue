<template>
  <div class="member">
    <h2>会员中心</h2>
    <el-switch v-model="faceEnabled" active-text="人脸迎宾（需小程序摄像头）" @change="toggleFace" />
    <p class="tip">H5 无法安全采集人脸。请在微信/支付宝小程序内打开摄像头实时授权；关闭可撤销特征。</p>

    <div v-if="recommendations.length" class="block">
      <h3>智能记录的口味爱好</h3>
      <p class="tip">根据您历史订单自动累计，点餐时会智能预选常点规格。</p>
      <el-tag v-for="r in recommendations" :key="r.option_code + r.category" style="margin:4px">
        {{ r.category }} · {{ r.option_name || r.option_code }}
      </el-tag>
      <el-button type="primary" style="margin-top:12px" @click="goOrder">按爱好去点餐</el-button>
    </div>
    <p v-else class="tip">完成订单后，系统将智能记录您的口味爱好。</p>

    <div class="block">
      <div class="head">
        <h3>历史订单</h3>
        <el-button link type="primary" @click="loadOrders">刷新</el-button>
      </div>
      <p v-if="ordersLoading" class="tip">加载中...</p>
      <p v-else-if="!orders.length" class="tip">暂无订单。完成后可在此再来一单。</p>
      <div v-for="o in orders" :key="o.id" class="order">
        <div class="row">
          <span class="ono">{{ o.order_no }}</span>
          <el-tag size="small" type="warning">{{ o.status_label || o.status }}</el-tag>
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
      <el-button style="margin-top:12px" @click="$router.push('/order/history')">查看全部历史</el-button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'

const faceEnabled = ref(false)
const recommendations = ref<any[]>([])
const orders = ref<any[]>([])
const ordersLoading = ref(false)
const reorderingId = ref<number | null>(null)
const customerId = ref<number | null>(null)
const router = useRouter()

function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function ensureCustomer() {
  const code = localStorage.getItem('h5_login_code') || `h5_member_${Date.now()}`
  localStorage.setItem('h5_login_code', code)
  const res = await api.post('/miniprogram/wechat/login', { code })
  customerId.value = res.data.customer_id
  localStorage.setItem('customer_token', res.data.customer_token)
  localStorage.setItem('customer_id', String(res.data.customer_id))
  if (res.data.nickname) localStorage.setItem('nickname', res.data.nickname)
  return res.data
}

async function toggleFace(val: boolean) {
  if (!customerId.value) return
  if (val) {
    faceEnabled.value = false
    ElMessage.warning('请使用微信/支付宝小程序摄像头实时采集，H5 不支持选图或假图授权')
    return
  }
  try {
    await api.post('/miniprogram/face/revoke', {})
    ElMessage.success('已关闭人脸迎宾')
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function loadOrders() {
  ordersLoading.value = true
  try {
    const res = await api.get('/orders/mine', { params: { page: 1, page_size: 10 } })
    orders.value = res.data.items || []
  } catch (_) {
    orders.value = []
  } finally {
    ordersLoading.value = false
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

function goOrder() {
  router.push('/order')
}

onMounted(async () => {
  await ensureCustomer()
  const prefs = await api.get(`/customers/${customerId.value}/preferences`)
  recommendations.value = prefs.data.preferences || []
  try {
    const face = await api.get('/miniprogram/customers/me/face-status')
    faceEnabled.value = !!(face.data && face.data.has_face)
  } catch (_) {}
  await loadOrders()
})
</script>
<style scoped>
.member { max-width:480px; margin:0 auto; padding:16px; }
.tip { color:#999; font-size:13px; margin-top:8px; line-height:1.5; }
.block { margin-top:20px; }
.head { display:flex; justify-content:space-between; align-items:center; }
.order { margin-top:12px; padding-top:12px; border-top:1px solid #f0f0f0; }
.row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.ono { font-size:12px; color:#666; }
.summary { margin-top:6px; font-weight:600; }
.foot { margin-top:8px; }
.meta { color:#999; font-size:12px; }
</style>
