<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>营销活动</h2>
        <p class="sub">绑定分群 + 券模板后发布发券；可选推荐加权商品。状态流：DRAFT → 发布 → RUNNING / PAUSED → 结束。</p>
      </div>
      <div class="header-actions">
        <el-button @click="goSegments">客户分群</el-button>
        <el-button @click="goReport">营销报表</el-button>
        <el-button @click="goCoupons">优惠券模板</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openEdit()">新建活动</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 先在「客户分群」估人数并物化 → ② 在「优惠券」准备启用中的券模板 → ③ 本页新建活动选分群与模板 → ④「发布」生成发券任务（幂等）→ ⑤「任务」看 SUCCESS/FAILED，可重试失败 → ⑥「营销报表」看核销率与 ROI。推荐加权仅 RUNNING 期间生效约 +25%。"
    />
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      class="tip"
      title="注意"
      description="发布前请确认分群已物化、券模板启用且库存/有效期合理。公益类活动（SOCIAL）与「社会贡献」身份折扣不同：前者是营销发券，后者是身份认定折扣且默认与券互斥。"
    />

    <el-empty v-if="!loading && !items.length" description="暂无活动；点「新建活动」开始，或先去客户分群圈人" />
    <el-table v-else :data="items" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="code" label="编码" width="140" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="campaign_type" label="类型" width="110" />
      <el-table-column prop="status" label="状态" width="110" />
      <el-table-column label="分群" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ segmentLabel(row.segment_id) }}</template>
      </el-table-column>
      <el-table-column label="券模板" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ couponLabel(row.coupon_template_id) }}</template>
      </el-table-column>
      <el-table-column label="推荐加权" width="120" show-overflow-tooltip>
        <template #default="{ row }">
          {{ (row.boost_product_ids || []).join(',') || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'DRAFT'" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            v-if="row.status === 'DRAFT' || row.status === 'SCHEDULED'"
            size="small"
            type="primary"
            @click="publish(row)"
          >发布</el-button>
          <el-button
            v-if="row.status === 'RUNNING'"
            size="small"
            @click="setStatus(row, 'PAUSED')"
          >暂停</el-button>
          <el-button
            v-if="row.status === 'PAUSED'"
            size="small"
            type="success"
            @click="setStatus(row, 'RUNNING')"
          >恢复</el-button>
          <el-button size="small" @click="showTasks(row)">任务</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑活动' : '新建活动'" width="600px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="草稿可反复改；发布后按分群快照批量发券。无分群/无模板时可先去对应菜单准备。"
      />
      <el-form label-width="120px">
        <el-form-item label="编码">
          <el-input v-model="form.code" placeholder="如 member_day_0415" />
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.campaign_type" style="width:100%">
            <el-option label="会员日 MEMBER_DAY" value="MEMBER_DAY" />
            <el-option label="节日 FESTIVAL" value="FESTIVAL" />
            <el-option label="时段 TIME_SLOT" value="TIME_SLOT" />
            <el-option label="公益营销 SOCIAL（发券，非身份折扣）" value="SOCIAL" />
          </el-select>
        </el-form-item>
        <el-form-item label="分群">
          <el-select v-model="form.segment_id" filterable clearable placeholder="选择分群" style="width:100%">
            <el-option
              v-for="s in segments"
              :key="s.id"
              :label="`${s.name} (#${s.id}${s.estimated_count != null ? ' · 约' + s.estimated_count + '人' : ''})`"
              :value="s.id"
            />
          </el-select>
          <div class="hint">建议选用已物化的分群，保证本轮圈人稳定。</div>
        </el-form-item>
        <el-form-item label="券模板">
          <el-select
            v-model="form.coupon_template_id"
            filterable
            clearable
            placeholder="选择启用中的券模板"
            style="width:100%"
          >
            <el-option
              v-for="t in couponTemplates"
              :key="t.id"
              :label="`${t.name} (#${t.id} · ${t.coupon_type || ''})`"
              :value="t.id"
            />
          </el-select>
          <div class="hint">列表来自「优惠券」启用模板；若为空请先去创建并启用。</div>
        </el-form-item>
        <el-form-item label="推荐加权商品">
          <el-input
            v-model="form.boost_product_ids_text"
            placeholder="商品 ID，逗号分隔，如 12,34"
          />
          <div class="hint">RUNNING 期间这些 SKU 在推荐列表加权约 +25%；活动结束自动失效。</div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="taskDlg" :title="`任务监控 #${taskCampaignId}`" width="720px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="发券任务按幂等键去重。FAILED 可点「重试 FAILED」重置后由 worker 再发。"
      />
      <p v-if="taskStats">
        汇总：
        <span v-for="(v, k) in taskStats" :key="k" style="margin-right: 12px">{{ k }}={{ v }}</span>
      </p>
      <el-button size="small" @click="retryFailed" style="margin-bottom: 8px">重试 FAILED</el-button>
      <el-empty v-if="!tasks.length" description="暂无任务（未发布或尚未入队）" />
      <el-table v-else :data="tasks" size="small" stripe max-height="360">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="customer_id" label="顾客" width="90" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="idempotency_key" label="幂等键" min-width="180" />
        <el-table-column prop="last_error" label="错误" min-width="140" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const items = ref<any[]>([])
const segments = ref<any[]>([])
const couponTemplates = ref<any[]>([])
const dlg = ref(false)
const taskDlg = ref(false)
const tasks = ref<any[]>([])
const taskStats = ref<Record<string, number> | null>(null)
const taskCampaignId = ref<number | null>(null)
const form = reactive<any>({
  id: null,
  code: '',
  name: '',
  campaign_type: 'MEMBER_DAY',
  segment_id: null,
  coupon_template_id: null,
  boost_product_ids_text: '',
  remark: '',
})

function goSegments() {
  router.push('/marketing/segments')
}
function goReport() {
  router.push('/marketing/report')
}
function goCoupons() {
  router.push('/coupons')
}

function segmentLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const s = segments.value.find((x) => x.id === id)
  return s ? `${s.name} (#${id})` : `#${id}`
}

function couponLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const t = couponTemplates.value.find((x) => x.id === id)
  return t ? `${t.name} (#${id})` : `#${id}`
}

function parseBoostIds(text: string): number[] | null {
  const raw = (text || '').trim()
  if (!raw) return null
  const ids = raw
    .split(/[,，\s]+/)
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0)
  return ids.length ? ids : null
}

function formatBoostIds(ids: unknown): string {
  if (!Array.isArray(ids) || !ids.length) return ''
  return ids.join(',')
}

async function load() {
  loading.value = true
  try {
    const [c, s, t] = await Promise.all([
      api.get('/marketing/campaigns', { params: { page_size: 50 } }),
      api.get('/marketing/segments', { params: { page_size: 100 } }),
      api.get('/coupons/templates', { params: { include_disabled: false } }),
    ])
    items.value = c.data?.items || []
    segments.value = s.data?.items || []
    const raw = t.data?.items || t.data || []
    couponTemplates.value = Array.isArray(raw) ? raw : []
  } finally {
    loading.value = false
  }
}

function openEdit(row?: any) {
  if (row) {
    Object.assign(form, {
      id: row.id,
      code: row.code,
      name: row.name,
      campaign_type: row.campaign_type,
      segment_id: row.segment_id,
      coupon_template_id: row.coupon_template_id,
      boost_product_ids_text: formatBoostIds(row.boost_product_ids),
      remark: row.remark || '',
    })
  } else {
    Object.assign(form, {
      id: null,
      code: '',
      name: '',
      campaign_type: 'MEMBER_DAY',
      segment_id: null,
      coupon_template_id: null,
      boost_product_ids_text: '',
      remark: '',
    })
  }
  dlg.value = true
}

async function save() {
  if (!form.code?.trim() || !form.name?.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  saving.value = true
  try {
    const body = {
      code: form.code,
      name: form.name,
      campaign_type: form.campaign_type,
      segment_id: form.segment_id,
      coupon_template_id: form.coupon_template_id,
      boost_product_ids: parseBoostIds(form.boost_product_ids_text),
      remark: form.remark || null,
    }
    if (form.id) await api.put(`/marketing/campaigns/${form.id}`, body)
    else await api.post('/marketing/campaigns', body)
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function publish(row: any) {
  await ElMessageBox.confirm(
    `发布活动「${row.name}」并按分群生成发券任务？请确认分群已物化、券模板可用。`,
    '确认发布',
  )
  const res = await api.post(`/marketing/campaigns/${row.id}/publish`)
  ElMessage.success(`已入队 ${res.data?.enqueued ?? 0} 条任务`)
  await load()
}

async function setStatus(row: any, status: string) {
  await api.post(`/marketing/campaigns/${row.id}/status`, { status })
  ElMessage.success(`已切换为 ${status}`)
  await load()
}

async function showTasks(row: any) {
  taskCampaignId.value = row.id
  const res = await api.get(`/marketing/campaigns/${row.id}/tasks`, { params: { page_size: 50 } })
  tasks.value = res.data?.items || []
  taskStats.value = res.data?.task_stats || null
  taskDlg.value = true
}

async function retryFailed() {
  if (!taskCampaignId.value) return
  const res = await api.post(`/marketing/campaigns/${taskCampaignId.value}/tasks/retry-failed`)
  ElMessage.success(`已重置 ${res.data?.reset ?? 0} 条`)
  await showTasks({ id: taskCampaignId.value })
}

onMounted(load)
</script>

<style scoped>
.page { padding: 8px; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 640px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
.hint { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
</style>
