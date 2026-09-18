<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="onStoreChange"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-input
          v-model="keyword"
          clearable
          placeholder="支付号 / 订单号"
          style="width:220px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>
      <el-form-item>
        <el-select v-model="operatorFilter" placeholder="收银员" clearable filterable style="width:130px" @change="reload">
          <el-option v-for="o in operators" :key="o.id" :label="o.name" :value="o.id" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="statusFilter" placeholder="支付状态" clearable style="width:130px" @change="reload">
          <el-option v-for="(label, key) in PAYMENT_STATUS" :key="key" :label="label" :value="key" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="providerFilter" placeholder="支付渠道" clearable style="width:130px" @change="reload">
          <el-option v-for="(label, key) in PAYMENT_PROVIDER" :key="key" :label="label" :value="key" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="支付起"
          end-placeholder="支付止"
          style="width:240px"
          @change="reload"
        />
      </el-form-item>
      <el-form-item>
        <el-input-number
          v-model="amountMin"
          :min="0"
          :precision="2"
          :controls="false"
          placeholder="金额≥"
          style="width:110px"
          @change="reload"
        />
        <span style="margin:0 4px;color:#999">-</span>
        <el-input-number
          v-model="amountMax"
          :min="0"
          :precision="2"
          :controls="false"
          placeholder="金额≤"
          style="width:110px"
          @change="reload"
        />
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <el-button v-if="canManageRefund" type="warning" plain @click="openInflightRefunds">处理中退款</el-button>
    </el-form>

    <el-table :data="payments" border>
      <el-table-column prop="payment_no" label="支付号" min-width="260" show-overflow-tooltip />
      <el-table-column prop="store_name" label="门店" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column label="订单" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">{{ row.order_no || `#${row.order_id}` }}</template>
      </el-table-column>
      <el-table-column label="桌号" width="90">
        <template #default="{ row }">
          {{
            row.table_name ||
            row.table_no ||
            (row.order_channel === 'MEITUAN' || row.order_channel === 'ELEME' ? '外卖' : '—')
          }}
        </template>
      </el-table-column>
      <el-table-column label="用餐" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.order_channel === 'MEITUAN' || row.order_channel === 'ELEME'" size="small" type="info">外卖</el-tag>
          <el-tag v-else-if="row.dining_type === 'TAKEAWAY'" size="small" type="warning">打包</el-tag>
          <span v-else>堂食</span>
        </template>
      </el-table-column>
      <el-table-column label="渠道" width="110">
        <template #default="{ row }">{{ label(PAYMENT_PROVIDER, row.provider) }}</template>
      </el-table-column>
      <el-table-column label="通道" width="100">
        <template #default="{ row }">{{ row.channel || '—' }}</template>
      </el-table-column>
      <el-table-column prop="amount" label="支付金额" width="120">
        <template #default="{ row }">
          ¥{{ row.amount }}
          <div v-if="Number(row.order_discount_amount) > 0" class="fee-tip discount">
            含会员优惠 -¥{{ row.order_discount_amount }}
          </div>
          <div v-if="Number(row.order_coupon_discount) > 0" class="fee-tip discount">
            含优惠券 -¥{{ row.order_coupon_discount }}
          </div>
          <div v-if="Number(row.order_voucher_discount) > 0" class="fee-tip discount">
            含代金券 -¥{{ row.order_voucher_discount }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="已退/可退" width="130">
        <template #default="{ row }">
          ¥{{ row.refunded_amount || '0.00' }} / ¥{{ row.refundable_amount ?? row.amount }}
          <el-tag v-if="parseFloat(row.refund_inflight || '0') > 0" size="small" type="warning" style="margin-left:4px">
            处理中
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="paymentStatusTagType(row.status)">
            {{ label(PAYMENT_STATUS, row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="paid_at" label="支付时间" width="170" />
      <el-table-column label="收银员" width="90">
        <template #default="{ row }">{{ row.operator_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openRefundDrawer(row)">明细</el-button>
          <el-button
            v-if="canRefund(row)"
            link
            type="danger"
            @click="openRefund(row)"
          >
            退款
          </el-button>
          <el-button link type="primary" @click="$router.push(`/orders/${row.order_id}`)">订单</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
      @size-change="reload"
    />

    <el-dialog v-model="refundDialog" title="发起退款" width="400px">
      <el-form label-width="90px">
        <el-form-item label="支付号">{{ refundRow?.payment_no }}</el-form-item>
        <el-form-item label="门店">{{ refundRow?.store_name || (refundRow?.store_id ? `门店#${refundRow.store_id}` : '—') }}</el-form-item>
        <el-form-item label="可退余额">¥{{ refundMax }}</el-form-item>
        <el-form-item label="退款金额">
          <el-input-number
            v-model="refundAmount"
            :min="0.01"
            :max="refundMax"
            :precision="2"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="refundReason" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="refundDialog = false">取消</el-button>
        <el-button type="danger" :loading="refunding" @click="submitRefund">确认退款</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="refundDrawer"
      :title="refundDrawerTitle"
      size="720px"
      destroy-on-close
    >
      <div v-loading="refundListLoading">
        <el-alert
          v-if="refundDrawerMode === 'inflight'"
          type="warning"
          :closable="false"
          show-icon
          title="以下为 PENDING / PROCESSING 退款单。可点「同步」向平台查询结果（补偿回调丢失）。"
          style="margin-bottom:12px"
        />
        <el-table :data="refundList" border size="small">
          <el-table-column prop="refund_no" label="退款单号" min-width="260" show-overflow-tooltip />
          <el-table-column v-if="refundDrawerMode === 'inflight'" prop="payment_no" label="支付号" min-width="240" show-overflow-tooltip />
          <el-table-column prop="order_no" label="订单号" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">{{ row.order_no || '—' }}</template>
          </el-table-column>
          <el-table-column v-if="refundDrawerMode === 'inflight'" prop="store_name" label="门店" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.store_name || '—' }}</template>
          </el-table-column>
          <el-table-column label="金额" width="90">
            <template #default="{ row }">¥{{ row.amount }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="paymentRefundStatusTagType(row.status)">
                {{ label(PAYMENT_REFUND_STATUS, row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.reason || '—' }}</template>
          </el-table-column>
          <el-table-column prop="operator_name" label="操作人" width="90" show-overflow-tooltip>
            <template #default="{ row }">{{ row.operator_name || '—' }}</template>
          </el-table-column>
          <el-table-column prop="fail_reason" label="失败原因" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.fail_reason || '—' }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="申请时间" width="160" />
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="canSyncRefund(row)"
                link
                type="warning"
                :loading="syncingNo === row.refund_no"
                @click="syncRefund(row)"
              >
                同步
              </el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-if="refundDrawerMode === 'inflight'"
          v-model:current-page="refundPage"
          v-model:page-size="refundPageSize"
          :total="refundTotal"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          style="margin-top:12px"
          @current-change="loadInflightRefunds"
          @size-change="reloadInflightRefunds"
        />
        <div v-if="!refundList.length && !refundListLoading" class="empty-hint">暂无退款记录</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  label,
  PAYMENT_PROVIDER,
  PAYMENT_REFUND_STATUS,
  PAYMENT_STATUS,
  paymentRefundStatusTagType,
  paymentStatusTagType,
} from '@/utils/labels'
import { todayDateRange } from '@/utils/date'

const auth = useAuthStore()
const loading = ref(false)
const payments = ref<any[]>([])
const stores = ref<any[]>([])
const operators = ref<any[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const storeFilter = ref<number | undefined>()
const statusFilter = ref<string | undefined>()
const providerFilter = ref<string | undefined>()
const operatorFilter = ref<number | undefined>()
const amountMin = ref<number | undefined>()
const amountMax = ref<number | undefined>()
const keyword = ref('')
const dateRange = ref<string[] | null>(todayDateRange())
const refundDialog = ref(false)
const refundRow = ref<any>(null)
const refundAmount = ref(0)
const refundReason = ref('商家退款')
const refunding = ref(false)

const refundDrawer = ref(false)
const refundDrawerMode = ref<'payment' | 'inflight'>('payment')
const refundDrawerPayment = ref<any>(null)
const refundList = ref<any[]>([])
const refundListLoading = ref(false)
const refundPage = ref(1)
const refundPageSize = ref(20)
const refundTotal = ref(0)
const syncingNo = ref('')

const canManageRefund = computed(() => auth.hasPermission('payment:refund'))

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const refundMax = computed(() => {
  const v = parseFloat(refundRow.value?.refundable_amount ?? refundRow.value?.amount ?? '0')
  return Number.isFinite(v) && v > 0 ? v : 0.01
})

const refundDrawerTitle = computed(() => {
  if (refundDrawerMode.value === 'inflight') return '处理中退款单'
  const no = refundDrawerPayment.value?.payment_no
  return no ? `退款明细 · ${no}` : '退款明细'
})

function canRefund(row: any) {
  if (!canManageRefund.value) return false
  if (row.status === 'REFUNDED') return false
  if (!(row.status === 'PAID' || row.status === 'PARTIAL_REFUND')) return false
  const remain = parseFloat(row.refundable_amount ?? '0')
  const inflight = parseFloat(row.refund_inflight ?? '0')
  if (inflight > 0) return false
  return remain > 0
}

function canSyncRefund(row: any) {
  if (!canManageRefund.value) return false
  return row.status === 'PENDING' || row.status === 'PROCESSING'
}

function reload() {
  page.value = 1
  load()
}

function resetFilters() {
  storeFilter.value = undefined
  statusFilter.value = undefined
  providerFilter.value = undefined
  operatorFilter.value = undefined
  amountMin.value = undefined
  amountMax.value = undefined
  keyword.value = ''
  dateRange.value = todayDateRange()
  onStoreChange()
}

// 门店切换后收银员下拉须按门店联动
async function onStoreChange() {
  operatorFilter.value = undefined
  page.value = 1
  await Promise.all([loadOperators(), load()])
}

async function loadOperators() {
  try {
    const res = await api.get('/orders/operators', {
      params: { store_id: auth.isSuperAdmin ? storeFilter.value : undefined },
    })
    operators.value = res.data?.items || []
  } catch {
    operators.value = []
  }
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
      status: statusFilter.value,
      store_id: storeFilter.value,
      provider: providerFilter.value,
      operator_id: operatorFilter.value,
      amount_min: amountMin.value,
      amount_max: amountMax.value,
      keyword: keyword.value.trim() || undefined,
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res = await api.get('/payments', { params })
    const list = res.data.items || []
    // 退款后末页变空时自动回退
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    payments.value = list
    total.value = res.data.total
  } catch (e: any) {
    ElMessage.error(e?.message || '加载支付记录失败')
  } finally {
    loading.value = false
  }
}

function openRefund(row: any) {
  refundRow.value = row
  const max = parseFloat(row.refundable_amount ?? row.amount)
  refundAmount.value = Number.isFinite(max) && max > 0 ? max : parseFloat(row.amount)
  refundReason.value = '商家退款'
  refundDialog.value = true
}

async function submitRefund() {
  if (!refundRow.value) return
  if (refundAmount.value > refundMax.value + 1e-9) {
    ElMessage.warning(`退款金额不能超过可退余额 ¥${refundMax.value}`)
    return
  }
  refunding.value = true
  try {
    const res = await api.post(`/payments/${refundRow.value.payment_no}/refund`, {
      amount: refundAmount.value,
      reason: refundReason.value,
    })
    ElMessage.success(res.data?.message || '退款已提交')
    refundDialog.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '退款失败')
  } finally {
    refunding.value = false
  }
}

async function openRefundDrawer(row: any) {
  refundDrawerMode.value = 'payment'
  refundDrawerPayment.value = row
  refundDrawer.value = true
  refundListLoading.value = true
  refundList.value = []
  try {
    const res = await api.get('/payments/refunds', {
      params: {
        payment_no: row.payment_no,
        page: 1,
        page_size: 50,
        store_id: storeFilter.value,
      },
    })
    refundList.value = res.data.items || []
    refundTotal.value = res.data.total || 0
  } catch (e: any) {
    ElMessage.error(e.message || '加载退款明细失败')
  } finally {
    refundListLoading.value = false
  }
}

function openInflightRefunds() {
  refundDrawerMode.value = 'inflight'
  refundDrawerPayment.value = null
  refundPage.value = 1
  refundDrawer.value = true
  loadInflightRefunds()
}

async function loadInflightRefunds() {
  refundListLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page: refundPage.value,
      page_size: refundPageSize.value,
      status: 'PENDING,PROCESSING',
      store_id: storeFilter.value,
      keyword: keyword.value.trim() || undefined,
    }
    if (dateRange.value?.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    const res = await api.get('/payments/refunds', { params })
    const list = res.data.items || []
    // 末页退款单处理完后状态离开"处理中"，自动回退避免空白页
    if (list.length === 0 && refundPage.value > 1) {
      refundPage.value -= 1
      refundListLoading.value = false
      await loadInflightRefunds()
      return
    }
    refundList.value = list
    refundTotal.value = res.data.total || 0
  } catch (e: any) {
    ElMessage.error(e.message || '加载处理中退款失败')
    refundList.value = []
    refundTotal.value = 0
  } finally {
    refundListLoading.value = false
  }
}

function reloadInflightRefunds() {
  refundPage.value = 1
  return loadInflightRefunds()
}

async function syncRefund(row: any) {
  syncingNo.value = row.refund_no
  try {
    const res = await api.post(`/payments/refunds/${row.refund_no}/sync`)
    const st = res.data?.status || row.status
    ElMessage.success(`同步完成：${label(PAYMENT_REFUND_STATUS, st)}`)
    if (refundDrawerMode.value === 'inflight') {
      await loadInflightRefunds()
    } else if (refundDrawerPayment.value) {
      await openRefundDrawer(refundDrawerPayment.value)
    }
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  } finally {
    syncingNo.value = ''
  }
}

onMounted(async () => {
  await loadStores()
  await Promise.all([loadOperators(), load()])
})
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.empty-hint { margin-top: 24px; text-align: center; color: #909399; }
.muted { color: #c0c4cc; }
.fee-tip { font-size: 11px; color: #e6a23c; line-height: 1.2; }
.fee-tip.discount { color: #f56c6c; }
</style>
