<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="storeFilter"
        placeholder="全部门店"
        clearable
        style="width:160px"
        @change="load"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <span v-if="auth.isSuperAdmin && !storeFilter" class="scope-hint">当前为全部门店汇总</span>
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
      <el-col :span="12">
        <el-card>
          <h3>热销商品 TOP10（近30天·未全额退款）</h3>
          <el-table :data="data?.hot_products || []" size="small">
            <el-table-column prop="name" label="商品" />
            <el-table-column prop="sales" label="销量" width="100" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <h3>支付方式（今日）</h3>
          <p class="pay-note">按今日成功支付笔数统计；退款后仍计入笔数，金额区分毛收/退款/净收。</p>
          <el-table :data="payRows" size="small" border>
            <el-table-column prop="name" label="渠道" width="90" />
            <el-table-column prop="count" label="笔数" width="80" />
            <el-table-column prop="gross" label="入账" />
            <el-table-column prop="refunded" label="退款" />
            <el-table-column prop="net" label="净收" />
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

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const cards = computed(() => [
  { label: '今日订单', value: data.value?.today_orders ?? '-', tone: '', hint: '按下单时间' },
  {
    label: '今日净营业额',
    value: data.value?.today_revenue != null ? `¥${data.value.today_revenue}` : '-',
    tone: '',
    hint: [
      data.value?.today_gross_revenue != null ? `毛收 ¥${data.value.today_gross_revenue}` : '',
      Number(data.value?.today_member_discount) > 0 ? `会员优惠 ¥${data.value.today_member_discount}` : '',
    ].filter(Boolean).join(' · ') || undefined,
  },
  {
    label: '今日会员优惠',
    value:
      data.value?.today_member_discount != null && Number(data.value.today_member_discount) > 0
        ? `¥${data.value.today_member_discount}`
        : '¥0',
    tone: 'discount',
    hint: '有效已付订单优惠合计',
  },
  {
    label: '今日退款',
    value: data.value?.today_refunded != null ? `¥${data.value.today_refunded}` : '¥0',
    tone: 'warn',
    hint: '含退昨日单',
  },
  { label: '今日客流', value: data.value?.today_visitors ?? '-', tone: '' },
  { label: '新会员', value: data.value?.today_new_members ?? '-', tone: '' },
  {
    label: '平均客单价',
    value: data.value?.avg_order_amount != null ? `¥${data.value.avg_order_amount}` : '-',
    tone: '',
    hint: data.value?.paid_order_count != null ? `${data.value.paid_order_count} 笔有效已付` : '',
  },
])

const payRows = computed(() => {
  const pr = data.value?.payment_ratio || {}
  const wx = pr.wechat_detail || { count: pr.wechat || 0, gross_amount: '0.00', refunded_amount: '0.00', net_amount: '0.00' }
  const ali = pr.alipay_detail || { count: pr.alipay || 0, gross_amount: '0.00', refunded_amount: '0.00', net_amount: '0.00' }
  return [
    {
      name: '微信',
      count: wx.count ?? 0,
      gross: `¥${wx.gross_amount ?? '0.00'}`,
      refunded: `¥${wx.refunded_amount ?? '0.00'}`,
      net: `¥${wx.net_amount ?? '0.00'}`,
    },
    {
      name: '支付宝',
      count: ali.count ?? 0,
      gross: `¥${ali.gross_amount ?? '0.00'}`,
      refunded: `¥${ali.refunded_amount ?? '0.00'}`,
      net: `¥${ali.net_amount ?? '0.00'}`,
    },
  ]
})

const deliveryCards = computed(() => {
  const d = data.value?.delivery || {}
  return [
    { label: '待接单', value: d.pending ?? 0, raw: d.pending ?? 0, alert: true },
    { label: '催单中', value: d.urging ?? 0, raw: d.urging ?? 0, alert: true },
    { label: '待退款', value: d.refund_pending ?? 0, raw: d.refund_pending ?? 0, alert: true },
    { label: '今日外卖单', value: d.today_orders ?? 0, raw: d.today_orders ?? 0, alert: false },
    { label: '今日外卖净营收', value: d.today_revenue != null ? `¥${d.today_revenue}` : '¥0', raw: 0, alert: false },
    {
      label: '今日外卖优惠',
      value: Number(d.today_member_discount) > 0 ? `¥${d.today_member_discount}` : '¥0',
      raw: d.today_member_discount ?? 0,
      alert: false,
    },
  ]
})

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
    const res = await api.get('/dashboard/overview', {
      params: { store_id: storeFilter.value },
    })
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
}
.scope-hint { color: #909399; font-size: 13px; }
.stat-card { margin-bottom: 8px; }
.stat { font-size: 26px; font-weight: bold; color: #409eff; }
.stat.warn { color: #e6a23c; }
.stat.discount { color: #f56c6c; }
.label { color: #999; margin-top: 8px; }
.hint { color: #c0c4cc; font-size: 12px; margin-top: 4px; }
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 { margin: 0; }
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
</style>
