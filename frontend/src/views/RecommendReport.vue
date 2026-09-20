<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item label="实验">
        <el-select
          v-model="experimentKey"
          placeholder="选择实验"
          style="width:280px"
          @change="loadReport"
        >
          <el-option
            v-for="e in experiments"
            :key="e.key"
            :label="`${e.name}（${e.key}）`"
            :value="e.key"
          >
            <span style="float:left">{{ e.name }}</span>
            <el-tag
              size="small"
              :type="e.status === 'RUNNING' ? 'success' : (e.status === 'PAUSED' ? 'warning' : 'info')"
              style="float:right;margin-top:4px"
            >{{ statusText(e.status) }}</el-tag>
          </el-option>
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width:260px"
        />
      </el-form-item>
      <el-button type="primary" @click="loadReport">查询</el-button>
      <el-button @click="setDefaultRange">近14天</el-button>
    </el-form>

    <el-alert
      v-if="report?.attribution_note"
      :title="report.attribution_note"
      type="info"
      :closable="false"
      show-icon
      class="note"
    />

    <el-empty v-if="!loading && report && !report.buckets?.length" description="该实验暂无桶配置" />

    <template v-if="report">
      <div class="section-title">
        24 小时推荐归因漏斗
        <span class="muted">（按「同顾客 × 同商品」去重计商品人次）</span>
      </div>
      <el-table :data="funnelRows" border class="funnel-table">
        <el-table-column prop="bucketName" label="桶" width="200" />
        <el-table-column label="曝光 → 点击 → 加购 → 支付" min-width="420">
          <template #default="{ row }">
            <div class="chain">
              <div class="chain-step">
                <span class="num">{{ row.exposed_pairs }}</span>
                <span class="lab">曝光</span>
              </div>
              <span class="arrow">→</span>
              <div class="chain-step">
                <span class="num">{{ row.click_pairs }}</span>
                <span class="lab">点击</span>
              </div>
              <span class="arrow">→</span>
              <div class="chain-step">
                <span class="num">{{ row.cart_pairs }}</span>
                <span class="lab">加购</span>
              </div>
              <span class="arrow">→</span>
              <div class="chain-step">
                <span class="num hot">{{ row.order_pairs }}</span>
                <span class="lab">支付</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="点击率" width="100">
          <template #default="{ row }">{{ pct(row.click_rate) }}</template>
        </el-table-column>
        <el-table-column label="加购采纳率" width="110">
          <template #default="{ row }">
            <strong>{{ pct(row.adopt_rate) }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="下单转化率" width="110">
          <template #default="{ row }">{{ pct(row.order_conv) }}</template>
        </el-table-column>
      </el-table>

      <div class="section-title">转化对比（以各桶曝光商品人次为基数）</div>
      <div class="bars">
        <div v-for="row in funnelRows" :key="`bar-${row.code}`" class="bar-group">
          <div class="bar-head">
            <el-tag size="small" effect="plain">{{ row.bucketName }}</el-tag>
          </div>
          <div class="bar-line">
            <span class="bar-lab">点击率</span>
            <div class="bar-track"><div class="bar-fill c1" :style="{ width: barW(row.click_rate) }"></div></div>
            <span class="bar-val">{{ pct(row.click_rate) }}</span>
          </div>
          <div class="bar-line">
            <span class="bar-lab">采纳率</span>
            <div class="bar-track"><div class="bar-fill c2" :style="{ width: barW(row.adopt_rate) }"></div></div>
            <span class="bar-val">{{ pct(row.adopt_rate) }}</span>
          </div>
          <div class="bar-line">
            <span class="bar-lab">下单率</span>
            <div class="bar-track"><div class="bar-fill c3" :style="{ width: barW(row.order_conv) }"></div></div>
            <span class="bar-val">{{ pct(row.order_conv) }}</span>
          </div>
        </div>
      </div>

      <div class="section-title">推荐 GMV 与流量</div>
      <el-table :data="report.buckets || []" border>
        <el-table-column prop="name" label="桶" min-width="180" />
        <el-table-column prop="assigned" label="已分流人数" width="110" />
        <el-table-column prop="exposed_users" label="曝光顾客数" width="110" />
        <el-table-column label="归因支付顾客" width="120">
          <template #default="{ row }">{{ row.funnel?.attributed_paid_users ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="归因推荐 GMV" width="150">
          <template #default="{ row }">
            <strong class="gmv">¥{{ row.funnel?.attributed_gmv ?? '0.00' }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="人均推荐 GMV" width="140">
          <template #default="{ row }">
            <span v-if="row.funnel?.gmv_per_paid_user">¥{{ row.funnel.gmv_per_paid_user }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="曝光次数" width="100">
          <template #default="{ row }">{{ row.exposed }}</template>
        </el-table-column>
        <el-table-column label="点击次数" width="100">
          <template #default="{ row }">{{ row.clicks }}</template>
        </el-table-column>
        <el-table-column label="加购次数" width="100">
          <template #default="{ row }">{{ row.cart_adds }}</template>
        </el-table-column>
        <el-table-column label="事件 CTR" width="100">
          <template #default="{ row }">{{ pct(row.ctr) }}</template>
        </el-table-column>
        <el-table-column label="事件加购率" width="110">
          <template #default="{ row }">{{ pct(row.cart_rate) }}</template>
        </el-table-column>
        <el-table-column label="窗口内付费率" width="120">
          <template #default="{ row }">{{ pct(row.order_rate) }}</template>
        </el-table-column>
      </el-table>

      <p class="foot-note">
        统计窗口：{{ report.date_from ? report.date_from.slice(0, 10) : '不限' }}
        ～ {{ report.date_to ? report.date_to.slice(0, 10) : '不限' }}；
        归因场景：{{ (report.scene_scope || []).join('、') }}；
        「事件」列为原始事件计数（同一顾客多次曝光/点击会重复计），漏斗列按顾客×商品去重。
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

interface Bucket {
  code: string
  name: string
  assigned: number
  exposed: number
  clicks: number
  cart_adds: number
  exposed_users: number
  paid_users: number
  ctr: number | null
  cart_rate: number | null
  order_rate: number | null
  funnel?: {
    exposed_pairs: number
    click_pairs: number
    cart_pairs: number
    order_pairs: number
    click_rate: number | null
    adopt_rate: number | null
    order_conv: number | null
    attributed_paid_users: number
    attributed_gmv: string
    gmv_per_paid_user: string | null
  }
}

interface Report {
  experiment: { key: string; name: string; status: string }
  date_from: string | null
  date_to: string | null
  funnel_window_hours: number
  scene_scope: string[]
  attribution_note: string
  buckets: Bucket[]
}

const loading = ref(false)
const experiments = ref<Array<{ key: string; name: string; status: string }>>([])
const experimentKey = ref('')
const dateRange = ref<[string, string] | null>(null)
const report = ref<Report | null>(null)

const funnelRows = computed(() =>
  (report.value?.buckets || []).map((b) => ({
    code: b.code,
    bucketName: `${b.name}（${b.code}）`,
    ...(b.funnel || {
      exposed_pairs: 0,
      click_pairs: 0,
      cart_pairs: 0,
      order_pairs: 0,
      click_rate: null,
      adopt_rate: null,
      order_conv: null,
      attributed_paid_users: 0,
      attributed_gmv: '0.00',
      gmv_per_paid_user: null,
    }),
  })),
)

function statusText(s: string) {
  return s === 'RUNNING' ? '运行中' : s === 'PAUSED' ? '已暂停' : '草稿'
}

function pct(v: number | null | undefined) {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function barW(v: number | null | undefined) {
  if (!v) return '0%'
  return `${Math.max(2, Math.min(100, v * 100))}%`
}

function setDefaultRange() {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 13)
  const fmt = (d: Date) => {
    const m = `${d.getMonth() + 1}`.padStart(2, '0')
    const day = `${d.getDate()}`.padStart(2, '0')
    return `${d.getFullYear()}-${m}-${day}`
  }
  dateRange.value = [fmt(start), fmt(end)]
  loadReport()
}

async function loadExperiments() {
  try {
    const res: any = await api.get('/admin/ab-experiments')
    experiments.value = res.data || []
    if (!experimentKey.value) {
      const def = experiments.value.find((e) => e.key === 'reco_algo') || experiments.value[0]
      if (def) {
        experimentKey.value = def.key
        await loadReport()
      }
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '实验列表加载失败')
  }
}

async function loadReport() {
  if (!experimentKey.value) return
  loading.value = true
  try {
    const params: Record<string, string> = {}
    if (dateRange.value?.[0]) params.date_from = dateRange.value[0]
    if (dateRange.value?.[1]) params.date_to = dateRange.value[1]
    const res: any = await api.get(`/admin/ab-experiments/${experimentKey.value}/report`, { params })
    report.value = res.data
  } catch (e: any) {
    ElMessage.error(e?.message || '报表加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  setDefaultRange()
  loadExperiments()
})
</script>

<style scoped>
.toolbar {
  margin-bottom: 8px;
}
.note {
  margin: 8px 0 16px;
}
.section-title {
  margin: 20px 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.muted {
  color: #909399;
  font-size: 12px;
  font-weight: 400;
}
.chain {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chain-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 56px;
}
.chain-step .num {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}
.chain-step .num.hot {
  color: #e85b3a;
}
.chain-step .lab {
  font-size: 12px;
  color: #909399;
}
.arrow {
  color: #c0c4cc;
}
.bars {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.bar-group {
  flex: 1;
  min-width: 320px;
  background: #faf7f2;
  border-radius: 10px;
  padding: 12px 16px;
}
.bar-head {
  margin-bottom: 8px;
}
.bar-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.bar-lab {
  width: 56px;
  font-size: 12px;
  color: #606266;
}
.bar-track {
  flex: 1;
  height: 14px;
  background: #eee5d8;
  border-radius: 7px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 7px;
  transition: width 0.4s ease;
}
.bar-fill.c1 { background: #9ab8e8; }
.bar-fill.c2 { background: #e8a35c; }
.bar-fill.c3 { background: #e85b3a; }
.bar-val {
  width: 52px;
  text-align: right;
  font-size: 12px;
  color: #303133;
}
.gmv {
  color: #e85b3a;
}
.foot-note {
  margin-top: 12px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
</style>
