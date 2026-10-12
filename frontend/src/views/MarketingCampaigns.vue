<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" @click="openEdit()">新建活动</el-button>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="items" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="code" label="编码" width="140" />
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="campaign_type" label="类型" width="110" />
      <el-table-column prop="status" label="状态" width="110" />
      <el-table-column prop="segment_id" label="分群" width="80" />
      <el-table-column prop="coupon_template_id" label="券模板" width="90" />
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

    <el-dialog v-model="dlg" :title="form.id ? '编辑活动' : '新建活动'" width="560px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="编码"><el-input v-model="form.code" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.campaign_type">
            <el-option label="会员日" value="MEMBER_DAY" />
            <el-option label="节日" value="FESTIVAL" />
            <el-option label="时段" value="TIME_SLOT" />
            <el-option label="公益" value="SOCIAL" />
          </el-select>
        </el-form-item>
        <el-form-item label="分群">
          <el-select v-model="form.segment_id" filterable clearable placeholder="选择分群">
            <el-option v-for="s in segments" :key="s.id" :label="`${s.name} (#${s.id})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="券模板 ID">
          <el-input-number v-model="form.coupon_template_id" :min="1" />
        </el-form-item>
        <el-form-item label="推荐加权商品">
          <el-input
            v-model="form.boost_product_ids_text"
            placeholder="商品 ID，逗号分隔，如 12,34"
          />
          <div class="hint">RUNNING 期间这些 SKU 在推荐列表加权约 +25%；活动结束自动失效</div>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="taskDlg" :title="`任务监控 #${taskCampaignId}`" width="720px">
      <p v-if="taskStats">
        汇总：
        <span v-for="(v, k) in taskStats" :key="k" style="margin-right: 12px">{{ k }}={{ v }}</span>
      </p>
      <el-button size="small" @click="retryFailed" style="margin-bottom: 8px">重试 FAILED</el-button>
      <el-table :data="tasks" size="small" stripe max-height="360">
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
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'

const loading = ref(false)
const saving = ref(false)
const items = ref<any[]>([])
const segments = ref<any[]>([])
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
    const [c, s] = await Promise.all([
      api.get('/marketing/campaigns', { params: { page_size: 50 } }),
      api.get('/marketing/segments', { params: { page_size: 100 } }),
    ])
    items.value = c.data?.items || []
    segments.value = s.data?.items || []
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
  await ElMessageBox.confirm(`发布活动「${row.name}」并生成发券任务？`, '确认发布')
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
.toolbar { margin-bottom: 12px; display: flex; gap: 8px; }
.hint { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
</style>
