<template>
  <div class="result-page">
    <div class="icon">✓</div>
    <h2>订单已提交</h2>
    <p>订单号: {{ order?.order_no }}</p>
    <p v-if="order?.store_name">门店: {{ order.store_name }}</p>
    <p v-if="order?.table">桌号: {{ order.table.table_name || order.table.table_no }}</p>
    <p>用餐: {{ order?.dining_type === 'TAKEAWAY' ? '打包外带' : '堂食' }}</p>
    <div v-if="order" class="amount-card">
      <div class="line"><span>商品小计</span><span>¥{{ order.subtotal }}</span></div>
      <div v-if="Number(order.discount_amount) > 0" class="line discount">
        <span>会员优惠{{ order.member_level_name ? `（${order.member_level_name}）` : '' }}</span>
        <span>-¥{{ order.discount_amount }}</span>
      </div>
      <div v-if="Number(order.coupon_discount) > 0" class="line discount">
        <span>优惠券</span>
        <span>-¥{{ order.coupon_discount }}</span>
      </div>
      <div v-if="Number(order.packing_fee) > 0" class="line">
        <span>打包费</span><span>¥{{ order.packing_fee }}</span>
      </div>
      <div class="line total"><span>应付</span><span>¥{{ order.total_amount }}</span></div>
    </div>
    <p>支付状态: {{ order?.payment_status_label || order?.payment_status }}</p>
    <el-button type="primary" style="margin-top:16px;width:100%" @click="$router.push('/order/history')">查看订单</el-button>
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
.amount-card {
  margin:16px 0; padding:12px 14px; background:#fafafa; border-radius:10px; text-align:left;
}
.line { display:flex; justify-content:space-between; padding:4px 0; font-size:14px; color:#666; }
.line.discount { color:#f56c6c; }
.line.total { margin-top:8px; padding-top:8px; border-top:1px solid #eee; font-weight:700; color:#333; }
</style>
