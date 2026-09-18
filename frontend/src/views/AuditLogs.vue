<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="reload"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">
        门店：{{ boundStoreName }}
      </el-tag>

      <el-form-item>
        <el-select
          v-model="categoryFilter"
          placeholder="分类"
          clearable
          style="width:140px"
          @change="reload"
        >
          <el-option
            v-for="c in categories"
            :key="c.value"
            :label="c.label"
            :value="c.value"
          />
        </el-select>
      </el-form-item>

      <el-form-item>
        <el-input
          v-model="moduleFilter"
          clearable
          placeholder="模块（如 order）"
          style="width:140px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>

      <el-form-item>
        <el-input
          v-model="actionFilter"
          clearable
          placeholder="操作 action"
          style="width:140px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>

      <el-form-item>
        <el-input-number
          v-model="userIdFilter"
          :min="1"
          :controls="false"
          placeholder="用户 ID"
          style="width:110px"
          @change="reload"
        />
      </el-form-item>

      <el-form-item>
        <el-input
          v-model="keyword"
          clearable
          placeholder="关键字（action/detail）"
          style="width:220px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>

      <el-form-item>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="起"
          end-placeholder="止"
          style="width:240px"
          @change="reload"
        />
      </el-form-item>

      <el-button type="primary" @click="reload">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </el-form>

    <el-row :gutter="12" class="kpi-row">
      <el-col :span="6">
        <el-card shadow="never" class="kpi-card">
          <div class="kpi-label">总日志数</div>
          <div class="kpi-value">{{ total }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="rows" border>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="用户" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.display_name || row.username || `用户#${row.user_id}` }}
          <div v-if="row.username && row.display_name" class="muted">{{ row.username }}</div>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="categoryTagType(row.category)">
            {{ categoryLabel(row.category) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="module" label="模块" width="120" show-overflow-tooltip />
      <el-table-column prop="action" label="操作" min-width="160" show-overflow-tooltip />
      <el-table-column label="门店" width="110">
        <template #default="{ row }">{{ storeName(row.store_id) }}</template>
      </el-table-column>
      <el-table-column label="目标" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.target_type">{{ row.target_type }}#{{ row.target_id }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column prop="ip" label="IP" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.ip || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="reload"
      />
    </div>

    <el-dialog v-model="detailDialog" title="操作日志详情" width="720px">
      <div v-if="currentRow" class="detail-box">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="时间">{{ formatDateTime(currentRow.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="分类">
            <el-tag size="small" :type="categoryTagType(currentRow.category)">
              {{ categoryLabel(currentRow.category) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="用户">
            {{ currentRow.display_name || currentRow.username || `用户#${currentRow.user_id}` }}
          </el-descriptions-item>
          <el-descriptions-item label="用户名">{{ currentRow.username || '—' }}</el-descriptions-item>
          <el-descriptions-item label="模块">{{ currentRow.module || '—' }}</el-descriptions-item>
          <el-descriptions-item label="操作">{{ currentRow.action || '—' }}</el-descriptions-item>
          <el-descriptions-item label="门店">{{ storeName(currentRow.store_id) }}</el-descriptions-item>
          <el-descriptions-item label="目标">
            <span v-if="currentRow.target_type">{{ currentRow.target_type }}#{{ currentRow.target_id }}</span>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="IP">{{ currentRow.ip || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Request ID">{{ currentRow.request_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="User Agent" :span="2">
            <span class="ua">{{ currentRow.user_agent || '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div class="detail-section">
          <div class="detail-section-title">Detail</div>
          <pre class="detail-pre">{{ formatDetail(currentRow.detail) }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import {
  listAuditLogs,
  listAuditCategories,
  type AuditLogItem,
  type AuditCategoryOption,
} from '@/api/auditLogs'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref<AuditLogItem[]>([])
const stores = ref<{ id: number; name: string }[]>([])
const categories = ref<AuditCategoryOption[]>([])
const boundStoreName = ref('')

const storeFilter = ref<number | null>(null)
const categoryFilter = ref('')
const moduleFilter = ref('')
const actionFilter = ref('')
const userIdFilter = ref<number | undefined>(undefined)
const keyword = ref('')
const dateRange = ref<string[] | null>(null)

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const detailDialog = ref(false)
const currentRow = ref<AuditLogItem | null>(null)

async function loadStores() {
  try {
    const res: any = await api.get('/settings/stores/options')
    stores.value = res.data?.items || []
    if (!auth.isSuperAdmin && auth.user?.store_id) {
      const s = stores.value.find((x) => x.id === auth.user?.store_id)
      boundStoreName.value = s?.name || `门店#${auth.user.store_id}`
    }
  } catch {
    stores.value = []
  }
}

async function loadCategories() {
  try {
    const res = await listAuditCategories()
    // 后端按 visible_values 已过滤，超管才含 DANGER/INTRUSION
    categories.value = res.data?.items || []
  } catch (e: any) {
    categories.value = []
  }
}

function categoryLabel(value: string) {
  return categories.value.find((c) => c.value === value)?.label || value || '—'
}

/** 分类对应标签颜色：危险/破解→红；系统→灰；顾客→绿；收银→黄；其他默认 */
function categoryTagType(value: string): '' | 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'DANGER' || value === 'INTRUSION') return 'danger'
  if (value === 'SYSTEM') return 'info'
  if (value === 'CUSTOMER') return 'success'
  if (value === 'CASHIER') return 'warning'
  return ''
}

function storeName(sid: number | null) {
  if (!sid) return '—'
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
}

function formatDateTime(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

function formatDetail(detail: unknown) {
  if (detail == null || detail === '') return '—'
  if (typeof detail === 'string') {
    try {
      return JSON.stringify(JSON.parse(detail), null, 2)
    } catch {
      return detail
    }
  }
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function resetFilters() {
  storeFilter.value = null
  categoryFilter.value = ''
  moduleFilter.value = ''
  actionFilter.value = ''
  userIdFilter.value = undefined
  keyword.value = ''
  dateRange.value = null
  reload()
}

function reload() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    if (categoryFilter.value) params.category = categoryFilter.value
    if (moduleFilter.value.trim()) params.module = moduleFilter.value.trim()
    if (actionFilter.value.trim()) params.action = actionFilter.value.trim()
    if (userIdFilter.value) params.user_id = userIdFilter.value
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    if (dateRange.value?.length === 2) {
      params.date_from = `${dateRange.value[0]}T00:00:00`
      params.date_to = `${dateRange.value[1]}T23:59:59`
    }
    const res = await listAuditLogs(params)
    const list = res.data?.items || []
    // 末页数据被清空后自动回退，避免空白页
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    rows.value = list
    total.value = Number(res.data?.total || 0)
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function openDetail(row: AuditLogItem) {
  currentRow.value = row
  detailDialog.value = true
}

onMounted(async () => {
  await Promise.all([loadStores(), loadCategories()])
  await load()
})
</script>

<style scoped>
.kpi-row { margin-bottom: 12px; }
.kpi-card { text-align: center; }
.kpi-label { font-size: 12px; color: #909399; }
.kpi-value { font-size: 24px; font-weight: 600; color: #303133; margin-top: 4px; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
.muted { color: #909399; font-size: 12px; }
.detail-box { padding: 4px 0; }
.detail-section { margin-top: 12px; }
.detail-section-title { font-weight: 600; margin-bottom: 6px; color: #303133; }
.detail-pre {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  max-height: 320px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
.ua { word-break: break-all; font-size: 12px; color: #606266; }
</style>
