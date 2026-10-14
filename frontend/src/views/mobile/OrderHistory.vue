<template>
  <div class="history">
    <div class="head">
      <h2>历史订单</h2>
      <el-button link type="primary" @click="$router.push('/member')">会员中心</el-button>
    </div>
    <div class="tabs">
      <span
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: activeTab === t.key }"
        @click="switchTab(t.key)"
      >{{ t.label }}</span>
    </div>
    <p v-if="loading" class="tip">加载中...</p>
    <p v-else-if="!orders.length" class="tip">暂无历史订单，去点餐后再来看看。</p>
    <div v-for="o in orders" :key="o.id" class="order">
      <div class="row">
        <span class="ono">{{ o.order_no }}</span>
        <el-tag size="small">{{ orderStatusText(o) }}</el-tag>
      </div>
      <div class="summary">{{ o.item_summary }}</div>
      <div class="row foot">
        <span class="meta">
          {{ formatTime(o.created_at) }} · ¥{{ o.total_amount }}
          <span v-if="Number(o.discount_amount) > 0 || Number(o.coupon_discount) > 0" class="discount">
            （已优惠¥{{ (Number(o.discount_amount) + Number(o.coupon_discount || 0)).toFixed(2) }}）
          </span>
        </span>
        <el-button
          size="small"
          type="primary"
          :disabled="!o.can_reorder || reorderingId === o.id"
          :loading="reorderingId === o.id"
          @click="reorder(o.id)"
        >再来一单</el-button>
      </div>
    </div>
    <div v-if="orders.length" class="more">
      <el-button
        v-if="hasMore"
        plain
        style="width:100%"
        :loading="loadingMore"
        @click="loadMore"
      >{{ loadingMore ? '加载中…' : '加载更多' }}</el-button>
      <span v-else class="nomore">没有更多了</span>
    </div>
    <el-button style="margin-top:16px;width:100%" @click="$router.push('/order')">去点餐</el-button>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { getCustomerToken, setCustomerToken } from '@/utils/customerAuth'
import { ORDER_STATUS, label } from '@/utils/labels'

function orderStatusText(o: any) {
  return label(ORDER_STATUS, o?.status, o?.status_label || '—')
}

const orders = ref<any[]>([])
const loading = ref(false)
const loadingMore = ref(false)
const page = ref(1)
const pageSize = 10
const hasMore = ref(false)
const reorderingId = ref<number | null>(null)
const router = useRouter()

// tab key 即 /orders/mine 的 status（逗号分隔多状态），与小程序端口径保持一致
const tabs = [
  { key: '', label: '全部' },
  { key: 'PENDING_PAYMENT', label: '待支付' },
  { key: 'PAID,CONFIRMED,PREPARING,COOKING,READY', label: '进行中' },
  { key: 'COMPLETED', label: '已完成' },
  { key: 'CANCELLED,REFUND_PENDING,REFUNDED', label: '取消/退款' },
]
const activeTab = ref('')

function switchTab(key: string) {
  if (key === activeTab.value) return
  activeTab.value = key
  loadOrders()
}

function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function ensureLogin() {
  if (getCustomerToken()) return
  // 生产禁止伪造 login code
  if (import.meta.env.PROD) {
    throw new Error('生产环境请使用小程序登录')
  }
  const code = localStorage.getItem('h5_login_code') || `h5_member_${Date.now()}`
  localStorage.setItem('h5_login_code', code)
  const res = await api.post('/miniprogram/wechat/login', { code })
  setCustomerToken(res.data.customer_token, res.data.customer_id)
}

async function loadOrders() {
  loading.value = true
  page.value = 1
  try {
    await ensureLogin()
    const res = await api.get('/orders/mine', {
      params: { page: page.value, page_size: pageSize, status: activeTab.value || undefined },
    })
    const list = res.data.items || []
    orders.value = list
    hasMore.value = list.length >= pageSize
  } catch (e: any) {
    orders.value = []
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value) return
  loadingMore.value = true
  try {
    const next = page.value + 1
    const res = await api.get('/orders/mine', {
      params: { page: next, page_size: pageSize, status: activeTab.value || undefined },
    })
    const list = res.data.items || []
    // 去重拼接，防止并发翻页边界重复
    const known = new Set(orders.value.map((o) => o.id))
    orders.value.push(...list.filter((o: any) => !known.has(o.id)))
    if (list.length < pageSize) {
      hasMore.value = false
    } else {
      page.value = next
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loadingMore.value = false
  }
}

async function reorder(orderId: number) {
  reorderingId.value = orderId
  try {
    const res = await api.post(`/orders/${orderId}/reorder`)
    const items = res.data.cart_items || []
    if (!items.length) {
      ElMessage.warning('没有可复购餐品')
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
    const reorderStoreId = res.data.store_id
    if (reorderStoreId != null && Number(reorderStoreId) > 0) {
      localStorage.setItem('snh_cart_store_id', String(Number(reorderStoreId)))
    }
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
.tabs { display:flex; gap:8px; overflow-x:auto; margin:10px 0 2px; padding-bottom:4px; }
.tab { flex:0 0 auto; padding:4px 14px; border-radius:14px; background:#f5f5f5; color:#666; font-size:13px; }
.tab.active { background:#ff6b35; color:#fff; font-weight:600; }
.tip { color:#999; font-size:13px; margin-top:12px; }
.order { margin-top:14px; padding-top:14px; border-top:1px solid #f0f0f0; }
.row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.ono { font-size:12px; color:#666; }
.summary { margin-top:6px; font-weight:600; }
.foot { margin-top:8px; }
.meta { color:#999; font-size:12px; }
.discount { color:#f56c6c; margin-left:4px; }
.more { margin-top:14px; text-align:center; }
.nomore { color:#bbb; font-size:12px; }
</style>
