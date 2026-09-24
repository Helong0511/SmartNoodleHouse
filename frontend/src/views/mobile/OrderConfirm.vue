<template>
  <div class="confirm-page">
    <h2>确认订单</h2>

    <div v-if="tableLabel" class="card">
      <div class="label">堂食桌号</div>
      <div class="table">{{ tableLabel }}</div>
    </div>

    <div class="card">
      <div class="label">已选商品</div>
      <div v-for="(item, idx) in cart" :key="idx" class="line">
        <img v-if="item.imageUrl" :src="item.imageUrl" class="mini" alt="" />
        <div v-else class="mini placeholder">{{ (item.name || '餐')[0] }}</div>
        <span class="name">
          {{ item.name }} ×{{ item.quantity || 1 }}
          <span v-if="item.option_text" class="opts">（{{ item.option_text }}）</span>
        </span>
        <span class="amt">¥{{ item.subtotal }}</span>
      </div>
      <div class="sum"><span>小计</span><span>¥{{ subtotal }}</span></div>
      <div v-if="hasDiscount" class="sum discount-row">
        <span>会员优惠{{ memberDiscountText ? `（${memberDiscountText}）` : '' }}</span>
        <span>-¥{{ discountAmount }}</span>
      </div>
      <div v-if="needPack" class="sum"><span>打包费（预估）</span><span>¥{{ packingFee }}</span></div>
      <div class="sum total"><span>应付约</span><span>¥{{ payEstimate }}</span></div>
    </div>

    <div class="card">
      <div class="label">用餐方式</div>
      <div class="pack-row">
        <button type="button" :class="['pack-opt', { on: !needPack }]" @click="needPack = false">堂食</button>
        <button type="button" :class="['pack-opt', { on: needPack }]" @click="needPack = true">打包外带</button>
      </div>
      <p v-if="needPack" class="tip">打包按商品餐盒费加收，以服务器结算为准</p>
    </div>

    <el-input v-model="remark" placeholder="备注" style="margin:12px 0" />
    <p>支付方式: {{ channelLabel }}</p>
    <el-button type="primary" :loading="loading" style="width:100%" @click="submit">提交订单并支付</el-button>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { getCustomerToken, setCustomerToken } from '@/utils/customerAuth'
import { mediaUrl } from '@/utils/media'

const router = useRouter()
const remark = ref('')
const loading = ref(false)
const needPack = ref(false)
const tableLabel = ref('')
const channelRaw = localStorage.getItem('snh_channel') || localStorage.getItem('channel') || 'wechat'
const channel = channelRaw.toUpperCase() === 'ALIPAY' ? 'ALIPAY' : 'WECHAT'
const channelLabel = channel === 'ALIPAY' ? '支付宝' : '微信'
const cart = ref<any[]>(JSON.parse(localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]'))
const memberRate = ref(1)
const isMember = ref(false)
const memberLevelName = ref('')
const memberDiscountText = ref('')

function discountText(rate: number) {
  if (!Number.isFinite(rate) || rate >= 1) return ''
  return `${Math.round(rate * 100) / 10}折`
}

const subtotal = computed(() =>
  cart.value.reduce((s, i) => s + parseFloat(i.subtotal || i.price || 0), 0).toFixed(2),
)
const packingFee = computed(() => {
  const fee = cart.value.reduce((s, i) => {
    const box = parseFloat(i.box_price || 0) || 0
    return s + box * (i.quantity || 1)
  }, 0)
  return fee.toFixed(2)
})
const discountAmount = computed(() => {
  const sub = cart.value.reduce((s, i) => {
    if (i.no_member_discount) return s
    return s + parseFloat(i.subtotal || i.price || 0)
  }, 0)
  const rate = memberRate.value
  if (rate > 0 && rate < 1) {
    return (Math.round(sub * (1 - rate) * 100) / 100).toFixed(2)
  }
  return '0.00'
})
const hasDiscount = computed(() => parseFloat(discountAmount.value) > 0)
const payEstimate = computed(() => {
  const sub = parseFloat(subtotal.value)
  const pack = needPack.value ? parseFloat(packingFee.value) : 0
  const disc = parseFloat(discountAmount.value)
  return Math.max(0, Math.round((sub - disc + pack) * 100) / 100).toFixed(2)
})

const checkoutNonce = ref('')

function newCheckoutNonce() {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}

function idempotencyKey(): string {
  const table = localStorage.getItem('snh_table_token') || localStorage.getItem('table_token') || ''
  const raw = JSON.stringify({
    table,
    channel,
    needPack: needPack.value,
    items: cart.value.map((c: any) => ({ p: c.product_id, q: c.quantity, o: c.option_ids || [] })),
  })
  let h = 0
  for (let i = 0; i < raw.length; i++) h = (Math.imul(31, h) + raw.charCodeAt(i)) | 0
  const content = Math.abs(h).toString(16)
  if (!checkoutNonce.value) checkoutNonce.value = newCheckoutNonce()
  return `idem_${content}_${checkoutNonce.value}_${cart.value.length}`
}

async function submit() {
  if (!cart.value.length) {
    ElMessage.warning('购物车为空')
    return
  }
  const tableToken = localStorage.getItem('snh_table_token') || localStorage.getItem('table_token')
  if (!tableToken) {
    ElMessage.warning('请先扫描桌台二维码')
    return
  }
  loading.value = true
  try {
    if (!checkoutNonce.value) checkoutNonce.value = newCheckoutNonce()
    const orderRes = await api.post('/orders', {
      table_token: tableToken,
      channel,
      need_pack: needPack.value,
      dining_type: needPack.value ? 'TAKEAWAY' : 'DINE_IN',
      remark: remark.value,
      items: cart.value.map((c: any) => ({
        product_id: c.product_id,
        quantity: c.quantity || 1,
        option_ids: c.option_ids || [],
      })),
      idempotency_key: idempotencyKey(),
    }, { headers: { 'X-Client': 'customer' } })
    const orderId = orderRes.data.id
    const payRes = (
      await api.post(
        `/payments/orders/${orderId}/pay`,
        { provider: channel, channel_type: channel === 'ALIPAY' ? 'qr' : 'native' },
        { headers: { 'X-Client': 'customer' } },
      )
    ).data
    if (!payRes?.mock_settled && !payRes?._mock) {
      ElMessage.warning('当前为 H5 演示：生产环境需展示支付二维码并轮询结果；测试环境请保持未配置支付密钥以自动 mock')
    }
    localStorage.removeItem('snh_cart')
    localStorage.removeItem('cart')
    localStorage.removeItem('snh_cart_store_id')
    checkoutNonce.value = ''
    const pts = payRes?.points_earned ? `&points_earned=${payRes.points_earned}` : ''
    router.push(`/order/result?order_id=${orderId}${pts}`)
  } catch (e: any) {
    if (/幂等键.*已结束|重新下单/.test(e?.message || '') || e?.code === 40903) {
      checkoutNonce.value = ''
    }
    ElMessage.error(e.message || '下单失败')
  } finally {
    loading.value = false
  }
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

async function loadMember() {
  try {
    await ensureLogin()
    const profile = (await api.get('/miniprogram/customers/me', { headers: { 'X-Client': 'customer' } })).data
    const rate = profile?.is_member && profile.member_discount_rate
      ? parseFloat(profile.member_discount_rate)
      : 1
    memberRate.value = Number.isFinite(rate) && rate > 0 && rate < 1 ? rate : 1
    isMember.value = !!profile?.is_member
    memberLevelName.value = profile?.member_level_name || ''
    memberDiscountText.value = discountText(memberRate.value)
  } catch {
    memberRate.value = 1
    isMember.value = false
    memberLevelName.value = ''
    memberDiscountText.value = ''
  }
}

onMounted(async () => {
  await loadMember()
  cart.value = cart.value.map((c) => ({
    ...c,
    imageUrl: c.imageUrl || mediaUrl(c.main_image),
  }))
  const token = localStorage.getItem('snh_table_token') || localStorage.getItem('table_token')
  if (token) {
    try {
      const entry = await api.get('/miniprogram/entry', { params: { token, channel: channelRaw } })
      const t = entry.data?.table
      if (t) tableLabel.value = t.table_name || t.table_no || ''
    } catch {
      /* ignore */
    }
  }
  // 补全餐盒费
  for (const item of cart.value) {
    if (item.box_price != null) continue
    try {
      const p = (await api.get(`/products/${item.product_id}`)).data
      item.box_price = p.box_price || '0'
      item.no_member_discount = !!p.no_member_discount
      if (!item.imageUrl) item.imageUrl = mediaUrl(p.main_image)
    } catch {
      item.box_price = '0'
    }
  }
})
</script>
<style scoped>
.confirm-page { max-width:480px; margin:0 auto; padding:16px; }
.card { background:#fff; border-radius:12px; padding:14px; margin-bottom:12px; border:1px solid #f0f0f0; }
.label { color:#666; font-size:13px; margin-bottom:8px; }
.table { font-size:20px; font-weight:700; color:#ff6b35; }
.line { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid #f5f5f5; }
.mini { width:40px; height:40px; border-radius:6px; object-fit:cover; flex-shrink:0; }
.mini.placeholder {
  width:40px; height:40px; border-radius:6px; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
  background:#ffe8df; color:#ff6b35; font-weight:700;
}
.name { flex:1; font-size:14px; }
.opts { color:#888; font-size:12px; }
.amt { color:#ff6b35; font-weight:600; }
.sum { display:flex; justify-content:space-between; margin-top:8px; color:#666; font-size:14px; }
.sum.total { color:#333; font-weight:700; font-size:16px; margin-top:12px; }
.discount-row { color:#f56c6c; }
.pack-row { display:flex; gap:10px; }
.pack-opt {
  flex:1; padding:10px 0; border-radius:8px; border:1px solid #ddd; background:#fff;
  color:#666; cursor:pointer;
}
.pack-opt.on { border-color:#ff6b35; color:#ff6b35; background:#fff5f0; font-weight:600; }
.tip { color:#999; font-size:12px; margin-top:8px; }
</style>
