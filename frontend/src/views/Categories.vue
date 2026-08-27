<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="全部门店"
        style="width: 180px"
        @change="load"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-button type="primary" @click="openForm()">新增分类</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="categories" style="margin-top:12px" border>
      <el-table-column v-if="auth.isSuperAdmin" prop="store_name" label="门店" min-width="120">
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column prop="name" label="分类名" />
      <el-table-column prop="description" label="描述" show-overflow-tooltip />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="danger" @click="toggleStatus(row)">
            {{ row.status === 1 ? '禁用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑分类' : '新增分类'" width="440px">
      <el-form :model="form" label-width="80px">
        <el-form-item v-if="!form.id && auth.isSuperAdmin" label="门店" required>
          <el-select v-model="form.store_id" placeholder="选择门店" style="width: 100%">
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
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" />
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const categories = ref<any[]>([])
const stores = ref<any[]>([])
const filterStoreId = ref<number | undefined>()
const dialogVisible = ref(false)
const form = reactive({
  id: 0,
  store_id: undefined as number | undefined,
  store_name: '',
  name: '',
  description: '',
  sort_order: 0,
  status: 1,
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
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
    const sid = auth.isSuperAdmin
      ? filterStoreId.value
      : auth.user?.store_id || undefined
    const res = await api.get('/categories', {
      params: { all: true, ...(sid ? { store_id: sid } : {}) },
    })
    categories.value = res.data || []
  } finally {
    loading.value = false
  }
}

function openForm(row?: any) {
  if (row) {
    Object.assign(form, {
      id: row.id,
      store_id: row.store_id,
      store_name: row.store_name || '',
      name: row.name,
      description: row.description || '',
      sort_order: row.sort_order,
      status: row.status,
    })
  } else {
    Object.assign(form, {
      id: 0,
      store_id: auth.isSuperAdmin
        ? filterStoreId.value || stores.value[0]?.id
        : auth.user?.store_id || stores.value[0]?.id,
      store_name: '',
      name: '',
      description: '',
      sort_order: 0,
      status: 1,
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
    }
    if (!form.id) {
      payload.store_id = form.store_id
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
</style>
