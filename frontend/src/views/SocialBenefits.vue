<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>社会贡献</h2>
        <p class="sub">环卫、骑手、长者、教师、学生、医护、残障、退役军人、志愿者等公益身份认定、优惠规则与核销台账（与券互斥；与会员折取更优）。</p>
      </div>
      <el-button @click="reloadCurrent">刷新</el-button>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 小程序会员中心可申请身份（类型来自身份字典），或收银 POS「公益核验」当场认定；② 本页审核「待审核 → 已生效」；③ 规则在「权益规则」查看（种子含环卫/骑手/长者/教师/学生/医护等）；④ 「核销台账 / 本月看板」看人次与补贴。证件图不在此展示。"
    />

    <el-tabs v-model="tab" class="tabs">
      <el-tab-pane label="待审认定" name="pending">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          class="tab-tip"
          title="只显示待审核申请。通过后顾客下单可走公益折扣；拒绝请填写原因便于审计。"
        />
        <el-empty v-if="!loading && !identities.length" description="暂无待审申请；可让顾客在小程序申请，或收银当场核验" />
        <el-table v-else :data="identities" v-loading="loading" stripe>
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column label="顾客" min-width="140">
            <template #default="{ row }">
              <div>{{ row.customer_nickname || row.customer_no || ('#' + row.customer_id) }}</div>
              <div style="color:#888;font-size:12px">{{ row.customer_phone || '无手机' }} · ID {{ row.customer_id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="身份" width="110">
            <template #default="{ row }">{{ identityLabel(row.identity_code) }}</template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">{{ verifyMethodLabel(row.verify_method) }}</template>
          </el-table-column>
          <el-table-column prop="store_id" label="门店" width="80">
            <template #default="{ row }">{{ row.store_id ?? '未绑店' }}</template>
          </el-table-column>
          <el-table-column prop="credential_note" label="凭证备注" min-width="140" />
          <el-table-column prop="created_at" label="申请时间" min-width="160" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">{{ identityStatusLabel(row.status) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'PENDING'"
                size="small"
                type="primary"
                @click="review(row, true)"
              >通过</el-button>
              <el-button
                v-if="row.status === 'PENDING'"
                size="small"
                @click="review(row, false)"
              >拒绝</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="权益规则" name="rules">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="tab-tip"
          title="规则由系统种子 / 运维配置。「与券互斥」：与会员折取更优（折扣更低者）。「可叠加」暂未接线结算。门店列为空=总部通用。"
        />
        <el-empty v-if="!loading && !rules.length" description="暂无启用规则，请检查系统种子或是否开启社会贡献公益" />
        <el-table v-else :data="rules" v-loading="loading" stripe>
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="身份" width="120">
            <template #default="{ row }">{{ identityLabel(row.identity_code) }}</template>
          </el-table-column>
          <el-table-column prop="discount_rate" label="折扣率" width="100" />
          <el-table-column prop="daily_limit" label="日限次" width="90" />
          <el-table-column label="叠加策略" width="120">
            <template #default="{ row }">{{ stackPolicyLabel(row.stack_policy) }}</template>
          </el-table-column>
          <el-table-column prop="store_id" label="门店" width="90">
            <template #default="{ row }">{{ row.store_id ?? '总部' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="核销台账" name="redemptions">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="tab-tip"
          title="每笔公益优惠落台账。退款会冲减（已冲减）。补贴金额用于本月看板汇总。"
        />
        <el-empty v-if="!loading && !redemptions.length" description="暂无核销记录" />
        <el-table v-else :data="redemptions" v-loading="loading" stripe>
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="customer_id" label="顾客" width="90" />
          <el-table-column label="身份" width="110">
            <template #default="{ row }">{{ identityLabel(row.identity_code) }}</template>
          </el-table-column>
          <el-table-column prop="subsidy_amount" label="补贴" width="90" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">{{ redeemStatusLabel(row.status) }}</template>
          </el-table-column>
          <el-table-column prop="order_id" label="订单" width="90" />
          <el-table-column prop="created_at" label="时间" min-width="160" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="本月看板" name="stats">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          class="tab-tip"
          title="按自然月统计已生效核销。可与「营销报表」对照活动补贴，勿把公益台账与营销归因混为一谈。"
        />
        <el-descriptions v-loading="loading" :column="3" border style="margin-bottom:12px">
          <el-descriptions-item label="月份">{{ stats?.year }}-{{ stats?.month }}</el-descriptions-item>
          <el-descriptions-item label="核销人次">{{ stats?.redemptions ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="补贴合计">¥{{ stats?.subsidy_amount || '0.00' }}</el-descriptions-item>
          <el-descriptions-item label="待审认定">{{ stats?.pending_reviews ?? 0 }}</el-descriptions-item>
        </el-descriptions>
        <el-empty v-if="!loading && !(stats?.by_identity || []).length" description="本月尚无按身份汇总的核销" />
        <el-table v-else :data="stats?.by_identity || []" stripe>
          <el-table-column label="身份">
            <template #default="{ row }">{{ identityLabel(row.identity_code) }}</template>
          </el-table-column>
          <el-table-column prop="redemptions" label="人次" width="100" />
          <el-table-column prop="subsidy_amount" label="补贴" width="120" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { SOCIAL_IDENTITY } from '@/utils/labels'

const route = useRoute()
const allowedTabs = new Set(['pending', 'rules', 'redemptions', 'stats'])
const initialTab = String(route.query.tab || 'pending')
const tab = ref(allowedTabs.has(initialTab) ? initialTab : 'pending')
const loading = ref(false)
const identities = ref<any[]>([])
const rules = ref<any[]>([])
const redemptions = ref<any[]>([])
const stats = ref<any>(null)

const VERIFY_LABELS: Record<string, string> = {
  WECHAT: '微信',
  ALIPAY: '支付宝',
  MINI: '小程序',
  POS: '收银核验',
  MANUAL: '手工认定',
}
const identityTypeMap = ref<Record<string, string>>({})
const IDENTITY_STATUS_LABELS: Record<string, string> = {
  PENDING: '待审核',
  ACTIVE: '已生效',
  REJECTED: '已拒绝',
  REVOKED: '已取消',
  EXPIRED: '已过期',
}
const REDEEM_STATUS_LABELS: Record<string, string> = {
  PENDING: '待确认',
  NORMAL: '已生效',
  REVERSED: '已冲减',
}
const STACK_POLICY_LABELS: Record<string, string> = {
  EXCLUSIVE: '与券互斥；与会员折取更优（折扣更低者）',
  STACKABLE: '可叠加（结算尚未接线，请勿依赖）',
}

function verifyMethodLabel(method: string) {
  const key = String(method || '').toUpperCase()
  return VERIFY_LABELS[key] || '—'
}

function identityLabel(code: string) {
  const key = String(code || '').toUpperCase()
  return identityTypeMap.value[key] || SOCIAL_IDENTITY[key] || '—'
}

async function loadIdentityTypes() {
  try {
    const res = await api.get('/social-benefits/identity-types')
    const map: Record<string, string> = {}
    for (const t of res.data?.items || []) {
      const code = String(t.code || '').toUpperCase()
      const name = String(t.name || '').trim()
      if (code && name) map[code] = name
    }
    identityTypeMap.value = map
  } catch {
    identityTypeMap.value = {}
  }
}

function identityStatusLabel(status: string) {
  const key = String(status || '').toUpperCase()
  return IDENTITY_STATUS_LABELS[key] || '—'
}

function redeemStatusLabel(status: string) {
  const key = String(status || '').toUpperCase()
  return REDEEM_STATUS_LABELS[key] || '—'
}

function stackPolicyLabel(policy: string) {
  const key = String(policy || '').toUpperCase()
  return STACK_POLICY_LABELS[key] || '—'
}

async function loadPending() {
  loading.value = true
  try {
    const res = await api.get('/social-benefits/identities', { params: { status: 'PENDING', page_size: 50 } })
    identities.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

async function loadRules() {
  loading.value = true
  try {
    const res = await api.get('/social-benefits/rules')
    rules.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

async function loadRedemptions() {
  loading.value = true
  try {
    const res = await api.get('/social-benefits/redemptions', { params: { page_size: 50 } })
    redemptions.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  loading.value = true
  try {
    const res = await api.get('/social-benefits/stats')
    stats.value = res.data || null
  } finally {
    loading.value = false
  }
}

function reloadCurrent() {
  if (tab.value === 'pending') return loadPending()
  if (tab.value === 'rules') return loadRules()
  if (tab.value === 'stats') return loadStats()
  return loadRedemptions()
}

async function review(row: any, approve: boolean) {
  let reject_reason: string | undefined
  if (!approve) {
    const { value } = await ElMessageBox.prompt('拒绝原因', '审核', { inputPlaceholder: '请输入原因' })
    reject_reason = value
  }
  await api.post(`/social-benefits/identities/${row.id}/review`, { approve, reject_reason })
  ElMessage.success(approve ? '已通过' : '已拒绝')
  await loadPending()
}

watch(tab, (t) => {
  if (t === 'pending') loadPending()
  else if (t === 'rules') loadRules()
  else if (t === 'stats') loadStats()
  else loadRedemptions()
})

onMounted(() => {
  loadIdentityTypes()
  reloadCurrent()
})
</script>

<style scoped>
.page { padding: 8px 4px; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; }
.tip { margin-bottom: 12px; }
.tab-tip { margin-bottom: 12px; }
.tabs { margin-top: 4px; }
</style>
