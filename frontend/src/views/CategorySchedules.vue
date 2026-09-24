<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="全部门店"
        style="width: 180px"
        @change="onStoreFilter"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-button type="primary" :disabled="!effectiveStoreId" @click="openForm()">新增方案</el-button>
      <el-button
        type="success"
        plain
        :disabled="!effectiveStoreId"
        :loading="autoRefreshing"
        @click="autoRefreshStore"
      >
        按销量刷新自动方案
      </el-button>
      <el-button @click="load">刷新</el-button>
      <el-time-select
        v-model="previewAt"
        start="00:00"
        step="00:30"
        end="23:30"
        placeholder="预览时刻"
        style="width: 140px; margin-left: 8px"
        :disabled="!effectiveStoreId"
      />
      <el-button :disabled="!effectiveStoreId || !previewAt" @click="loadPreview">预览排序</el-button>
    </div>
    <p class="hint">
      按门店本地时分命中时段后，覆盖分类默认排序；重叠取优先级更大者；开始晚于结束表示跨午夜。无命中则用分类管理里的默认排序。
      开启「销量自动」后，关店夜间批会按该时段历史销量刷新分类序；销量越多权重越高，排序越准。也可点「按销量刷新」立即更新。
    </p>

    <el-table :data="schedules" style="margin-top: 12px" border>
      <el-table-column prop="name" label="方案名" min-width="120" />
      <el-table-column label="时段" width="140">
        <template #default="{ row }">{{ row.start_time }} – {{ row.end_time }}</template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="80" />
      <el-table-column label="销量自动" width="100">
        <template #default="{ row }">
          <el-tag :type="row.auto_sort === 1 ? 'success' : 'info'" size="small">
            {{ row.auto_sort === 1 ? '开' : '关' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分类序" min-width="220">
        <template #default="{ row }">
          <span v-if="!(row.items || []).length" class="muted">未配置（等同默认）</span>
          <span v-else>{{ formatItems(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最近自动刷新" width="170">
        <template #default="{ row }">{{ row.auto_updated_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="success" :loading="autoRefreshingId === row.id" @click="autoRefreshOne(row)">
            按销量刷新
          </el-button>
          <el-button link type="warning" @click="toggleStatus(row)">
            {{ row.status === 1 ? '停用' : '启用' }}
          </el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="previewNames.length" class="preview">
      <div class="preview-title">预览（{{ previewAt }}）{{ previewScheduleName ? `· ${previewScheduleName}` : '· 默认排序' }}</div>
      <ol>
        <li v-for="(n, i) in previewNames" :key="i">{{ n }}</li>
      </ol>
    </div>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑时段方案' : '新增时段方案'" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item v-if="!form.id && auth.isSuperAdmin" label="门店" required>
          <el-select v-model="form.store_id" placeholder="选择门店" style="width: 100%" @change="onFormStoreChange">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="门店">
          <span>{{ formStoreLabel }}</span>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如 早餐优先" />
        </el-form-item>
        <el-form-item label="开始" required>
          <el-time-select v-model="form.start_time" start="00:00" step="00:15" end="23:45" placeholder="含" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束" required>
          <el-time-select v-model="form.end_time" start="00:00" step="00:15" end="23:45" placeholder="不含" style="width: 100%" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.priority" :min="0" :max="999" />
          <span class="form-tip">重叠时段取更大值</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="销量自动">
          <el-switch
            v-model="form.auto_sort"
            :active-value="1"
            :inactive-value="0"
            active-text="按销量自动刷新"
            inactive-text="仅手动"
          />
          <div class="form-tip" style="margin-left:0;margin-top:4px">
            开启后夜间批与「按销量刷新」会按该时段历史销量重排；样本越多越贴近真实热度
          </div>
        </el-form-item>
        <el-form-item label="分类排序">
          <div v-if="!formCategories.length" class="muted">请先选择门店并确保已有分类</div>
          <div v-else class="sort-list">
            <div v-for="(row, idx) in form.items" :key="row.category_id" class="sort-row">
              <span class="sort-idx">{{ idx + 1 }}</span>
              <span class="sort-name">{{ categoryName(row.category_id) }}</span>
              <el-button link :disabled="idx === 0" @click="moveItem(idx, -1)">上移</el-button>
              <el-button link :disabled="idx === form.items.length - 1" @click="moveItem(idx, 1)">下移</el-button>
            </div>
          </div>
          <div class="form-tip">未列入的分类会排在已配置项之后，仍按默认排序</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const autoRefreshing = ref(false)
const autoRefreshingId = ref<number | null>(null)
const schedules = ref<any[]>([])
const stores = ref<any[]>([])
const categories = ref<any[]>([])
const filterStoreId = ref<number | undefined>()
const previewAt = ref('')
const previewNames = ref<string[]>([])
const previewScheduleName = ref('')
const dialogVisible = ref(false)
const form = reactive({
  id: 0,
  store_id: undefined as number | undefined,
  name: '',
  start_time: '07:00',
  end_time: '10:30',
  priority: 0,
  status: 1,
  auto_sort: 1,
  items: [] as { category_id: number; sort_order: number }[],
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const effectiveStoreId = computed(() => {
  if (auth.isSuperAdmin) return filterStoreId.value
  return auth.user?.store_id || undefined
})

const formCategories = computed(() => {
  const sid = form.store_id || effectiveStoreId.value
  if (!sid) return []
  return categories.value
    .filter((c) => c.store_id === sid)
    .slice()
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id)
})

const formStoreLabel = computed(() => {
  const sid = form.store_id
  if (!sid) return boundStoreName.value || '当前门店'
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function categoryName(id: number) {
  return categories.value.find((c) => c.id === id)?.name || `#${id}`
}

function formatItems(row: any) {
  const items = [...(row.items || [])].sort(
    (a: any, b: any) => (a.sort_order || 0) - (b.sort_order || 0) || a.category_id - b.category_id,
  )
  return items.map((it: any) => categoryName(it.category_id)).join(' → ')
}

function buildDefaultItems(sid: number) {
  return categories.value
    .filter((c) => c.store_id === sid && c.status === 1)
    .slice()
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id)
    .map((c, i) => ({ category_id: c.id, sort_order: i }))
}

function moveItem(idx: number, delta: number) {
  const j = idx + delta
  if (j < 0 || j >= form.items.length) return
  const arr = form.items
  const tmp = arr[idx]
  arr[idx] = arr[j]
  arr[j] = tmp
  form.items = arr.map((it, i) => ({ ...it, sort_order: i }))
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data?.items || []
  } catch {
    stores.value = []
  }
}

async function loadCategories() {
  const sid = effectiveStoreId.value || (auth.isSuperAdmin ? undefined : auth.user?.store_id)
  const res = await api.get('/categories', {
    params: { all: true, ...(sid ? { store_id: sid } : {}) },
  })
  categories.value = res.data || []
}

async function load() {
  loading.value = true
  try {
    await loadCategories()
    const sid = effectiveStoreId.value
    const res = await api.get('/category-schedules', {
      params: sid ? { store_id: sid } : {},
    })
    schedules.value = res.data || []
  } finally {
    loading.value = false
  }
}

function onStoreFilter() {
  previewNames.value = []
  load()
}

async function loadPreview() {
  const sid = effectiveStoreId.value
  if (!sid || !previewAt.value) return
  const res = await api.get('/categories', {
    params: { store_id: sid, at: previewAt.value },
  })
  const list = res.data || []
  previewNames.value = list.map((c: any) => c.name)
  previewScheduleName.value = list[0]?.schedule_name || ''
}

function openForm(row?: any) {
  const sid = row?.store_id || effectiveStoreId.value || auth.user?.store_id
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return
  }
  form.id = row?.id || 0
  form.store_id = sid
  form.name = row?.name || ''
  form.start_time = (row?.start_time || '07:00').slice(0, 5)
  form.end_time = (row?.end_time || '10:30').slice(0, 5)
  form.priority = row?.priority ?? 0
  form.status = row?.status ?? 1
  form.auto_sort = row?.auto_sort ?? 1
  if (row?.items?.length) {
    const ordered = [...row.items].sort(
      (a: any, b: any) => (a.sort_order || 0) - (b.sort_order || 0) || a.category_id - b.category_id,
    )
    const known = new Set(ordered.map((x: any) => x.category_id))
    const rest = buildDefaultItems(sid).filter((x) => !known.has(x.category_id))
    form.items = [...ordered, ...rest].map((it, i) => ({ category_id: it.category_id, sort_order: i }))
  } else {
    form.items = buildDefaultItems(sid)
  }
  dialogVisible.value = true
}

function onFormStoreChange() {
  if (form.store_id) form.items = buildDefaultItems(form.store_id)
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写方案名称')
    return
  }
  if (!form.start_time || !form.end_time) {
    ElMessage.warning('请选择时段')
    return
  }
  const sid = form.store_id || effectiveStoreId.value
  if (!sid && !form.id) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      start_time: form.start_time,
      end_time: form.end_time,
      priority: form.priority,
      status: form.status,
      auto_sort: form.auto_sort,
      items: form.items.map((it, i) => ({ category_id: it.category_id, sort_order: i })),
      ...(form.id ? {} : { store_id: sid }),
    }
    if (form.id) {
      await api.put(`/category-schedules/${form.id}`, payload)
    } else {
      await api.post('/category-schedules', payload)
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: any) {
  await api.put(`/category-schedules/${row.id}`, { status: row.status === 1 ? 0 : 1 })
  ElMessage.success('已更新')
  await load()
}

async function autoRefreshOne(row: any) {
  autoRefreshingId.value = row.id
  try {
    const res: any = await api.post(`/category-schedules/${row.id}/auto-refresh`)
    const conf = res.data?.result?.confidence
    const qty = res.data?.result?.total_qty
    const pct = conf == null ? '' : `，置信 ${(Number(conf) * 100).toFixed(0)}%`
    ElMessage.success(`已按销量刷新（样本 ${qty ?? 0} 件${pct}）`)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '刷新失败')
  } finally {
    autoRefreshingId.value = null
  }
}

async function autoRefreshStore() {
  const sid = effectiveStoreId.value
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return
  }
  autoRefreshing.value = true
  try {
    const res: any = await api.post('/category-schedules/auto-refresh', null, {
      params: { store_id: sid, only_auto: true },
    })
    ElMessage.success(`已刷新 ${res.data?.updated ?? 0} 个自动方案`)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '刷新失败')
  } finally {
    autoRefreshing.value = false
  }
}

async function remove(row: any) {
  await ElMessageBox.confirm(`确定删除方案「${row.name}」？`, '删除确认', { type: 'warning' })
  await api.delete(`/category-schedules/${row.id}`)
  ElMessage.success('已删除')
  await load()
}

onMounted(async () => {
  await loadStores()
  if (!auth.isSuperAdmin && auth.user?.store_id) {
    filterStoreId.value = auth.user.store_id
  }
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.hint {
  margin: 10px 0 0;
  color: #909399;
  font-size: 13px;
  line-height: 1.5;
}
.muted { color: #909399; }
.form-tip {
  margin-left: 8px;
  color: #909399;
  font-size: 12px;
}
.sort-list {
  width: 100%;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  max-height: 280px;
  overflow: auto;
}
.sort-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid #f2f3f5;
}
.sort-row:last-child { border-bottom: none; }
.sort-idx {
  width: 22px;
  color: #909399;
  font-size: 12px;
}
.sort-name { flex: 1; }
.preview {
  margin-top: 16px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 6px;
}
.preview-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.preview ol {
  margin: 0;
  padding-left: 20px;
}
</style>
