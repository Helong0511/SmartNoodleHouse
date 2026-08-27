<template>
  <div class="confirm-page">
    <h2>确认订单</h2>
    <el-input v-model="remark" placeholder="备注" style="margin:12px 0" />
    <p>支付方式: {{ channel === 'alipay' ? '支付宝' : '微信' }}</p>
    <el-button type="primary" :loading="loading" style="width:100%" @click="submit">提交订单并支付</el-button>
  </div>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'

const router = useRouter()
const remark = ref('')
const loading = ref(false)
const channel = localStorage.getItem('snh_channel') || localStorage.getItem('channel') || 'wechat'
const cart = JSON.parse(localStorage.getItem('snh_cart') || localStorage.getItem('cart') || '[]')

function idempotencyKey(): string {
  const table = localStorage.getItem('snh_table_token') || localStorage.getItem('table_token') || ''
  const raw = JSON.stringify({
    table,
    channel,
    items: cart.map((c: any) => ({ p: c.product_id, q: c.quantity, o: c.option_ids || [] })),
  })
  // 稳定短 hash，避免 Date.now() 导致重复下单
  let h = 0
  for (let i = 0; i < raw.length; i++) h = (Math.imul(31, h) + raw.charCodeAt(i)) | 0
  return `idem_${Math.abs(h).toString(16)}_${cart.length}`
}

async function submit() {
  if (!cart.length) {
    ElMessage.warning('购物车为空')
    return
  }
  loading.value = true
  try {
    const orderRes = await api.post('/orders', {
      table_token: localStorage.getItem('snh_table_token') || localStorage.getItem('table_token'),
      channel,
      remark: remark.value,
      items: cart.map((c: any) => ({
        product_id: c.product_id,
        quantity: c.quantity || 1,
        option_ids: c.option_ids || [],
      })),
      idempotency_key: idempotencyKey(),
    }, { headers: { 'X-Client': 'customer' } })
    const orderId = orderRes.data.id
    const provider = channel === 'alipay' ? 'ALIPAY' : 'WECHAT'
    await api.post(
      `/payments/orders/${orderId}/pay`,
      { provider, channel_type: channel === 'alipay' ? 'qr' : 'native' },
      { headers: { 'X-Client': 'customer' } },
    )
    localStorage.removeItem('snh_cart')
    localStorage.removeItem('cart')
    router.push(`/order/result?order_id=${orderId}`)
  } catch (e: any) {
    ElMessage.error(e.message || '下单失败')
  } finally {
    loading.value = false
  }
}
</script>
<style scoped>.confirm-page { max-width:480px; margin:0 auto; padding:16px; }</style>
