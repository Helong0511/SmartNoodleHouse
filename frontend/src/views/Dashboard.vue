<template>
  <div class="dashboard-page" v-loading="loading">
    <!-- 背景装饰层 -->
    <div class="bg-layer" aria-hidden="true">
      <div class="bg-grid"></div>
      <div class="bg-glow glow-1"></div>
      <div class="bg-glow glow-2"></div>
    </div>

    <div class="dashboard-inner">
      <div class="toolbar">
        <el-radio-group v-model="period" @change="load">
          <el-radio-button value="today">今日</el-radio-button>
          <el-radio-button value="yesterday">昨日</el-radio-button>
          <el-radio-button value="month">本月</el-radio-button>
        </el-radio-group>
        <el-select
          v-if="auth.isSuperAdmin"
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          popper-class="dashboard-dark-popper"
          style="width:180px"
          @change="load"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
        <span v-if="isAllStores" class="scope-hint">
          汇总 {{ storeRows.length || stores.length }} 家门店 · 点击下表行可切换单店
        </span>
        <span v-else-if="currentStoreName" class="scope-hint">{{ periodLabel }} · {{ currentStoreName }}</span>
        <el-button class="refresh-btn" style="margin-left:auto" @click="load">刷新</el-button>
      </div>

      <el-row :gutter="16">
        <el-col :xs="12" :sm="8" :md="4" v-for="item in cards" :key="item.label">
          <el-card shadow="hover" class="stat-card">
            <div class="stat" :class="item.tone">{{ item.value }}</div>
            <div class="label">{{ item.label }}</div>
            <div v-if="item.hint" class="hint">{{ item.hint }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-row v-if="showTodoPanel" :gutter="16" style="margin-top:16px">
        <el-col :span="24">
          <el-card shadow="hover" class="todo-card" :class="{ 'todo-alert': todoAlert }">
            <div class="section-head">
              <h3 class="sec-title">待办事项</h3>
              <div class="todo-links">
                <span class="todo-poll-hint">约每 15 秒自动刷新</span>
                <el-button
                  v-if="showInvoiceTodo"
                  link
                  type="primary"
                  @click="$router.push({ path: '/invoices', query: { status: 'PENDING' } })"
                >
                  发票申请
                </el-button>
                <el-button
                  v-if="showSocialTodo"
                  link
                  type="primary"
                  @click="$router.push({ path: '/social-benefits', query: { tab: 'pending' } })"
                >
                  社会贡献审核
                </el-button>
              </div>
            </div>

            <template v-if="showInvoiceTodo">
              <div v-if="invoicePendingCount > 0" class="todo-list">
                <div class="todo-summary">
                  <el-tag type="warning" effect="dark">待开票 {{ invoicePendingCount }}</el-tag>
                  <span class="scope-hint">顾客已提交，请尽快开票或驳回</span>
                </div>
                <el-table :data="invoicePendingItems" size="small" border>
                  <el-table-column prop="created_at" label="申请时间" width="160" />
                  <el-table-column prop="order_no" label="订单号" min-width="180" show-overflow-tooltip />
                  <el-table-column prop="title" label="抬头" min-width="140" show-overflow-tooltip />
                  <el-table-column label="金额" width="100" align="right">
                    <template #default="{ row }">¥{{ row.amount }}</template>
                  </el-table-column>
                  <el-table-column prop="email" label="邮箱" min-width="150" show-overflow-tooltip />
                  <el-table-column v-if="showInvoiceTodo" label="操作" width="160" fixed="right">
                    <template #default="{ row }">
                      <el-button
                        link
                        type="primary"
                        :loading="todoBusyKey === `inv-issue-${row.id}`"
                        @click="issueInvoiceTodo(row)"
                      >开票</el-button>
                      <el-button
                        link
                        type="danger"
                        :loading="todoBusyKey === `inv-reject-${row.id}`"
                        @click="rejectInvoiceTodo(row)"
                      >驳回</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-else class="todo-empty">暂无待开票申请</div>
            </template>

            <template v-if="showSocialTodo">
              <div v-if="socialPendingCount > 0" class="todo-list" :class="{ 'todo-list-gap': showInvoiceTodo }">
                <div class="todo-summary">
                  <el-tag type="warning" effect="dark">待审公益认定 {{ socialPendingCount }}</el-tag>
                  <span class="scope-hint">小程序已提交，请尽快通过或驳回</span>
                </div>
                <el-table :data="socialPendingItems" size="small" border>
                  <el-table-column prop="created_at" label="申请时间" width="160" />
                  <el-table-column label="顾客" min-width="140" show-overflow-tooltip>
                    <template #default="{ row }">
                      {{ formatCustomerNameLines(row).primary }}
                      <span v-if="formatCustomerNameLines(row).secondary" class="todo-sub">{{ formatCustomerNameLines(row).secondary }}</span>
                      <span v-if="row.customer_phone" class="todo-sub">{{ row.customer_phone }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="身份" min-width="120" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.identity_name || row.identity_code || '—' }}</template>
                  </el-table-column>
                  <el-table-column prop="credential_note" label="凭证备注" min-width="140" show-overflow-tooltip />
                  <el-table-column label="来源" width="90">
                    <template #default="{ row }">{{ socialVerifyLabel(row.verify_method) }}</template>
                  </el-table-column>
                  <el-table-column v-if="canReviewSocial" label="操作" width="160" fixed="right">
                    <template #default="{ row }">
                      <el-button
                        link
                        type="primary"
                        :loading="todoBusyKey === `soc-ok-${row.id}`"
                        @click="reviewSocialTodo(row, true)"
                      >通过</el-button>
                      <el-button
                        link
                        type="danger"
                        :loading="todoBusyKey === `soc-no-${row.id}`"
                        @click="reviewSocialTodo(row, false)"
                      >拒绝</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-else class="todo-empty" :class="{ 'todo-list-gap': showInvoiceTodo }">暂无待审公益认定</div>
            </template>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16" style="margin-top:16px">
        <el-col :xs="24" :md="10">
          <el-card shadow="hover" class="side-card">
            <div class="section-head">
              <h3 class="sec-title">{{ periodLabel }}优惠构成</h3>
              <span class="scope-hint">有效已付订单统计</span>
            </div>
            <el-table :data="discountRows" size="small" border>
              <el-table-column prop="name" label="类型" width="100" />
              <el-table-column label="金额" width="100" align="right">
                <template #default="{ row }">¥{{ row.amount }}</template>
              </el-table-column>
              <el-table-column prop="desc" label="说明" min-width="120" show-overflow-tooltip />
            </el-table>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="14">
          <el-card shadow="hover" class="side-card">
            <div class="section-head">
              <h3 class="sec-title">近 7 日趋势</h3>
              <span class="scope-hint">按自然日 · 净营收 = 入账 − 退款</span>
            </div>
            <el-table :data="data?.daily_series || []" size="small" stripe border>
              <el-table-column prop="date" label="日期" width="100" />
              <el-table-column prop="order_count" label="下单数" width="72" align="right" />
              <el-table-column prop="paid_order_count" label="已付" width="64" align="right" />
              <el-table-column label="净营收" width="92" align="right">
                <template #default="{ row }">¥{{ row.net_revenue }}</template>
              </el-table-column>
              <el-table-column label="毛收入" width="92" align="right">
                <template #default="{ row }">¥{{ row.gross_revenue }}</template>
              </el-table-column>
              <el-table-column label="退款" width="80" align="right">
                <template #default="{ row }">
                  <span :class="{ 'text-warn': Number(row.refunded_amount) > 0 }">¥{{ row.refunded_amount }}</span>
                </template>
              </el-table-column>
              <el-table-column label="合计优惠" min-width="88" align="right">
                <template #default="{ row }">
                  {{ Number(row.total_discount) > 0 ? `¥${row.total_discount}` : '—' }}
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-row v-if="isAllStores && storeRows.length" :gutter="16" style="margin-top:16px">
        <el-col :span="24">
          <el-card shadow="hover">
            <div class="section-head">
              <h3 class="sec-title">各门店{{ periodLabel }}销售</h3>
              <span class="scope-hint">点击行切换单店视图</span>
            </div>
            <el-table :data="storeRows" size="small" stripe highlight-current-row class="store-table"
              @row-click="onStoreRowClick">
              <el-table-column prop="store_name" label="门店" min-width="110" fixed>
                <template #default="{ row }">
                  <span class="store-link">{{ row.store_name }}</span>
                  <span class="store-no">{{ row.store_no }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="order_count" label="订单" width="68" align="right" />
              <el-table-column prop="paid_order_count" label="已付" width="68" align="right" />
              <el-table-column label="净营收" width="96" align="right">
                <template #default="{ row }">¥{{ row.net_revenue }}</template>
              </el-table-column>
              <el-table-column label="实付合计" width="96" align="right">
                <template #default="{ row }">¥{{ row.payable_amount }}</template>
              </el-table-column>
              <el-table-column label="会员优惠" width="88" align="right">
                <template #default="{ row }">{{ fmtMoney(row.member_discount) }}</template>
              </el-table-column>
              <el-table-column label="优惠券" width="80" align="right">
                <template #default="{ row }">{{ fmtMoney(row.coupon_discount) }}</template>
              </el-table-column>
              <el-table-column label="代金券" width="80" align="right">
                <template #default="{ row }">{{ fmtMoney(row.voucher_discount) }}</template>
              </el-table-column>
              <el-table-column label="合计优惠" width="88" align="right">
                <template #default="{ row }">{{ fmtMoney(row.total_discount) }}</template>
              </el-table-column>
              <el-table-column label="退款" width="80" align="right">
                <template #default="{ row }">
                  <span :class="{ 'text-warn': Number(row.refunded_amount) > 0 }">¥{{ row.refunded_amount }}</span>
                </template>
              </el-table-column>
              <el-table-column label="客单价" width="80" align="right">
                <template #default="{ row }">
                  {{ row.paid_order_count ? `¥${row.avg_order_amount}` : '—' }}
                </template>
              </el-table-column>
              <el-table-column prop="new_store_customers" label="首单顾客" width="88" align="right" />
              <el-table-column label="外卖待接" width="80" align="right">
                <template #default="{ row }">
                  <el-tag v-if="row.delivery_pending > 0" type="danger" size="small">{{ row.delivery_pending }}</el-tag>
                  <span v-else>0</span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16" style="margin-top:16px">
        <el-col :span="24">
          <el-card shadow="hover">
            <div class="section-head">
              <h3 class="sec-title">外卖实时</h3>
              <el-button link type="primary" @click="$router.push('/delivery')">进入外卖管理</el-button>
            </div>
            <el-row :gutter="12">
              <el-col :xs="12" :sm="8" :md="4" v-for="d in deliveryCards" :key="d.label">
                <div class="mini-stat" :class="{ alert: d.alert && Number(d.raw) > 0 }">
                  <div class="mini-val">{{ d.value }}</div>
                  <div class="mini-label">{{ d.label }}</div>
                </div>
              </el-col>
            </el-row>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16" style="margin-top:16px">
        <el-col :span="isAllStores ? 24 : 12">
          <el-card>
            <h3 class="sec-title">{{ periodLabel }}餐品销量 TOP{{ isAllStores ? '20' : '15' }}</h3>
            <el-table :data="data?.product_sales || []" size="small" border>
              <el-table-column v-if="isAllStores" prop="store_name" label="门店" width="120" />
              <el-table-column prop="name" label="餐品" min-width="140" />
              <el-table-column prop="sales" label="销量（份）" width="100" align="right" />
            </el-table>
          </el-card>
        </el-col>
        <el-col v-if="!isAllStores" :span="12">
          <el-card>
            <h3 class="sec-title">支付方式（{{ periodLabel }}）</h3>
            <p class="pay-note">按成功支付笔数统计；退款后仍计入笔数，金额区分毛收/退款/净收。</p>
            <el-table :data="paymentRows" size="small" border>
              <el-table-column prop="label" label="渠道" width="90" />
              <el-table-column prop="count" label="笔数" width="80" />
              <el-table-column label="入账" prop="gross_amount">
                <template #default="{ row }">¥{{ row.gross_amount }}</template>
              </el-table-column>
              <el-table-column label="退款" prop="refunded_amount">
                <template #default="{ row }">¥{{ row.refunded_amount }}</template>
              </el-table-column>
              <el-table-column label="净收" prop="net_amount">
                <template #default="{ row }">¥{{ row.net_amount }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-row v-if="isAllStores" :gutter="16" style="margin-top:16px">
        <el-col :span="24">
          <el-card>
            <h3 class="sec-title">支付方式（{{ periodLabel }}·全店汇总）</h3>
            <p class="pay-note">按成功支付笔数统计；退款后仍计入笔数，金额区分毛收/退款/净收。</p>
            <el-table :data="paymentRows" size="small" border>
              <el-table-column prop="label" label="渠道" width="90" />
              <el-table-column prop="count" label="笔数" width="80" />
              <el-table-column label="入账">
                <template #default="{ row }">¥{{ row.gross_amount }}</template>
              </el-table-column>
              <el-table-column label="退款">
                <template #default="{ row }">¥{{ row.refunded_amount }}</template>
              </el-table-column>
              <el-table-column label="净收">
                <template #default="{ row }">¥{{ row.net_amount }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { formatCustomerNameLines } from '@/utils/customerName'

/** 待办静默轮询间隔（毫秒）；切回标签页会立即再拉一次 */
const TODO_POLL_MS = 15_000

const SOCIAL_VERIFY_LABELS: Record<string, string> = {
  WECHAT: '微信',
  ALIPAY: '支付宝',
  MINI: '小程序',
  POS: '收银核验',
  MANUAL: '手工认定',
}

function socialVerifyLabel(method: string | undefined) {
  const key = String(method || '').toUpperCase()
  return SOCIAL_VERIFY_LABELS[key] || (key ? key : '—')
}

const auth = useAuthStore()
const data = ref<any>(null)
const loading = ref(false)
const { storeOptions: stores, loadStores } = useStoreOptions()
const storeFilter = ref<number | undefined>()
const period = ref<'today' | 'yesterday' | 'month'>('today')
const invoicePendingCount = ref(0)
const invoicePendingItems = ref<any[]>([])
const socialPendingCount = ref(0)
const socialPendingItems = ref<any[]>([])
const todoBusyKey = ref<string | null>(null)
const showInvoiceTodo = computed(() => auth.hasPermission('invoice:manage') && !auth.isCashier)
const showSocialTodo = computed(() => auth.hasPermission('customer:read') && !auth.isCashier)
/** 与后端 require_social_identity_manage 对齐 */
const canReviewSocial = computed(
  () =>
    !auth.isCashier &&
    (auth.isSuperAdmin ||
      auth.isStoreAdmin ||
      auth.hasPermission('customer:manage') ||
      auth.hasPermission('customer:write')),
)
const showTodoPanel = computed(() => showInvoiceTodo.value || showSocialTodo.value)
const todoAlert = computed(
  () =>
    (showInvoiceTodo.value && invoicePendingCount.value > 0) ||
    (showSocialTodo.value && socialPendingCount.value > 0),
)

const summary = computed(() => data.value?.summary || {})
const amounts = computed(() => summary.value.amounts || {})
const periodLabel = computed(() => summary.value.period_label || '今日')

const isAllStores = computed(() => !!data.value?.multi_store && !storeFilter.value)
const storeRows = computed(() => data.value?.stores || [])
const currentStoreName = computed(() => {
  if (storeFilter.value) {
    return stores.value.find((s) => s.id === storeFilter.value)?.name || data.value?.store_name
  }
  return data.value?.store_name || ''
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function fmtMoney(v: string | number | undefined) {
  return v != null && Number(v) > 0 ? `¥${v}` : '—'
}

const cards = computed(() => {
  const s = summary.value
  const a = amounts.value
  const isToday = period.value === 'today'
  return [
    { label: `${periodLabel.value}订单`, value: s.order_count ?? '-', tone: '', hint: '按下单时间' },
    {
      label: `${periodLabel.value}净营业额`,
      value: s.net_revenue != null ? `¥${s.net_revenue}` : '-',
      tone: '',
      hint: s.gross_revenue != null ? `毛收 ¥${s.gross_revenue}` : '',
    },
    {
      label: '有效已付订单',
      value: s.paid_order_count ?? '-',
      tone: '',
      hint: s.avg_order_amount != null ? `客单价 ¥${s.avg_order_amount}` : '',
    },
    {
      label: '餐品小计',
      value: a.subtotal != null ? `¥${a.subtotal}` : '-',
      tone: '',
      hint: a.payable_amount != null ? `实付 ¥${a.payable_amount}` : '',
    },
    {
      label: '合计优惠',
      value: a.total_discount != null && Number(a.total_discount) > 0 ? `¥${a.total_discount}` : '¥0',
      tone: 'discount',
      hint: '会员+券+代金券',
    },
    {
      label: `${periodLabel.value}退款`,
      value: s.refunded_amount != null ? `¥${s.refunded_amount}` : '¥0',
      tone: 'warn',
      hint: '含退非当日单',
    },
    {
      label: '会员总数',
      value: data.value?.member_total ?? '-',
      tone: '',
      hint: isAllStores.value ? '全平台注册会员' : '在本店下过单的会员',
    },
    {
      label: isToday ? '今日新增会员' : `${periodLabel.value}新注册`,
      value: s.new_registered_members ?? '-',
      tone: '',
      hint: isAllStores.value
        ? (isToday && data.value?.visitors != null ? `今日客流 ${data.value.visitors}` : '新注册顾客')
        : (s.new_store_customers ? `首单顾客 ${s.new_store_customers}` : '新注册顾客'),
    },
  ]
})

const discountRows = computed(() => {
  const a = amounts.value
  return [
    { name: '会员折扣', amount: a.member_discount || '0.00', desc: '银卡/金卡等级折扣合计' },
    { name: '优惠券', amount: a.coupon_discount || '0.00', desc: '满减/折扣券抵扣合计' },
    { name: '代金券', amount: a.voucher_discount || '0.00', desc: '代金券面额抵扣合计' },
    { name: '合计优惠', amount: a.total_discount || '0.00', desc: '以上三项之和' },
    { name: '打包费', amount: a.packing_fee || '0.00', desc: '外卖/打包费合计（非优惠）' },
  ]
})

const paymentRows = computed(() => data.value?.payments || [])

const deliveryCards = computed(() => {
  const d = data.value?.delivery || {}
  const pl = periodLabel.value
  return [
    { label: '待接单', value: d.pending ?? 0, raw: d.pending ?? 0, alert: true },
    { label: '催单中', value: d.urging ?? 0, raw: d.urging ?? 0, alert: true },
    { label: '待退款', value: d.refund_pending ?? 0, raw: d.refund_pending ?? 0, alert: true },
    { label: `${pl}外卖单`, value: d.orders ?? 0, raw: d.orders ?? 0, alert: false },
    { label: `${pl}外卖净营收`, value: d.revenue != null ? `¥${d.revenue}` : '¥0', raw: 0, alert: false },
    {
      label: `${pl}外卖优惠`,
      value: Number(d.member_discount) > 0 ? `¥${d.member_discount}` : '¥0',
      raw: d.member_discount ?? 0,
      alert: false,
    },
  ]
})

function onStoreRowClick(row: any) {
  if (!auth.isSuperAdmin || !row?.store_id) return
  storeFilter.value = row.store_id
  load()
}

async function loadInvoiceTodos() {
  if (!showInvoiceTodo.value) {
    invoicePendingCount.value = 0
    invoicePendingItems.value = []
    return
  }
  try {
    const params: Record<string, string | number> = { limit: 5 }
    if (storeFilter.value) params.store_id = storeFilter.value
    const res: any = await api.get('/admin/invoices/pending-summary', { params })
    const body = res?.data ?? res ?? {}
    invoicePendingCount.value = Number(body.pending_count || 0) || 0
    invoicePendingItems.value = Array.isArray(body.items) ? body.items : []
  } catch {
    invoicePendingCount.value = 0
    invoicePendingItems.value = []
  }
}

async function loadSocialTodos() {
  if (!showSocialTodo.value) {
    socialPendingCount.value = 0
    socialPendingItems.value = []
    return
  }
  try {
    const params: Record<string, string | number> = { limit: 5 }
    if (storeFilter.value) params.store_id = storeFilter.value
    const res: any = await api.get('/social-benefits/identities/pending-summary', { params })
    const body = res?.data ?? res ?? {}
    socialPendingCount.value = Number(body.pending_count || 0) || 0
    socialPendingItems.value = Array.isArray(body.items) ? body.items : []
  } catch {
    socialPendingCount.value = 0
    socialPendingItems.value = []
  }
}

/** 仅刷新待办（发票 + 公益），不触发整页 loading */
async function loadTodos() {
  if (!showTodoPanel.value) return
  await Promise.all([loadInvoiceTodos(), loadSocialTodos()])
}

async function reviewSocialTodo(row: any, approve: boolean) {
  if (!canReviewSocial.value || !row?.id) return
  let reject_reason: string | undefined
  if (!approve) {
    try {
      const { value } = await ElMessageBox.prompt('拒绝原因', '审核公益认定', {
        confirmButtonText: '确认拒绝',
        cancelButtonText: '取消',
        inputPlaceholder: '请输入原因',
        inputValidator: (v: string) => (v && v.trim() ? true : '拒绝原因不能为空'),
      })
      reject_reason = (value || '').trim()
    } catch {
      return
    }
  } else {
    try {
      await ElMessageBox.confirm(
        `确认通过「${row.identity_name || row.identity_code || '公益身份'}」认定？`,
        '审核公益认定',
        { confirmButtonText: '通过', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  const key = approve ? `soc-ok-${row.id}` : `soc-no-${row.id}`
  todoBusyKey.value = key
  try {
    await api.post(`/social-benefits/identities/${row.id}/review`, { approve, reject_reason })
    ElMessage.success(approve ? '已通过' : '已拒绝')
    await loadTodos()
  } catch (e: any) {
    ElMessage.error(e?.message || '审核失败')
  } finally {
    todoBusyKey.value = null
  }
}

async function issueInvoiceTodo(row: any) {
  if (!showInvoiceTodo.value || !row?.id) return
  try {
    await ElMessageBox.confirm(
      `确认为订单 ${row.order_no || `#${row.order_id}`} 标记已开票？开票结果请自行发送至 ${row.email || '顾客邮箱'}`,
      '标记已开票',
      { confirmButtonText: '确认开票', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  todoBusyKey.value = `inv-issue-${row.id}`
  try {
    await api.post(`/admin/invoices/${row.id}/issue`)
    ElMessage.success('已标记开票')
    await loadTodos()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    todoBusyKey.value = null
  }
}

async function rejectInvoiceTodo(row: any) {
  if (!showInvoiceTodo.value || !row?.id) return
  let reason = ''
  try {
    const res = await ElMessageBox.prompt('请填写驳回原因（必填）', '驳回开票申请', {
      confirmButtonText: '确认驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '如：订单已退款，无法开票',
      inputValidator: (v: string) => (v && v.trim() ? true : '驳回原因不能为空'),
    })
    reason = (res.value || '').trim()
  } catch {
    return
  }
  todoBusyKey.value = `inv-reject-${row.id}`
  try {
    await api.post(`/admin/invoices/${row.id}/reject`, { reason })
    ElMessage.success('已驳回')
    await loadTodos()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    todoBusyKey.value = null
  }
}

let todoPollTimer: ReturnType<typeof setInterval> | null = null

function stopTodoPoll() {
  if (todoPollTimer) {
    clearInterval(todoPollTimer)
    todoPollTimer = null
  }
}

function startTodoPoll() {
  stopTodoPoll()
  if (!showTodoPanel.value) return
  todoPollTimer = setInterval(() => {
    if (document.visibilityState !== 'visible') return
    void loadTodos()
  }, TODO_POLL_MS)
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible' && showTodoPanel.value) {
    void loadTodos()
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { period: period.value }
    if (storeFilter.value) params.store_id = storeFilter.value
    const [dash] = await Promise.all([
      api.get('/dashboard', { params }),
      loadTodos(),
    ])
    data.value = dash.data
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await load()
  startTodoPoll()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  stopTodoPoll()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<style scoped>
/* =================================================== */
/*              页面容器：深蓝科技底 + 变量覆盖            */
/* =================================================== */
.dashboard-page {
  /* —— Element Plus 深色变量 —— */
  --el-text-color-primary: #dbeaff;
  --el-text-color-regular: #b7d3f0;
  --el-text-color-secondary: #7a9cc4;
  --el-text-color-placeholder: #61809f;
  --el-border-color: rgba(120, 200, 255, 0.22);
  --el-border-color-light: rgba(120, 200, 255, 0.16);
  --el-border-color-lighter: rgba(120, 200, 255, 0.1);
  --el-fill-color-light: rgba(80, 165, 245, 0.1);
  --el-fill-color-lighter: rgba(80, 165, 245, 0.05);
  --el-fill-color-blank: rgba(16, 34, 66, 0.6);
  --el-bg-color: rgba(20, 42, 78, 0.55);
  --el-bg-color-overlay: rgba(20, 42, 78, 0.96);
  --el-bg-color-page: transparent;
  --el-mask-color: rgba(10, 23, 48, 0.7);

  /* —— 表格变量 —— */
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(30, 58, 102, 0.75);
  --el-table-header-text-color: #8fc8ff;
  --el-table-text-color: #cfe6ff;
  --el-table-border-color: rgba(120, 200, 255, 0.14);
  --el-table-row-hover-bg-color: rgba(80, 165, 245, 0.14);
  --el-table-current-row-bg-color: rgba(80, 165, 245, 0.2);
  --el-table-fixed-box-shadow: 8px 0 8px -8px rgba(0, 0, 0, 0.6);

  /* —— 按钮变量 —— */
  --el-button-bg-color: rgba(30, 58, 102, 0.6);
  --el-button-border-color: rgba(120, 200, 255, 0.35);
  --el-button-text-color: #b7d3f0;
  --el-button-hover-bg-color: rgba(80, 165, 245, 0.22);
  --el-button-hover-border-color: rgba(127, 200, 255, 0.75);
  --el-button-hover-text-color: #e2f2ff;

  /* —— 主色 —— */
  --el-color-primary: #5ba8f5;
  --el-color-primary-light-3: #7fc8ff;
  --el-color-primary-light-5: rgba(91, 168, 245, 0.5);
  --el-color-primary-light-7: rgba(91, 168, 245, 0.3);
  --el-color-primary-light-8: rgba(91, 168, 245, 0.2);
  --el-color-primary-light-9: rgba(91, 168, 245, 0.12);

  position: relative;
  min-height: 100vh;
  padding: 16px;
  box-sizing: border-box;
  color: #d9ecff;
  background:
    radial-gradient(circle at 25% -10%, #244a86 0%, transparent 55%),
    radial-gradient(circle at 85% 115%, #1a3a6e 0%, transparent 60%),
    linear-gradient(180deg, #0e1e3c 0%, #0a1730 100%);
  font-family: inherit;
}

/* =================================================== */
/*                      背景装饰                        */
/* =================================================== */
.bg-layer {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(120, 195, 255, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120, 195, 255, 0.055) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: radial-gradient(ellipse at 50% 30%, #000 0%, transparent 75%);
  mask-image: radial-gradient(ellipse at 50% 30%, #000 0%, transparent 75%);
}

.bg-glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(110px);
}
.glow-1 {
  width: 460px; height: 460px;
  top: -180px; left: -120px;
  background: #4a8fe7;
  opacity: 0.32;
  animation: float-a 18s ease-in-out infinite;
}
.glow-2 {
  width: 520px; height: 520px;
  bottom: -220px; right: -180px;
  background: #62d6f0;
  opacity: 0.25;
  animation: float-b 22s ease-in-out infinite;
}
@keyframes float-a {
  0%, 100% { transform: translate(0, 0); }
  50%      { transform: translate(60px, 40px); }
}
@keyframes float-b {
  0%, 100% { transform: translate(0, 0); }
  50%      { transform: translate(-70px, -50px); }
}

/* 内容层压住背景 */
.dashboard-inner {
  position: relative;
  z-index: 1;
}

/* =================================================== */
/*                      工具栏                          */
/* =================================================== */
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border: 1px solid rgba(120, 200, 255, 0.2);
  border-radius: 12px;
  background: rgba(20, 42, 78, 0.5);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  box-shadow:
    0 0 22px rgba(80, 165, 245, 0.12),
    inset 0 0 26px rgba(120, 190, 255, 0.04);
}

.scope-hint {
  color: #7a9cc4;
  font-size: 13px;
}

.refresh-btn {
  position: relative;
  border-radius: 8px;
  font-weight: 600;
  letter-spacing: 1px;
  transition: box-shadow 0.25s ease, transform 0.15s ease;
}
.refresh-btn:hover {
  box-shadow: 0 0 16px rgba(100, 185, 255, 0.45);
  transform: translateY(-1px);
}
.refresh-btn:active {
  transform: translateY(0);
}

/* =================================================== */
/*                 卡片统一深蓝玻璃                      */
/* =================================================== */
:deep(.el-card) {
  position: relative;
  border: 1px solid rgba(120, 200, 255, 0.24);
  border-radius: 12px;
  background: rgba(20, 42, 78, 0.55);
  backdrop-filter: blur(12px) saturate(130%);
  -webkit-backdrop-filter: blur(12px) saturate(130%);
  color: #d9ecff;
  overflow: visible;
  box-shadow:
    0 0 22px rgba(80, 165, 245, 0.14),
    inset 0 0 28px rgba(120, 190, 255, 0.04);
  transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.2s ease;
}
:deep(.el-card:hover) {
  border-color: rgba(127, 200, 255, 0.5);
  box-shadow:
    0 0 32px rgba(80, 165, 245, 0.32),
    inset 0 0 32px rgba(120, 190, 255, 0.07);
}
:deep(.el-card__body) {
  background: transparent;
  color: inherit;
  padding: 16px;
}

/* 顶部流动高光线（全局卡片） */
:deep(.el-card)::before {
  content: '';
  position: absolute;
  top: -1px; left: 14%;
  width: 72%; height: 1px;
  background: linear-gradient(90deg, transparent, #7fc8ff, transparent);
  filter: drop-shadow(0 0 5px rgba(127, 200, 255, 0.8));
  opacity: 0.55;
  pointer-events: none;
}

/* =================================================== */
/*                      统计卡                          */
/* =================================================== */
.stat-card {
  margin-bottom: 8px;
}

.stat {
  font-size: 24px;
  font-weight: 700;
  color: #7fc8ff;
  letter-spacing: 0.5px;
  text-shadow: 0 0 14px rgba(127, 200, 255, 0.55);
  font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', 'Menlo', monospace;
  line-height: 1.15;
}

.stat.warn {
  color: #f5c87c;
  text-shadow: 0 0 14px rgba(245, 200, 124, 0.5);
}

.stat.discount {
  color: #ff9a9a;
  text-shadow: 0 0 14px rgba(255, 154, 154, 0.5);
}

.label {
  color: #9ec1e4;
  margin-top: 8px;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.hint {
  color: #63809f;
  font-size: 12px;
  margin-top: 4px;
}

/* =================================================== */
/*                 通用小标题（sec-title）              */
/* =================================================== */
.sec-title {
  margin: 0 0 10px;
  padding-bottom: 8px;
  border-bottom: 1px dashed rgba(120, 200, 255, 0.16);
  font-size: 15px;
  font-weight: 600;
  color: #b8dcff;
  letter-spacing: 1px;
}
.sec-title::before {
  content: '';
  display: inline-block;
  width: 3px; height: 14px;
  margin-right: 8px;
  vertical-align: -2px;
  border-radius: 2px;
  background: linear-gradient(180deg, #7fc8ff, #3b7fe0);
  box-shadow: 0 0 8px rgba(127, 200, 255, 0.85);
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
/* section-head 里已用 sec-title 自带了 margin/padding/border，
   这里只做布局对齐，避免重复的 margin-bottom 叠加 */
.section-head .sec-title {
  margin-bottom: 0;
  border-bottom: none;
  padding-bottom: 0;
}

.side-card {
  height: 100%;
  margin-bottom: 8px;
}

.pay-note {
  font-size: 12px;
  color: #7a9cc4;
  margin: 0 0 10px;
}

/* =================================================== */
/*                    外卖迷你卡                        */
/* =================================================== */
.mini-stat {
  position: relative;
  padding: 12px;
  border: 1px solid rgba(120, 200, 255, 0.18);
  border-radius: 10px;
  background: rgba(16, 34, 66, 0.6);
  text-align: center;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}
.mini-stat:hover {
  border-color: rgba(127, 200, 255, 0.45);
  box-shadow: 0 0 16px rgba(80, 165, 245, 0.25);
}
.mini-stat.alert {
  border-color: rgba(245, 108, 108, 0.4);
  background: rgba(120, 30, 40, 0.28);
  box-shadow: 0 0 14px rgba(245, 108, 108, 0.18);
}
.mini-val {
  font-size: 22px;
  font-weight: 700;
  color: #7fc8ff;
  text-shadow: 0 0 12px rgba(127, 200, 255, 0.5);
  font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', 'Menlo', monospace;
}
.mini-stat.alert .mini-val {
  color: #ff9a9a;
  text-shadow: 0 0 12px rgba(255, 154, 154, 0.6);
}
.mini-label {
  font-size: 12px;
  color: #7a9cc4;
  margin-top: 4px;
  letter-spacing: 0.5px;
}

/* =================================================== */
/*                     表格深色化                        */
/* =================================================== */
:deep(.el-table) {
  background: transparent;
  color: #cfe6ff;
  font-size: 13px;
}
:deep(.el-table__inner-wrapper) {
  background: transparent;
}
:deep(.el-table th.el-table__cell) {
  background: rgba(30, 58, 102, 0.75) !important;
  color: #8fc8ff !important;
  font-weight: 600;
  letter-spacing: 0.5px;
  border-bottom: 1px solid rgba(120, 200, 255, 0.2) !important;
}
:deep(.el-table td.el-table__cell) {
  border-bottom: 1px solid rgba(120, 200, 255, 0.1);
}
:deep(.el-table--border) {
  border-color: rgba(120, 200, 255, 0.14);
}
:deep(.el-table--border .el-table__cell) {
  border-right-color: rgba(120, 200, 255, 0.1);
}
:deep(.el-table__empty-block) {
  background: transparent;
}
:deep(.el-table__empty-text) {
  color: #7a9cc4;
}
:deep(.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell) {
  background: rgba(80, 165, 245, 0.05);
}
:deep(.el-table__body tr:hover > td.el-table__cell) {
  background: rgba(80, 165, 245, 0.14) !important;
}
/* 固定列背景别透出白色 */
:deep(.el-table td.el-table-fixed-column--left),
:deep(.el-table td.el-table-fixed-column--right) {
  background: rgba(22, 46, 84, 0.92);
}
/* hover 时固定列优先级要压过斑马纹 */
:deep(.el-table__body tr:hover > td.el-table-fixed-column--left),
:deep(.el-table__body tr:hover > td.el-table-fixed-column--right) {
  background: rgba(40, 78, 130, 0.95) !important;
}
/* 高亮当前行时，固定列也跟着变 */
:deep(.el-table__body tr.current-row > td.el-table__cell) {
  background: rgba(80, 165, 245, 0.2) !important;
}

/* 滚动条深色化 */
:deep(.el-scrollbar__bar) {
  background: rgba(120, 200, 255, 0.08);
}
:deep(.el-scrollbar__thumb) {
  background: rgba(127, 200, 255, 0.4);
}
:deep(.el-scrollbar__thumb:hover) {
  background: rgba(127, 200, 255, 0.7);
}

/* =================================================== */
/*                 Radio / Select / Tag                 */
/* =================================================== */
:deep(.el-radio-button__inner) {
  background: rgba(30, 58, 102, 0.6);
  border-color: rgba(120, 200, 255, 0.32);
  color: #b7d3f0;
  box-shadow: none;
  transition: all 0.25s ease;
}
:deep(.el-radio-button__inner:hover) {
  color: #e2f2ff;
}
:deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: linear-gradient(100deg, #4f9bf5 0%, #5ba8f5 100%);
  border-color: #7fc8ff;
  color: #ffffff;
  box-shadow: 0 0 15px rgba(91, 168, 245, 0.55);
}

:deep(.el-select__wrapper) {
  background: rgba(16, 34, 66, 0.72);
  box-shadow: inset 0 0 0 1px rgba(127, 200, 255, 0.3);
  color: #dff4ff;
  transition: box-shadow 0.25s ease, background 0.25s ease;
}
:deep(.el-select__wrapper:hover) {
  box-shadow: inset 0 0 0 1px rgba(127, 200, 255, 0.55);
}
:deep(.el-select__wrapper.is-focused) {
  background: rgba(18, 40, 76, 0.92);
  box-shadow:
    inset 0 0 0 1px #7fc8ff,
    0 0 14px rgba(127, 200, 255, 0.4);
}
:deep(.el-select__placeholder) {
  color: rgba(160, 200, 240, 0.55);
}

:deep(.el-tag) {
  background: rgba(30, 58, 102, 0.7);
  border-color: rgba(120, 200, 255, 0.35);
  color: #b7d3f0;
}
:deep(.el-tag.el-tag--info) {
  background: rgba(30, 58, 102, 0.6);
  border-color: rgba(120, 200, 255, 0.35);
  color: #a8cdf0;
}
:deep(.el-tag.el-tag--warning) {
  background: rgba(200, 120, 30, 0.22);
  border-color: rgba(230, 162, 60, 0.5);
  color: #f5c87c;
}
:deep(.el-tag.el-tag--warning.el-tag--dark) {
  background: linear-gradient(100deg, #e6a23c, #f0b95c);
  border-color: #f0b95c;
  color: #241503;
  box-shadow: 0 0 12px rgba(230, 162, 60, 0.45);
}
:deep(.el-tag.el-tag--danger) {
  background: rgba(200, 50, 50, 0.22);
  border-color: rgba(245, 108, 108, 0.5);
  color: #ff9a9a;
}

/* link 型按钮（进入发票申请等）颜色跟着主色走 */
:deep(.el-button.is-link) {
  color: #7fc8ff;
}
:deep(.el-button.is-link:hover) {
  color: #a5daff;
}

/* =================================================== */
/*                   待办卡强调态                        */
/* =================================================== */
.todo-card.todo-alert {
  border-color: rgba(245, 200, 124, 0.5);
  box-shadow:
    0 0 26px rgba(230, 162, 60, 0.22),
    inset 0 0 28px rgba(230, 162, 60, 0.06);
}
.todo-card.todo-alert::before {
  background: linear-gradient(90deg, transparent, #f5c87c, transparent);
  filter: drop-shadow(0 0 6px rgba(245, 200, 124, 0.85));
  opacity: 1;
}

.todo-links {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.todo-poll-hint {
  font-size: 12px;
  color: #7a9cc4;
  margin-right: 6px;
}

.todo-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.todo-sub {
  display: block;
  font-size: 12px;
  color: #7a9cc4;
}

.todo-list-gap {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(120, 200, 255, 0.12);
}

.todo-empty {
  color: #7a9cc4;
  font-size: 13px;
  padding: 4px 0 2px;
}

/* =================================================== */
/*                      小工具                          */
/* =================================================== */
.store-table :deep(.el-table__row) {
  cursor: pointer;
}

.store-link {
  font-weight: 600;
  color: #cfe6ff;
}

.store-no {
  margin-left: 6px;
  font-size: 12px;
  color: #5f7c9c;
}

.text-warn {
  color: #f5c87c;
  text-shadow: 0 0 8px rgba(245, 200, 124, 0.4);
}

/* Loading 遮罩改成深色 */
:deep(.el-loading-mask) {
  background: rgba(10, 23, 48, 0.7);
  backdrop-filter: blur(2px);
}
:deep(.el-loading-spinner .path) {
  stroke: #7fc8ff;
}

/* =================================================== */
/*                   响应式 & 降级                       */
/* =================================================== */
@media (max-width: 768px) {
  .dashboard-page {
    padding: 12px;
  }
  .stat {
    font-size: 20px;
  }
  .mini-val {
    font-size: 18px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .bg-glow {
    animation: none;
  }
}
</style>

<!-- ===================================================
     以下为非 scoped 全局样式：
     处理 el-select 下拉、tooltip 等 teleport 到 body 的浮层，
     这些元素不在本组件 DOM 树内，scoped 的 :deep() 覆盖不到。
     通过 popper-class 精确命中，避免污染全局其他页面。
     =================================================== -->
<style>
/* ---------- Select 下拉面板 ---------- */
.dashboard-dark-popper.el-popper {
  background: rgba(20, 42, 78, 0.98) !important;
  border: 1px solid rgba(120, 200, 255, 0.3) !important;
  box-shadow:
    0 0 26px rgba(80, 165, 245, 0.3),
    0 14px 40px rgba(0, 0, 0, 0.5) !important;
  color: #cfe6ff;
}
.dashboard-dark-popper.el-popper .el-popper__arrow::before {
  background: rgba(20, 42, 78, 0.98) !important;
  border-color: rgba(120, 200, 255, 0.3) !important;
}
.dashboard-dark-popper .el-select-dropdown__wrap {
  max-height: 274px;
}
.dashboard-dark-popper .el-select-dropdown__list {
  padding: 4px 0;
  background: transparent;
}
.dashboard-dark-popper .el-select-dropdown__item {
  color: #cfe6ff;
  font-size: 13px;
}
.dashboard-dark-popper .el-select-dropdown__item.is-hovering,
.dashboard-dark-popper .el-select-dropdown__item:hover {
  background: rgba(80, 165, 245, 0.18);
}
.dashboard-dark-popper .el-select-dropdown__item.is-selected {
  color: #7fc8ff;
  font-weight: 600;
}
.dashboard-dark-popper .el-select-dropdown__item.is-selected::after {
  color: #7fc8ff;
}
.dashboard-dark-popper .el-select-dropdown__empty {
  color: #7a9cc4;
  padding: 14px 0;
}
</style>