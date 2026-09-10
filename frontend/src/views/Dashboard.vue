<template>
  <div v-loading="loading">
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
      <el-button style="margin-left:auto" @click="load">刷新</el-button>
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

    <el-row :gutter="16" style="margin-top:16px">
      <el-col :xs="24" :md="10">
        <el-card shadow="hover" class="side-card">
          <div class="section-head">
            <h3>{{ periodLabel }}优惠构成</h3>
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
            <h3>近 7 日趋势</h3>
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
            <h3>各门店{{ periodLabel }}销售</h3>
            <span class="scope-hint">点击行切换单店视图</span>
          </div>
          <el-table
            :data="storeRows"
            size="small"
            stripe
            highlight-current-row
            class="store-table"
            @row-click="onStoreRowClick"
          >
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
            <h3>外卖实时</h3>
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
          <h3>{{ periodLabel }}商品销量 TOP{{ isAllStores ? '20' : '15' }}</h3>
          <el-table :data="data?.product_sales || []" size="small" border>
            <el-table-column v-if="isAllStores" prop="store_name" label="门店" width="120" />
            <el-table-column prop="name" label="商品" min-width="140" />
            <el-table-column prop="sales" label="销量（份）" width="100" align="right" />
          </el-table>
        </el-card>
      </el-col>
      <el-col v-if="!isAllStores" :span="12">
        <el-card>
          <h3>支付方式（{{ periodLabel }}）</h3>
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
          <h3>支付方式（{{ periodLabel }}·全店汇总）</h3>
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
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const data = ref<any>(null)
const loading = ref(false)
const stores = ref<any[]>([])
const storeFilter = ref<number | undefined>()
const period = ref<'today' | 'yesterday' | 'month'>('today')

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
      label: '商品小计',
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
    const params: Record<string, string | number> = { period: period.value }
    if (storeFilter.value) params.store_id = storeFilter.value
    const res = await api.get('/dashboard', { params })
    data.value = res.data
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
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.scope-hint { color: #909399; font-size: 13px; }
.stat-card { margin-bottom: 8px; }
.stat { font-size: 24px; font-weight: bold; color: #409eff; }
.stat.warn { color: #e6a23c; }
.stat.discount { color: #f56c6c; }
.label { color: #999; margin-top: 8px; font-size: 13px; }
.hint { color: #c0c4cc; font-size: 12px; margin-top: 4px; }
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 { margin: 0; font-size: 16px; }
.side-card { height: 100%; margin-bottom: 8px; }
.pay-note { font-size: 12px; color: #909399; margin: 0 0 10px; }
.mini-stat {
  background: #f7f8fa;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}
.mini-stat.alert { background: #fef0f0; }
.mini-val { font-size: 22px; font-weight: 700; color: #303133; }
.mini-label { font-size: 12px; color: #909399; margin-top: 4px; }
.store-table :deep(.el-table__row) { cursor: pointer; }
.store-link { font-weight: 600; color: #303133; }
.store-no { margin-left: 6px; font-size: 12px; color: #c0c4cc; }
.text-warn { color: #e6a23c; }
</style>
