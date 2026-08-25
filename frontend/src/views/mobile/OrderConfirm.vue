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
const channel = localStorage.getItem('channel') || 'wechat'
const cart = JSON.parse(localStorage.getItem('cart') || '[]')

async function submit() {
  loading.value = true
  try {
    const orderRes = await api.post('/orders', {
      table_token: localStorage.getItem('table_token'),
      channel,
      remark: remark.value,
      items: cart.map((c: any) => ({ product_id: c.product_id, quantity: c.quantity || 1, option_ids: c.option_ids || [] })),
      idempotency_key: `idem_${Date.now()}`
    })
    const orderId = orderRes.data.id
    const provider = channel === 'alipay' ? 'ALIPAY' : 'WECHAT'
    await api.post(`/payments/orders/${orderId}/pay`, { provider, channel_type: channel === 'alipay' ? 'qr' : 'native' })
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
