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
        <el-input
          v-model="keyword"
          clearable
          placeholder="订单号 / 桌号"
          style="width:200px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>
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
      <el-form-item>
        <el-select v-model="channelFilter" placeholder="渠道" clearable style="width:130px" @change="reload">
          <el-option v-for="(label, key) in CHANNEL" :key="key" :label="label" :value="key" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="diningFilter" placeholder="用餐方式" clearable style="width:120px" @change="reload">
          <el-option label="堂食" value="DINE_IN" />
          <el-option label="打包/外带" value="TAKEAWAY" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="下单起"
          end-placeholder="下单止"
          style="width:240px"
          @change="reload"
        />
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </el-form>

    <el-table :data="orders" border>
      <el-table-column prop="order_no" label="订单号" min-width="100" show-overflow-tooltip />
      <el-table-column prop="store_name" label="门店" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}
          <span v-if="row.store_no" class="fee-tip">{{ row.store_no }}</span>
        </template>
      </el-table-column>
      <el-table-column label="顾客" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <div>{{ row.customer_name || (row.customer_id ? `#${row.customer_id}` : '—') }}</div>
          <div class="fee-tip">
            <template v-if="row.is_member">
              {{ row.member_level_name || '会员' }}
              <span v-if="row.customer_phone"> · {{ row.customer_phone }}</span>
            </template>
            <template v-else-if="row.customer_no">{{ row.customer_no }}</template>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="桌号" width="100">
        <template #default="{ row }">
          {{ row.table_name || row.table_no || (row.channel === 'MEITUAN' || row.channel === 'ELEME' ? '外卖' : '—') }}
        </template>
      </el-table-column>
      <el-table-column label="用餐" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.channel === 'MEITUAN' || row.channel === 'ELEME'" size="small" type="info">外卖</el-tag>
          <el-tag v-else-if="row.dining_type === 'TAKEAWAY'" size="small" type="warning">打包</el-tag>
          <span v-else>堂食</span>
        </template>
      </el-table-column>
      <el-table-column label="渠道" width="130">
        <template #default="{ row }">
          <el-tag size="small">{{ label(CHANNEL, row.channel) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="total_amount" label="金额" width="120">
        <template #default="{ row }">
          ¥{{ row.total_amount }}
          <div v-if="Number(row.discount_amount) > 0" class="fee-tip discount">
            会员优惠 -¥{{ row.discount_amount }}
          </div>
          <div v-if="Number(row.coupon_discount) > 0" class="fee-tip discount">
            优惠券 -¥{{ row.coupon_discount }}
          </div>
          <div v-if="Number(row.voucher_discount) > 0" class="fee-tip discount">
            代金券 -¥{{ row.voucher_discount }}
          </div>
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
      <el-table-column prop="created_at" label="下单时间" width="220" />
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
import { todayDateRange } from '@/utils/date'

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
const channelFilter = ref<string | undefined>()
const diningFilter = ref<string | undefined>()
const keyword = ref('')
const dateRange = ref<string[] | null>(todayDateRange())

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function reload() {
  page.value = 1
  load()
}

function resetFilters() {
  storeFilter.value = undefined
  statusFilter.value = undefined
  paymentFilter.value = undefined
  channelFilter.value = undefined
  diningFilter.value = undefined
  keyword.value = ''
  dateRange.value = todayDateRange()
  reload()
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
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize.value,
      store_id: storeFilter.value,
      status: statusFilter.value,
      payment_status: paymentFilter.value,
      channel: channelFilter.value,
      dining_type: diningFilter.value,
      keyword: keyword.value.trim() || undefined,
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res = await api.get('/orders', { params })
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
.fee-tip.discount { color: #f56c6c; }
</style>
