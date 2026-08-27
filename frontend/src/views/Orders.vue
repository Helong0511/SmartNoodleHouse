<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="reload"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-select v-model="statusFilter" placeholder="订单状态" clearable style="width:130px" @change="reload">
          <el-option v-for="(label, key) in ORDER_STATUS" :key="key" :label="label" :value="key" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="paymentFilter" placeholder="支付状态" clearable style="width:130px" @change="reload">
          <el-option v-for="(label, key) in PAYMENT_STATUS" :key="key" :label="label" :value="key" />
        </el-select>
      </el-form-item>
      <el-button @click="load">刷新</el-button>
    </el-form>

    <el-table :data="orders" border>
      <el-table-column prop="order_no" label="订单号" min-width="180" show-overflow-tooltip />
      <el-table-column prop="store_name" label="门店" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column label="桌号" width="100">
        <template #default="{ row }">
          {{ row.table_name || row.table_no || (row.channel === 'MEITUAN' || row.channel === 'ELEME' ? '外卖' : '—') }}
        </template>
      </el-table-column>
      <el-table-column label="用餐" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.dining_type === 'TAKEAWAY'" size="small" type="warning">打包</el-tag>
          <span v-else-if="row.channel === 'MEITUAN' || row.channel === 'ELEME'">—</span>
          <span v-else>堂食</span>
        </template>
      </el-table-column>
      <el-table-column label="渠道" width="110">
        <template #default="{ row }">
          <el-tag size="small">{{ label(CHANNEL, row.channel) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="total_amount" label="金额" width="100">
        <template #default="{ row }">
          ¥{{ row.total_amount }}
          <div v-if="Number(row.packing_fee) > 0" class="fee-tip">含打包 ¥{{ row.packing_fee }}</div>
        </template>
      </el-table-column>
      <el-table-column label="订单状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="orderStatusTagType(row.status)">
            {{ label(ORDER_STATUS, row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="支付状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="paymentStatusTagType(row.payment_status)">
            {{ label(PAYMENT_STATUS, row.payment_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="下单时间" width="170" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="$router.push(`/orders/${row.id}`)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  CHANNEL,
  label,
  ORDER_STATUS,
  PAYMENT_STATUS,
  orderStatusTagType,
  paymentStatusTagType,
} from '@/utils/labels'

const auth = useAuthStore()
const loading = ref(false)
const orders = ref<any[]>([])
const stores = ref<any[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const storeFilter = ref<number | undefined>()
const statusFilter = ref<string | undefined>()
const paymentFilter = ref<string | undefined>()

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function reload() {
  page.value = 1
  load()
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/orders', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        store_id: storeFilter.value,
        status: statusFilter.value,
        payment_status: paymentFilter.value,
      },
    })
    orders.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.fee-tip { font-size: 11px; color: #e6a23c; line-height: 1.2; }
</style>
