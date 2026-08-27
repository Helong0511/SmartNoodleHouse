<template>
  <div class="result-page">
    <div class="icon">✓</div>
    <h2>订单已提交</h2>
    <p>订单号: {{ order?.order_no }}</p>
    <p v-if="order?.table">桌号: {{ order.table.table_name || order.table.table_no }}</p>
    <p>用餐: {{ order?.dining_type === 'TAKEAWAY' ? '打包外带' : '堂食' }}</p>
    <p v-if="Number(order?.packing_fee) > 0">打包费: ¥{{ order.packing_fee }}</p>
    <p>金额: ¥{{ order?.total_amount }}</p>
    <p>状态: {{ order?.payment_status }}</p>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '@/api'
const route = useRoute()
const order = ref<any>(null)
onMounted(async () => {
  order.value = (await api.get(`/orders/${route.query.order_id}`, { headers: { 'X-Client': 'customer' } })).data
})
</script>
<style scoped>
.result-page { max-width:480px; margin:0 auto; padding:40px 16px; text-align:center; }
.icon {
  width:72px; height:72px; line-height:72px; margin:0 auto 16px; border-radius:50%;
  background:#e8f8ef; color:#2ea043; font-size:36px; font-weight:700;
}
</style>
