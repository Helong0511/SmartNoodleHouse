<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select v-if="auth.isSuperAdmin" v-model="filterStoreId" clearable placeholder="全部门店" style="width: 180px"
        @change="onStoreFilterChange">
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-input
        v-model="keyword"
        placeholder="编号 / 分类名"
        clearable
        style="width: 180px"
        @keyup.enter="reload"
        @clear="reload"
      />
      <el-select v-model="filterStatus" clearable placeholder="全部状态" style="width: 120px" @change="reload">
        <el-option label="启用" :value="1" />
        <el-option label="禁用" :value="0" />
      </el-select>
      <el-button v-if="canCreateProduct" type="primary" @click="openForm()">新增分类</el-button>
      <el-button v-if="auth.isSuperAdmin && canCreateProduct" type="success" plain :disabled="!selectedIds.length"
        @click="openCopy">复制到门店</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="pagedCategories" style="margin-top:12px" border row-key="id" @selection-change="onSelectionChange">
      <el-table-column v-if="auth.isSuperAdmin" type="selection" width="48" />
      <el-table-column v-if="auth.isSuperAdmin" prop="store_name" label="门店" min-width="80">
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column prop="category_no" label="编号" min-width="90" show-overflow-tooltip />
      <el-table-column prop="name" label="分类名" />
      <el-table-column prop="description" label="描述" show-overflow-tooltip />
      <el-table-column label="须先点分类" min-width="160">
        <template #default="{ row }">
          <span v-if="!(row.require_category_ids || []).length" class="muted">无限制</span>
          <span v-else>{{ formatRequireNames(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="canUpdateProduct" label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="danger" @click="toggleStatus(row)">
            {{ row.status === 1 ? '禁用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="pager.page"
        v-model:page-size="pager.pageSize"
        :total="filteredCategories.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="onPageChange"
        @size-change="reload"
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑分类' : '新增分类'" width="520px">
      <el-form :model="form" label-width="100px">
        <el-form-item v-if="!form.id && auth.isSuperAdmin" label="门店" required>
          <el-select v-model="form.store_id" placeholder="选择门店" style="width: 100%" @change="onStoreChange">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else-if="!form.id" label="门店">
          <span>{{ boundStoreName || '当前绑定门店' }}</span>
        </el-form-item>
        <el-form-item v-else label="门店">
          <span>{{ form.store_name || boundStoreName || `门店#${form.store_id}` }}</span>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" @input="onNameInput" />
        </el-form-item>
        <el-form-item label="编号" required>
          <el-input v-model="form.category_no" :disabled="!!form.id" :placeholder="categoryNoPlaceholder"
            @input="codeTouched = true" />
          <div class="form-tip">{{ categoryNoTip }}</div>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" />
        </el-form-item>
        <el-form-item label="须先点分类">
          <el-select v-model="form.require_category_ids" multiple clearable filterable style="width: 100%"
            placeholder="不选=无限制；例：加料依赖「面」">
            <el-option v-for="c in requireOptions" :key="c.id" :label="c.name" :value="c.id"
              :disabled="form.id === c.id" />
          </el-select>
          <div class="form-tip">新建餐品时默认带入本配置，可在餐品编辑里改成指定分类或指定餐品</div>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <CopyToStoreDialog v-model="copyVisible" :stores="stores" :exclude-store-id="copySourceStoreId"
      :selected-count="selectedIds.length" :category-ids="selectedIds" @done="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import CopyToStoreDialog from '@/components/CopyToStoreDialog.vue'
import { codeHint, storePrefixFromNo, suggestCodeFromName } from '@/utils/bizCode'

const auth = useAuthStore()
const canCreateProduct = computed(
  () => auth.isSuperAdmin || auth.hasPermission('product:create'),
)
const canUpdateProduct = computed(
  () => auth.isSuperAdmin || auth.hasPermission('product:update'),
)
const loading = ref(false)
const saving = ref(false)
const categories = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const filterStoreId = ref<number | undefined>()
const keyword = ref('')
const filterStatus = ref<number | undefined>()
const pager = reactive({ page: 1, pageSize: 20 })
const dialogVisible = ref(false)
const copyVisible = ref(false)
const selectedRows = ref<any[]>([])
const codeTouched = ref(false)
const form = reactive({
  id: 0,
  store_id: undefined as number | undefined,
  store_name: '',
  category_no: '',
  name: '',
  description: '',
  sort_order: 0,
  status: 1,
  require_category_ids: [] as number[],
})

function formStorePrefix() {
  const sid = form.store_id || auth.user?.store_id
  const s = stores.value.find((x) => Number(x.id) === Number(sid))
  return storePrefixFromNo(s?.store_no)
}

const categoryNoPlaceholder = computed(() => `逻辑码如 Mian（保存为 ${formStorePrefix()}-Mian）`)
const categoryNoTip = computed(() =>
  form.id
    ? '编号创建后不可改'
    : `${codeHint('category', formStorePrefix())}；输入名称后自动建议拼音编码，可改。`,
)

function onNameInput() {
  if (form.id || codeTouched.value) return
  suggestCodeFromName(
    { name: form.name, kind: 'category', store_id: form.store_id || auth.user?.store_id },
    (code) => {
      if (!codeTouched.value) form.category_no = code
    },
  )
}

function onStoreChange() {
  if (!form.id && form.name && !codeTouched.value) onNameInput()
}

const selectedIds = computed(() => selectedRows.value.map((r) => r.id))
const copySourceStoreId = computed(() => {
  const storesSet = new Set(selectedRows.value.map((r) => Number(r.store_id)))
  return storesSet.size === 1 ? [...storesSet][0] : null
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const filteredCategories = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return categories.value.filter((c) => {
    if (filterStatus.value !== undefined && filterStatus.value !== null && Number(c.status) !== Number(filterStatus.value)) {
      return false
    }
    if (!kw) return true
    const no = String(c.category_no || '').toLowerCase()
    const name = String(c.name || '').toLowerCase()
    const desc = String(c.description || '').toLowerCase()
    return no.includes(kw) || name.includes(kw) || desc.includes(kw)
  })
})

const pagedCategories = computed(() => {
  const start = (pager.page - 1) * pager.pageSize
  return filteredCategories.value.slice(start, start + pager.pageSize)
})

const requireOptions = computed(() => {
  const sid = form.store_id || (form.id ? categories.value.find((c) => c.id === form.id)?.store_id : undefined)
  if (!sid) return categories.value.filter((c) => c.status === 1 || c.id === form.id)
  return categories.value.filter((c) => c.store_id === sid)
})

function formatRequireNames(row: any) {
  const ids = row.require_category_ids || []
  if (!ids.length) return '无限制'
  const names = ids.map((id: number) => {
    const c = categories.value.find((x) => x.id === id)
    return c?.name || `#${id}`
  })
  return names.join('、')
}

function onSelectionChange(rows: any[]) {
  selectedRows.value = rows
}

function onPageChange() {
  selectedRows.value = []
}

function reload() {
  pager.page = 1
  selectedRows.value = []
}

async function onStoreFilterChange() {
  reload()
  await load()
}

function openCopy() {
  if (!selectedIds.value.length) {
    ElMessage.warning('请先勾选分类')
    return
  }
  const storesSet = new Set(selectedRows.value.map((r) => Number(r.store_id)))
  if (storesSet.size > 1) {
    ElMessage.warning('请选择同一门店的分类再复制')
    return
  }
  copyVisible.value = true
}

async function load() {
  loading.value = true
  try {
    const sid = auth.isSuperAdmin
      ? filterStoreId.value
      : auth.user?.store_id || undefined
    const res = await api.get('/categories', {
      params: { all: true, ...(sid ? { store_id: sid } : {}) },
    })
    categories.value = res.data || []
    selectedRows.value = []
  } finally {
    loading.value = false
  }
}

function openForm(row?: any) {
  codeTouched.value = !!row
  if (row) {
    Object.assign(form, {
      id: row.id,
      store_id: row.store_id,
      store_name: row.store_name || '',
      category_no: row.category_no || '',
      name: row.name,
      description: row.description || '',
      sort_order: row.sort_order,
      status: row.status,
      require_category_ids: [...(row.require_category_ids || [])],
    })
  } else {
    Object.assign(form, {
      id: 0,
      store_id: auth.isSuperAdmin
        ? filterStoreId.value || stores.value[0]?.id
        : auth.user?.store_id || stores.value[0]?.id,
      store_name: '',
      category_no: '',
      name: '',
      description: '',
      sort_order: 0,
      status: 1,
      require_category_ids: [],
    })
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入分类名')
    return
  }
  if (!form.id && auth.isSuperAdmin && !form.store_id) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    const payload: any = {
      name: form.name,
      description: form.description || null,
      sort_order: form.sort_order,
      status: form.status,
      require_category_ids: form.require_category_ids || [],
    }
    if (!form.id) {
      if (!form.category_no.trim()) {
        ElMessage.warning('请填写分类编号（或先输入名称自动生成）')
        saving.value = false
        return
      }
      payload.store_id = form.store_id
      payload.category_no = form.category_no.trim()
      await api.post('/categories', payload)
    } else {
      await api.put(`/categories/${form.id}`, payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: any) {
  await api.put(`/categories/${row.id}`, { status: row.status === 1 ? 0 : 1 })
  ElMessage.success('已更新')
  load()
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
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.form-tip {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}

.muted {
  color: #c0c4cc;
}
</style>
