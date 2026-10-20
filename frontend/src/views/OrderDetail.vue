<template>
  <div v-loading="loading">
    <el-button link @click="goBack">← 返回</el-button>
    <template v-if="order">
      <h3 style="margin:12px 0">{{ order.order_no }}</h3>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="门店">
          {{ order.store_name || (order.store_id ? `门店#${order.store_id}` : '—') }}
          <span v-if="order.store_no" class="muted">（{{ order.store_no }}）</span>
        </el-descriptions-item>
        <el-descriptions-item label="订单状态">
          <el-tag :type="orderStatusTagType(order.status)">{{ label(ORDER_STATUS, order.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="支付状态">
          <el-tag :type="paymentStatusTagType(order.payment_status)">{{ label(PAYMENT_STATUS, order.payment_status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="支付方式">{{ paymentMethodText }}</el-descriptions-item>
        <el-descriptions-item label="支付时间">{{ order.paid_at || '—' }}</el-descriptions-item>
        <el-descriptions-item label="渠道">{{ label(CHANNEL, order.channel) }}</el-descriptions-item>
        <el-descriptions-item label="桌号">
          <template v-if="isDelivery">外卖</template>
          <template v-else>{{ order.table?.table_name || order.table?.table_no || '—' }}</template>
        </el-descriptions-item>
        <el-descriptions-item label="用餐方式">
          <el-tag v-if="isDelivery" type="info" size="small">外卖配送</el-tag>
          <el-tag v-else-if="order.dining_type === 'TAKEAWAY'" type="warning" size="small">打包外带</el-tag>
          <span v-else>堂食</span>
        </el-descriptions-item>
        <el-descriptions-item label="小计">¥{{ order.subtotal }}</el-descriptions-item>
        <el-descriptions-item :label="order.social_applied ? '公益优惠' : '会员优惠'">
          <span v-if="Number(order.discount_amount) > 0" class="discount">
            -¥{{ order.discount_amount }}
            <span v-if="order.social_applied && order.social_identity_code" class="muted">
              （{{ socialIdentityLabel(order.social_identity_code) }}）
            </span>
          </span>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="优惠券">
          <span v-if="Number(order.coupon_discount) > 0" class="discount">-¥{{ order.coupon_discount }}</span>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="代金券">
          <span v-if="Number(order.voucher_discount) > 0" class="discount">-¥{{ order.voucher_discount }}</span>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="打包费">
          ¥{{ order.packing_fee || '0.00' }}
        </el-descriptions-item>
        <el-descriptions-item label="应付金额">
          <strong>¥{{ order.total_amount }}</strong>
        </el-descriptions-item>
        <el-descriptions-item label="顾客">
          <template v-if="order.customer_name || order.customer_no || order.customer_id">
            {{ order.customer_name || '—' }}
            <span v-if="order.customer_no" class="muted">（{{ order.customer_no }}）</span>
            <span v-else-if="order.customer_id" class="muted">（#{{ order.customer_id }}）</span>
          </template>
          <template v-else>—</template>
        </el-descriptions-item>
        <el-descriptions-item label="手机">{{ order.customer_phone || '—' }}</el-descriptions-item>
        <el-descriptions-item label="是否会员">
          <el-tag size="small" :type="order.is_member ? 'success' : 'info'">
            {{ order.is_member ? '已注册会员' : '未注册/游客' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="会员等级">{{ order.member_level_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="会员折扣">
          {{
            order.member_discount_rate != null && Number(order.member_discount_rate) < 1
              ? `${(Number(order.member_discount_rate) * 10).toFixed(1)}折`
              : (order.is_member ? '无折扣' : '—')
          }}
        </el-descriptions-item>
        <el-descriptions-item label="下单时间">{{ order.created_at || '—' }}</el-descriptions-item>
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
        <el-button v-if="canManageRefund" type="warning" plain @click="openInflightRefunds">处理中退款</el-button>
        <el-button v-if="canReadPayments" link type="primary" @click="goPaymentsTab">全部支付记录</el-button>
      </div>

      <el-table :data="order.items" style="margin-top:16px" border>
        <el-table-column prop="product_name" label="餐品" />
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

      <h4 id="order-payments" class="pay-title">收款与退款</h4>
      <el-table v-if="order.payments?.length" :data="order.payments" border>
        <el-table-column label="支付方式" width="110">
          <template #default="{ row }">{{ label(PAYMENT_PROVIDER, row.provider) }}</template>
        </el-table-column>
        <el-table-column label="通道" width="90">
          <template #default="{ row }">{{ label(PAYMENT_CHANNEL, row.channel) }}</template>
        </el-table-column>
        <el-table-column prop="amount" label="支付金额" width="110">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="已退/可退" width="140">
          <template #default="{ row }">
            <span :class="{ 'refunded-amt': Number(row.refunded_amount) > 0 }">
              ¥{{ row.refunded_amount || '0.00' }}
            </span>
            / ¥{{ row.refundable_amount ?? row.amount }}
            <el-tag v-if="parseFloat(row.refund_inflight || '0') > 0" size="small" type="warning" style="margin-left:4px">
              处理中
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="paymentStatusTagType(row.status)">
              {{ label(PAYMENT_STATUS, row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="payment_no" label="支付单号" min-width="180" show-overflow-tooltip />
        <el-table-column prop="paid_at" label="收款时间" width="280" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRefundDrawer(row)">明细</el-button>
            <el-button v-if="canRefund(row)" link type="danger" @click="openRefund(row)">退款</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="empty-hint">暂无收款记录</div>
    </template>

    <el-dialog v-model="refundDialog" title="发起退款" width="400px">
      <el-form label-width="90px">
        <el-form-item label="支付号">{{ refundRow?.payment_no }}</el-form-item>
        <el-form-item label="门店">
          {{ order?.store_name || (order?.store_id ? `门店#${order.store_id}` : '—') }}
        </el-form-item>
        <el-form-item label="可退余额">¥{{ refundMax }}</el-form-item>
        <el-form-item label="退款金额">
          <el-input-number v-model="refundAmount" :min="0.01" :max="refundMax" :precision="2" style="width:100%" />
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

    <el-drawer v-model="refundDrawer" :title="refundDrawerTitle" size="720px" destroy-on-close>
      <div v-loading="refundListLoading">
        <el-alert v-if="refundDrawerMode === 'inflight'" type="warning" :closable="false" show-icon
          title="以下为待处理 / 处理中的退款单。可点「同步」向平台查询结果（补偿回调丢失）。" style="margin-bottom:12px" />
        <el-table :data="refundList" border size="small">
          <el-table-column prop="refund_no" label="退款单号" min-width="240" show-overflow-tooltip />
          <el-table-column v-if="refundDrawerMode === 'inflight'" prop="payment_no" label="支付号" min-width="220"
            show-overflow-tooltip />
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
              <el-button v-if="canSyncRefund(row)" link type="warning" :loading="syncingNo === row.refund_no"
                @click="syncRefund(row)">
                同步
              </el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!refundList.length && !refundListLoading" class="empty-hint">暂无退款记录</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  CHANNEL,
  cancelAllowed,
  label,
  ORDER_NEXT_ACTIONS,
  ORDER_STATUS,
  PAYMENT_CHANNEL,
  PAYMENT_PROVIDER,
  PAYMENT_REFUND_STATUS,
  PAYMENT_STATUS,
  orderStatusTagType,
  paymentRefundStatusTagType,
  paymentStatusTagType,
  SOCIAL_IDENTITY,
} from '@/utils/labels'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const order = ref<any>(null)

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
const syncingNo = ref('')

const canManageRefund = computed(() => auth.hasPermission('payment:refund'))
const canReadPayments = computed(() => auth.hasPermission('payment:read'))

function socialIdentityLabel(code: string) {
  const key = String(code || '').toUpperCase()
  return SOCIAL_IDENTITY[key] || code || ''
}

const nextActions = computed(() => ORDER_NEXT_ACTIONS[order.value?.status] || [])
const canCancel = computed(() => order.value && cancelAllowed(order.value.status))
const isDelivery = computed(() => {
  const ch = order.value?.channel
  return ch === 'MEITUAN' || ch === 'ELEME'
})
const paymentMethodText = computed(() => {
  const o = order.value
  if (!o) return '—'
  const payments = Array.isArray(o.payments) ? o.payments : []
  if (payments.length > 1) {
    const parts = payments
      .map((p: any) => `${label(PAYMENT_PROVIDER, p.provider)}¥${p.amount}`)
      .join(' + ')
    return `组合支付（${parts}）`
  }
  if (o.payment_method) return label(PAYMENT_PROVIDER, o.payment_method)
  if (payments.length === 1) return label(PAYMENT_PROVIDER, payments[0].provider)
  return '—'
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

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/orders')
}

function goPaymentsTab() {
  const q: Record<string, string> = { tab: 'payments' }
  if (order.value?.order_no) q.keyword = order.value.order_no
  router.push({ path: '/orders', query: q })
}

function scrollToPayments() {
  nextTick(() => {
    document.getElementById('order-payments')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

async function load() {
  loading.value = true
  try {
    order.value = (await api.get(`/orders/${route.params.id}`)).data
    if (route.query.pay === '1' || route.query.focus === 'pay') scrollToPayments()
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
    await load()
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
        store_id: order.value?.store_id,
      },
    })
    refundList.value = res.data.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载退款明细失败')
  } finally {
    refundListLoading.value = false
  }
}

async function openInflightRefunds() {
  refundDrawerMode.value = 'inflight'
  refundDrawerPayment.value = null
  refundDrawer.value = true
  refundListLoading.value = true
  refundList.value = []
  try {
    const res = await api.get('/payments/refunds', {
      params: {
        status: 'PENDING,PROCESSING',
        page: 1,
        page_size: 50,
        store_id: order.value?.store_id,
        keyword: order.value?.order_no,
      },
    })
    refundList.value = res.data.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载处理中退款失败')
  } finally {
    refundListLoading.value = false
  }
}

async function syncRefund(row: any) {
  syncingNo.value = row.refund_no
  try {
    const res = await api.post(`/payments/refunds/${row.refund_no}/sync`)
    const st = res.data?.status || row.status
    ElMessage.success(`同步完成：${label(PAYMENT_REFUND_STATUS, st)}`)
    if (refundDrawerMode.value === 'inflight') {
      await openInflightRefunds()
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

onMounted(load)
</script>

<style scoped>
.actions { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.muted { color: #999; }
.discount { color: #f56c6c; font-weight: 600; }
.refunded-amt { color: #f56c6c; font-weight: 600; }
.pay-title { margin: 20px 0 10px; font-size: 15px; font-weight: 600; }
.empty-hint { margin-top: 16px; color: #909399; text-align: center; }
</style>
