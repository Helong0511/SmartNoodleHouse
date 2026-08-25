<template>
  <div class="result-page">
    <div class="icon">✅</div>
    <h2>订单已提交</h2>
    <p>订单号: {{ order?.order_no }}</p>
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
onMounted(async () => { order.value = (await api.get(`/orders/${route.query.order_id}`)).data })
</script>
<style scoped>
.result-page { max-width:480px; margin:0 auto; padding:40px 16px; text-align:center; }
.icon { font-size: 64px; }
</style>
