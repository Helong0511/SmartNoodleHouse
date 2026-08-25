<template>
  <el-table :data="orders">
    <el-table-column prop="order_no" label="订单号" />
    <el-table-column label="渠道" width="120">
      <template #default="{ row }">
        <el-tag size="small" :type="channelType(row.channel)">{{ channelLabel(row.channel) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="total_amount" label="金额" width="100" />
    <el-table-column prop="status" label="状态" width="120" />
    <el-table-column prop="payment_status" label="支付" width="100" />
    <el-table-column label="操作" width="100">
      <template #default="{ row }"><el-button link type="primary" @click="$router.push(`/orders/${row.id}`)">详情</el-button></template>
    </el-table-column>
  </el-table>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api'
const orders = ref<any[]>([])

function channelLabel(c: string) {
  return ({ WECHAT: '微信堂食', ALIPAY: '支付宝堂食', MEITUAN: '美团', ELEME: '饿了么' } as Record<string, string>)[c] || c || '堂食'
}
function channelType(c: string) {
  return ({ MEITUAN: 'danger', ELEME: 'warning', WECHAT: 'success', ALIPAY: '' } as Record<string, string>)[c] || 'info'
}

onMounted(async () => { orders.value = (await api.get('/orders')).data.items })
</script>
