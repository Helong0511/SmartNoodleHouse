<template>
  <div>
    <el-tabs v-model="activeTab" class="orders-tabs" @tab-change="onTabChange">
      <el-tab-pane v-if="canReadOrders" label="订单" name="orders">
        <div v-loading="loading">
          <el-form inline class="toolbar">
            <el-form-item v-if="auth.isSuperAdmin">
              <el-select v-model="storeFilter" placeholder="全部门店" clearable style="width:160px" @change="onStoreChange">
                <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
              </el-select>
            </el-form-item>
            <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
            <el-form-item>
              <el-input v-model="keyword" clearable placeholder="订单号/桌号/手机号/姓名" style="width:220px" @keyup.enter="reload"
                @clear="reload" />
            </el-form-item>
            <el-form-item>
              <el-select v-model="operatorFilter" placeholder="收银员" clearable filterable style="width:150px" @change="reload">
                <el-option v-for="o in operators" :key="o.id" :label="o.name" :value="o.id" />
              </el-select>
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
              <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="下单起"
                end-placeholder="下单止" :shortcuts="dateShortcuts" style="width:240px" @change="reload" />
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
            <el-table-column label="顾客" width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <template v-if="row.customer_id">
                  <div>{{ formatCustomerNameLines(row).primary }}</div>
                  <div v-if="formatCustomerNameLines(row).secondary" class="fee-tip">{{ formatCustomerNameLines(row).secondary }}</div>
                  <div class="fee-tip">
                    <template v-if="row.is_member">
                      {{ row.member_level_name || '会员' }}
                      <span v-if="row.customer_phone"> · {{ row.customer_phone }}</span>
                    </template>
                    <template v-else-if="row.customer_no">{{ row.customer_no }}</template>
                  </div>
                </template>
                <span v-else>—</span>
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
                  {{ row.social_applied ? '公益优惠' : '会员优惠' }} -¥{{ row.discount_amount }}
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
            <el-table-column label="支付方式" width="100">
              <template #default="{ row }">
                <div>{{ formatOrderPayMethod(row) }}</div>
                <div v-if="formatOrderPayBreakdown(row)" class="fee-tip">{{ formatOrderPayBreakdown(row) }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="下单时间" width="280" />
            <el-table-column label="收银员" width="100" show-overflow-tooltip>
              <template #default="{ row }">{{ row.operator_name || '—' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="$router.push(`/orders/${row.id}`)">详情</el-button>
                <el-button
                  v-if="canManageRefund && canOpenRefund(row)"
                  link
                  type="danger"
                  @click="$router.push(`/orders/${row.id}?pay=1`)"
                >
                  退款
                </el-button>
                <el-button
                  v-else-if="canReadPayments && row.payment_status && row.payment_status !== 'UNPAID'"
                  link
                  type="primary"
                  @click="openPaymentsForOrder(row)"
                >
                  支付
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next" style="margin-top:16px" @current-change="load" @size-change="reload" />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="canReadPayments" label="支付记录" name="payments" lazy>
        <PaymentsPanel :initial-keyword="paymentsKeyword" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import PaymentsPanel from '@/views/Payments.vue'
import {
  CHANNEL,
  label,
  ORDER_STATUS,
  PAYMENT_PROVIDER,
  PAYMENT_STATUS,
  orderStatusTagType,
  paymentStatusTagType,
} from '@/utils/labels'
import { todayDateRange } from '@/utils/date'
import { formatCustomerNameLines } from '@/utils/customerName'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const orders = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const storeFilter = ref<number | undefined>()
const statusFilter = ref<string | undefined>()
const paymentFilter = ref<string | undefined>()
const channelFilter = ref<string | undefined>()
const diningFilter = ref<string | undefined>()
const keyword = ref('')
const operatorFilter = ref<number | undefined>()
const operators = ref<any[]>([])
const dateRange = ref<string[] | null>(todayDateRange())
const syncingQuery = ref(false)

const canReadOrders = computed(() => auth.hasPermission('order:read'))
const canReadPayments = computed(() => auth.hasPermission('payment:read'))
const canManageRefund = computed(() => auth.hasPermission('payment:refund'))
/** 支付记录 Tab 预填关键词（来自 URL 或从订单行跳转） */
const paymentsKeyword = ref(String(route.query.keyword || ''))

function defaultTab() {
  if (canReadOrders.value) return 'orders'
  if (canReadPayments.value) return 'payments'
  return 'orders'
}

function tabFromQuery() {
  const q = String(route.query.tab || '')
  if (q === 'payments' && canReadPayments.value) return 'payments'
  if (q === 'orders' && canReadOrders.value) return 'orders'
  return defaultTab()
}

const activeTab = ref(tabFromQuery())

/** 已支付/部分退款等才显示退款入口（进详情页再做精确可退判断） */
function canOpenRefund(row: any) {
  const st = String(row?.payment_status || '').toUpperCase()
  return st === 'PAID' || st === 'PARTIAL_REFUND' || st === 'REFUNDING' || st === 'REFUND_PENDING'
}

function openPaymentsForOrder(row: any) {
  const kw = String(row?.order_no || '').trim()
  paymentsKeyword.value = kw
  activeTab.value = 'payments'
  syncQuery()
}

// 日期快捷：与后端 date_to 含全天口径一致（结束日按当地零点 +1 天）
const dateShortcuts = [
  {
    text: '今日',
    value: () => {
      const d = new Date()
      return [d, d]
    },
  },
  {
    text: '昨日',
    value: () => {
      const d = new Date()
      d.setDate(d.getDate() - 1)
      return [d, d]
    },
  },
  {
    text: '近7天',
    value: () => {
      const end = new Date()
      const start = new Date()
      start.setDate(start.getDate() - 6)
      return [start, end]
    },
  },
]

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

/** 主行：组合支付 / 微信 / 现金；多笔时标题用「组合支付」 */
function formatOrderPayMethod(row: any) {
  const tenders = Array.isArray(row?.payment_tenders) ? row.payment_tenders : []
  if (tenders.length > 1 || String(row?.payment_method || '').toUpperCase() === 'MIXED') {
    return label(PAYMENT_PROVIDER, 'MIXED')
  }
  if (tenders.length === 1) return label(PAYMENT_PROVIDER, tenders[0].provider)
  return row?.payment_method ? label(PAYMENT_PROVIDER, row.payment_method) : '—'
}

/** 副行：组合时拆开「微信¥x + 现金¥y」 */
function formatOrderPayBreakdown(row: any) {
  const tenders = Array.isArray(row?.payment_tenders) ? row.payment_tenders : []
  if (tenders.length <= 1) return ''
  return tenders
    .map((t: any) => `${label(PAYMENT_PROVIDER, t.provider)}¥${t.amount}`)
    .join(' + ')
}

function syncQuery() {
  if (syncingQuery.value) return
  syncingQuery.value = true
  const query: Record<string, string> = {}
  if (activeTab.value === 'payments') {
    query.tab = 'payments'
    const kw = paymentsKeyword.value.trim()
    if (kw) query.keyword = kw
  }
  router.replace({ path: '/orders', query }).finally(() => {
    syncingQuery.value = false
  })
}

function onTabChange() {
  syncQuery()
}

function reload() {
  page.value = 1
  load()
}

async function onStoreChange() {
  // 收银员下拉随门店联动，避免用 A 店员工筛选 B 店订单
  operatorFilter.value = undefined
  await Promise.all([reload(), loadOperators()])
}

function resetFilters() {
  storeFilter.value = undefined
  statusFilter.value = undefined
  paymentFilter.value = undefined
  channelFilter.value = undefined
  diningFilter.value = undefined
  keyword.value = ''
  operatorFilter.value = undefined
  dateRange.value = todayDateRange()
  reload()
  loadOperators()
}

async function loadOperators() {
  try {
    const res = await api.get('/orders/operators', {
      params: { store_id: storeFilter.value },
    })
    operators.value = res.data || []
  } catch {
    operators.value = []
  }
}

async function load() {
  if (!canReadOrders.value) return
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
      operator_id: operatorFilter.value,
      keyword: keyword.value.trim() || undefined,
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res = await api.get('/orders', { params })
    const list = res.data.items
    // 撤单/改筛选后末页可能变空，自动回退避免空白页
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    orders.value = list
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => [route.query.tab, route.query.keyword],
  () => {
    if (syncingQuery.value) return
    const next = tabFromQuery()
    if (next !== activeTab.value) activeTab.value = next
    const kw = String(route.query.keyword || '')
    if (kw && kw !== paymentsKeyword.value) paymentsKeyword.value = kw
  },
)

onMounted(async () => {
  activeTab.value = tabFromQuery()
  paymentsKeyword.value = String(route.query.keyword || '')
  if (canReadOrders.value) {
    await Promise.all([loadStores(), loadOperators()])
    await load()
  }
})
</script>

<style scoped>
.orders-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

.toolbar {
  margin-bottom: 12px;
}

.fee-tip {
  font-size: 11px;
  color: #e6a23c;
  line-height: 1.2;
}

.fee-tip.discount {
  color: #f56c6c;
}
</style>
