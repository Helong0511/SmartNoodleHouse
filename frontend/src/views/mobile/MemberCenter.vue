<template>
  <div class="member">
    <h2>会员中心</h2>

    <div v-if="profile" class="block profile-card">
      <div class="profile-row">
        <span class="nickname">{{ profile.nickname || '面馆会员' }}</span>
        <el-tag v-if="profile.is_member" size="small" type="success">{{ profile.member_level_name || '会员' }}</el-tag>
        <el-tag v-else size="small" type="info">未注册</el-tag>
      </div>
      <div v-if="profile.is_member" class="profile-meta">
        <span>积分 {{ profile.member_points ?? 0 }}</span>
        <span v-if="memberDiscountText">· {{ memberDiscountText }}</span>
        <span v-if="profile.phone">· {{ profile.phone }}</span>
      </div>
    </div>

    <div class="block">
      <div class="head">
        <h3>我的优惠券</h3>
        <el-button link type="primary" @click="loadWallet">刷新</el-button>
      </div>
      <p v-if="walletLoading" class="tip">加载中...</p>
      <p v-else-if="!coupons.length" class="tip">暂无可用优惠券</p>
      <div v-for="c in coupons" :key="c.id" class="wallet-item coupon">
        <div class="wallet-main">
          <span class="wallet-name">{{ c.name }}</span>
          <span class="wallet-rule">{{ c.summary }}</span>
        </div>
        <span class="wallet-expire">{{ formatExpire(c.expires_at) }}</span>
      </div>
    </div>

    <div class="block">
      <div class="head">
        <h3>我的代金券</h3>
      </div>
      <p v-if="walletLoading" class="tip">加载中...</p>
      <p v-else-if="!vouchers.length" class="tip">暂无可用代金券</p>
      <div v-for="v in vouchers" :key="v.id" class="wallet-item voucher">
        <div class="wallet-main">
          <span class="wallet-name">{{ v.name }}</span>
          <span class="wallet-rule">{{ v.summary }}</span>
        </div>
        <span class="wallet-expire">{{ formatExpire(v.expires_at) }}</span>
      </div>
    </div>

    <el-switch v-model="faceEnabled" active-text="人脸迎宾（需小程序摄像头）" @change="toggleFace" />
    <p class="tip">H5 无法安全采集人脸。请在微信/支付宝小程序内打开摄像头完成动作挑战与实拍授权；关闭可撤销特征。</p>

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
          <el-tag size="small" type="warning">{{ orderStatusText(o) }}</el-tag>
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
      <el-button style="margin-top:12px" @click="$router.push('/order/history')">查看全部历史</el-button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { setCustomerToken } from '@/utils/customerAuth'
import { ORDER_STATUS, label } from '@/utils/labels'

function orderStatusText(o: any) {
  return label(ORDER_STATUS, o?.status, o?.status_label || '—')
}

const faceEnabled = ref(false)
const profile = ref<any>(null)
const coupons = ref<any[]>([])
const vouchers = ref<any[]>([])
const walletLoading = ref(false)
const recommendations = ref<any[]>([])
const orders = ref<any[]>([])
const ordersLoading = ref(false)
const reorderingId = ref<number | null>(null)
const customerId = ref<number | null>(null)
const router = useRouter()

const memberDiscountText = computed(() => {
  const rate = Number(profile.value?.member_discount_rate)
  if (!Number.isFinite(rate) || rate >= 1) return ''
  return `${Math.round(rate * 100) / 10}折`
})

function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ')
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatExpire(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return `至 ${String(iso).slice(0, 10)}`
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`)
  return `至 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

async function loadWallet() {
  walletLoading.value = true
  try {
    const [cp, vp, me] = await Promise.all([
      api.get('/miniprogram/coupons/mine'),
      api.get('/miniprogram/vouchers/mine'),
      api.get('/miniprogram/customers/me'),
    ])
    coupons.value = cp.data?.items || []
    vouchers.value = vp.data?.items || []
    profile.value = me.data || null
  } catch (_) {
    coupons.value = []
    vouchers.value = []
  } finally {
    walletLoading.value = false
  }
}

async function ensureCustomer() {
  // 生产禁止伪造 login code
  if (import.meta.env.PROD) {
    throw new Error('生产环境请使用小程序登录')
  }
  const code = localStorage.getItem('h5_login_code') || `h5_member_${Date.now()}`
  localStorage.setItem('h5_login_code', code)
  const res = await api.post('/miniprogram/wechat/login', { code })
  customerId.value = res.data.customer_id
  setCustomerToken(res.data.customer_token, res.data.customer_id)
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

function goOrder() {
  router.push('/order')
}

onMounted(async () => {
  await ensureCustomer()
  await loadWallet()
  let storeId: number | undefined
  try {
    const raw = localStorage.getItem('snh_table')
    if (raw) {
      const t = JSON.parse(raw)
      if (t?.store_id) storeId = Number(t.store_id)
    }
  } catch (_) {}
  const prefs = await api.get(`/customers/${customerId.value}/preferences`, {
    params: storeId ? { store_id: storeId } : {},
  })
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
.profile-card { padding:12px 14px; background:#f8f9fb; border-radius:8px; }
.profile-row { display:flex; align-items:center; gap:8px; }
.nickname { font-weight:600; font-size:16px; }
.profile-meta { margin-top:6px; color:#666; font-size:13px; }
.wallet-item { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-top:10px; padding:10px 12px; border-radius:8px; border:1px solid #f0f0f0; }
.wallet-item.coupon { background:linear-gradient(90deg, #fff5f5 0%, #fff 40%); border-color:#fde2e2; }
.wallet-item.voucher { background:linear-gradient(90deg, #fff8f0 0%, #fff 40%); border-color:#ffe7ba; }
.wallet-main { display:flex; flex-direction:column; gap:4px; min-width:0; }
.wallet-name { font-weight:600; font-size:14px; }
.wallet-rule { color:#666; font-size:12px; }
.wallet-expire { flex-shrink:0; color:#999; font-size:12px; white-space:nowrap; }
.head { display:flex; justify-content:space-between; align-items:center; }
.order { margin-top:12px; padding-top:12px; border-top:1px solid #f0f0f0; }
.row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.ono { font-size:12px; color:#666; }
.summary { margin-top:6px; font-weight:600; }
.foot { margin-top:8px; }
.meta { color:#999; font-size:12px; }
.discount { color:#f56c6c; margin-left:4px; }
</style>
