<template>
  <div v-loading="loading">
    <el-button link @click="$router.back()">← 返回</el-button>
    <template v-if="order">
      <h3 style="margin:12px 0">{{ order.order_no }}</h3>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="门店">
          {{ order.store_name || (order.store_id ? `门店#${order.store_id}` : '—') }}
        </el-descriptions-item>
        <el-descriptions-item label="订单状态">
          <el-tag :type="orderStatusTagType(order.status)">{{ label(ORDER_STATUS, order.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="支付状态">
          <el-tag :type="paymentStatusTagType(order.payment_status)">{{ label(PAYMENT_STATUS, order.payment_status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="渠道">{{ label(CHANNEL, order.channel) }}</el-descriptions-item>
        <el-descriptions-item label="桌号">
          {{ order.table?.table_name || order.table?.table_no || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="用餐方式">
          <el-tag v-if="order.dining_type === 'TAKEAWAY'" type="warning" size="small">打包外带</el-tag>
          <span v-else>堂食</span>
        </el-descriptions-item>
        <el-descriptions-item label="金额">¥{{ order.total_amount }}</el-descriptions-item>
        <el-descriptions-item label="小计">¥{{ order.subtotal }}</el-descriptions-item>
        <el-descriptions-item label="打包费">
          ¥{{ order.packing_fee || '0.00' }}
        </el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ order.remark || '—' }}</el-descriptions-item>
      </el-descriptions>

      <div class="actions">
        <el-button
          v-for="act in nextActions"
          :key="act.status"
          :type="act.type as any"
          @click="updateStatus(act.status)"
        >
          {{ act.label }}
        </el-button>
        <el-button v-if="canCancel" type="danger" plain @click="updateStatus('CANCELLED')">取消订单</el-button>
      </div>

      <el-table :data="order.items" style="margin-top:16px" border>
        <el-table-column prop="product_name" label="商品" />
        <el-table-column label="规格" min-width="160">
          <template #default="{ row }">
            <span v-if="row.options?.length">
              {{ row.options.map((o: any) => `${o.group}:${o.name}`).join(' / ') }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="unit_price" label="单价" width="90" />
        <el-table-column prop="quantity" label="数量" width="70" />
        <el-table-column prop="subtotal" label="小计" width="90" />
      </el-table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import {
  CHANNEL,
  cancelAllowed,
  label,
  ORDER_NEXT_ACTIONS,
  ORDER_STATUS,
  PAYMENT_STATUS,
  orderStatusTagType,
  paymentStatusTagType,
} from '@/utils/labels'

const route = useRoute()
const loading = ref(false)
const order = ref<any>(null)

const nextActions = computed(() => ORDER_NEXT_ACTIONS[order.value?.status] || [])
const canCancel = computed(() => order.value && cancelAllowed(order.value.status))

async function load() {
  loading.value = true
  try {
    order.value = (await api.get(`/orders/${route.params.id}`)).data
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function updateStatus(status: string) {
  const name = label(ORDER_STATUS, status)
  if (status === 'CANCELLED') {
    await ElMessageBox.confirm('确定取消该订单？', '确认')
  }
  try {
    await api.patch(`/orders/${route.params.id}/status`, { status })
    ElMessage.success(`已更新为：${name}`)
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.actions { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.muted { color: #999; }
</style>
