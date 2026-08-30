<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="用户名/姓名" clearable style="width:180px" @keyup.enter="reload" />
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="全部门店"
        style="width:160px"
        @change="reload"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-button type="primary" @click="reload">搜索</el-button>
      <el-button type="primary" @click="openForm()">新增员工</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="users" border style="margin-top:12px">
      <el-table-column prop="username" label="用户名" width="120" />
      <el-table-column prop="display_name" label="姓名" width="120" />
      <el-table-column prop="role_name" label="角色" width="120" />
      <el-table-column prop="store_name" label="门店" min-width="140">
        <template #default="{ row }">{{ row.store_name || (row.role_code === 'SUPER_ADMIN' ? '全店' : '—') }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_login_at" label="最近登录" width="170">
        <template #default="{ row }">{{ formatDateTime(row.last_login_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="warning" @click="openReset(row)">重置密码</el-button>
          <el-button
            link
            :type="row.status === 1 ? 'danger' : 'success'"
            @click="toggleStatus(row)"
          >{{ row.status === 1 ? '禁用' : '启用' }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top:12px"
      @current-change="load"
    />

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑员工' : '新增员工'" width="460px">
      <el-form :model="form" label-width="90px">
        <el-form-item v-if="!form.id" label="用户名" required>
          <el-input v-model="form.username" autocomplete="off" />
        </el-form-item>
        <el-form-item v-if="!form.id" label="密码" required>
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="form.display_name" />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-select v-model="form.role_id" style="width:100%">
            <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="门店">
          <el-select
            v-model="form.store_id"
            clearable
            :disabled="!auth.isSuperAdmin"
            placeholder="选择门店"
            style="width:100%"
          >
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetVisible" title="重置密码" width="400px">
      <el-form label-width="80px">
        <el-form-item label="新密码">
          <el-input v-model="resetPassword" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doReset">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const users = ref<any[]>([])
const roles = ref<any[]>([])
const stores = ref<any[]>([])
const keyword = ref('')
const filterStoreId = ref<number | undefined>()
const page = ref(1)
const pageSize = 20
const total = ref(0)
const dialogVisible = ref(false)
const resetVisible = ref(false)
const resetPassword = ref('')
const resetUserId = ref(0)
const form = reactive<{
  id: number
  username: string
  password: string
  display_name: string
  role_id?: number
  store_id?: number
}>({
  id: 0,
  username: '',
  password: '',
  display_name: '',
  role_id: undefined,
  store_id: undefined,
})

function formatDateTime(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

function reload() {
  page.value = 1
  load()
}

async function loadMeta() {
  const [r, s] = await Promise.all([
    api.get('/users/roles'),
    api.get('/settings/stores/options'),
  ])
  roles.value = r.data || []
  stores.value = s.data.items || []
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/users', {
      params: {
        page: page.value,
        page_size: pageSize,
        keyword: keyword.value || undefined,
        store_id: auth.isSuperAdmin ? filterStoreId.value || undefined : undefined,
      },
    })
    users.value = res.data.items || []
    total.value = res.data.total || 0
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function openForm(row?: any) {
  if (row) {
    Object.assign(form, {
      id: row.id,
      username: row.username,
      password: '',
      display_name: row.display_name,
      role_id: row.role_id,
      store_id: row.store_id || undefined,
    })
  } else {
    Object.assign(form, {
      id: 0,
      username: '',
      password: '',
      display_name: '',
      role_id: roles.value.find((r) => r.code === 'CASHIER')?.id || roles.value[0]?.id,
      store_id: auth.user?.store_id || stores.value[0]?.id,
    })
  }
  dialogVisible.value = true
}

async function save() {
  if (!form.display_name.trim() || !form.role_id) {
    ElMessage.warning('请填写姓名和角色')
    return
  }
  if (!form.id) {
    if (!form.username.trim() || !form.password) {
      ElMessage.warning('请填写用户名和密码')
      return
    }
    if (form.username.trim().length < 3) {
      ElMessage.warning('用户名至少 3 个字符')
      return
    }
    if (form.password.length < 6) {
      ElMessage.warning('密码至少 6 位')
      return
    }
    const role = roles.value.find((r) => r.id === form.role_id)
    if (auth.isSuperAdmin && role?.code !== 'SUPER_ADMIN' && !form.store_id) {
      ElMessage.warning('非超管账号必须选择门店')
      return
    }
  }
  saving.value = true
  try {
    if (form.id) {
      await api.put(`/users/${form.id}`, {
        display_name: form.display_name,
        role_id: form.role_id,
        store_id: form.store_id || undefined,
        clear_store: auth.isSuperAdmin && !form.store_id,
      })
      ElMessage.success('已更新')
    } else {
      await api.post('/users', {
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        role_id: form.role_id,
        store_id: form.store_id || undefined,
      })
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function openReset(row: any) {
  resetUserId.value = row.id
  resetPassword.value = ''
  resetVisible.value = true
}

async function doReset() {
  if (!resetPassword.value || resetPassword.value.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  saving.value = true
  try {
    await api.post(`/users/${resetUserId.value}/reset-password`, { password: resetPassword.value })
    ElMessage.success('密码已重置')
    resetVisible.value = false
  } catch (e: any) {
    ElMessage.error(e.message || '重置失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: any) {
  try {
    await api.put(`/users/${row.id}`, { status: row.status === 1 ? 0 : 1 })
    ElMessage.success('已更新')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
</style>
