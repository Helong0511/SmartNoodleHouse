<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="6" v-for="item in cards" :key="item.label">
        <el-card shadow="hover"><div class="stat">{{ item.value }}</div><div class="label">{{ item.label }}</div></el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="12">
        <el-card><h3>热销商品 TOP10</h3>
          <el-table :data="data?.hot_products || []" size="small">
            <el-table-column prop="name" label="商品" /><el-table-column prop="sales" label="销量" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card><h3>支付方式</h3>
          <p>微信: {{ data?.payment_ratio?.wechat || 0 }} 笔</p>
          <p>支付宝: {{ data?.payment_ratio?.alipay || 0 }} 笔</p>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api'

const data = ref<any>(null)

const cards = computed(() => [
  { label: '今日订单', value: data.value?.today_orders ?? '-' },
  { label: '今日营业额', value: data.value?.today_revenue ? `¥${data.value.today_revenue}` : '-' },
  { label: '今日客流', value: data.value?.today_visitors ?? '-' },
  { label: '平均客单价', value: data.value?.avg_order_amount ? `¥${data.value.avg_order_amount}` : '-' }
])

onMounted(async () => {
  const res = await api.get('/dashboard/overview')
  data.value = res.data
})
</script>

<style scoped>
.stat { font-size: 28px; font-weight: bold; color: #409eff; }
.label { color: #999; margin-top: 8px; }
</style>
