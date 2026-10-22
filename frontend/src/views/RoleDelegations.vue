<template>
  <div v-loading="loading">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="tip"
      title="临时授权在生效期内并入被委托人权限；到期自动失效。非超管仅可委派本店员工。"
    />

    <div class="toolbar">
      <el-button type="primary" @click="openCreate">新建授权</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="rows" border>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="委托人" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ userLabel(row.grantor_user_id) }}</template>
      </el-table-column>
      <el-table-column label="被委托人" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ userLabel(row.delegatee_user_id) }}</template>
      </el-table-column>
      <el-table-column label="权限" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ (row.permission_codes || []).join(', ') }}</template>
      </el-table-column>
      <el-table-column label="开始" width="160">
        <template #default="{ row }">{{ formatDateTime(row.starts_at) }}</template>
      </el-table-column>
      <el-table-column label="结束" width="160">
        <template #default="{ row }">{{ formatDateTime(row.ends_at) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="120" show-overflow-tooltip />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'ACTIVE' && canRevoke(row)"
            type="danger"
            link
            @click="revoke(row)"
          >
            撤销
          </el-button>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新建临时授权" width="520px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item label="被委托人" required>
          <el-select
            v-model="form.delegatee_user_id"
            filterable
            placeholder="选择员工"
            style="width:100%"
          >
            <el-option
              v-for="u in delegateeOptions"
              :key="u.id"
              :label="userOptionLabel(u)"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="权限码" required>
          <el-select
            v-model="form.permission_codes"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入权限码"
            style="width:100%"
          >
            <el-option v-for="code in grantableCodes" :key="code" :label="code" :value="code" />
          </el-select>
        </el-form-item>
        <el-form-item label="生效时间" required>
          <el-date-picker
            v-model="form.starts_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="开始"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="结束时间" required>
          <el-date-picker
            v-model="form.ends_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="结束"
            style="width:100%"
          />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="form.reason" maxlength="255" show-word-limit placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
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
const rows = ref<any[]>([])
const users = ref<any[]>([])
const catalogCodes = ref<string[]>([])
const dialogVisible = ref(false)

const form = reactive({
  delegatee_user_id: null as number | null,
  permission_codes: [] as string[],
  starts_at: '',
  ends_at: '',
  reason: '',
})

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: '生效中',
  EXPIRED: '已过期',
  REVOKED: '已撤销',
}

const userMap = computed(() => {
  const m = new Map<number, any>()
  for (const u of users.value) m.set(u.id, u)
  return m
})

const grantableCodes = computed(() => {
  const mine = new Set((auth.permissions || []).map(String))
  if (auth.isSuperAdmin) {
    const all = new Set([...catalogCodes.value, ...mine])
    return Array.from(all).sort()
  }
  return Array.from(mine).sort()
})

const delegateeOptions = computed(() =>
  users.value.filter((u) => u.id !== auth.user?.id && Number(u.status) === 1),
)

function statusLabel(s: string) {
  return STATUS_LABEL[s] || s || '—'
}

function statusType(s: string) {
  if (s === 'ACTIVE') return 'success'
  if (s === 'REVOKED') return 'info'
  if (s === 'EXPIRED') return 'warning'
  return ''
}

function formatDateTime(iso?: string | null) {
  if (!iso) return '—'
  return String(iso).replace('T', ' ').slice(0, 19)
}

function userLabel(id: number) {
  const u = userMap.value.get(id)
  if (!u) return `用户#${id}`
  return u.display_name || u.username || `用户#${id}`
}

function userOptionLabel(u: any) {
  const name = u.display_name || u.username
  return `${name}（${u.username}）`
}

function canRevoke(row: any) {
  return auth.isSuperAdmin || row.grantor_user_id === auth.user?.id
}

function defaultRange() {
  const now = new Date()
  const end = new Date(now.getTime() + 24 * 3600 * 1000)
  const fmt = (d: Date) => {
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  form.starts_at = fmt(now)
  form.ends_at = fmt(end)
}

async function loadUsers() {
  try {
    const res = await api.get('/users', { params: { page: 1, page_size: 200 } })
    users.value = res.data?.items || res.data || []
  } catch {
    users.value = []
  }
}

async function loadCatalogCodes() {
  if (!auth.hasPermission('system:manage') && !auth.isSuperAdmin) return
  try {
    const res = await api.get('/users/role-permissions-matrix')
    const perms = res.data?.permissions || []
    catalogCodes.value = perms.map((p: any) => String(p.code)).filter(Boolean).sort()
  } catch {
    catalogCodes.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/users/delegations')
    rows.value = res.data?.items || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载临时授权失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.delegatee_user_id = null
  form.permission_codes = []
  form.reason = ''
  defaultRange()
  dialogVisible.value = true
}

async function submitCreate() {
  if (!form.delegatee_user_id) {
    ElMessage.warning('请选择被委托人')
    return
  }
  if (!form.permission_codes.length) {
    ElMessage.warning('请至少选择一个权限码')
    return
  }
  if (!form.starts_at || !form.ends_at) {
    ElMessage.warning('请填写起止时间')
    return
  }
  saving.value = true
  try {
    await api.post('/users/delegations', {
      delegatee_user_id: form.delegatee_user_id,
      permission_codes: form.permission_codes,
      starts_at: form.starts_at,
      ends_at: form.ends_at,
      reason: form.reason || null,
    })
    ElMessage.success('已创建临时授权')
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  } finally {
    saving.value = false
  }
}

async function revoke(row: any) {
  try {
    await ElMessageBox.confirm(`确认撤销对「${userLabel(row.delegatee_user_id)}」的临时授权？`, '撤销授权', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await api.post(`/users/delegations/${row.id}/revoke`)
    ElMessage.success('已撤销')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '撤销失败')
  }
}

onMounted(async () => {
  await Promise.all([loadUsers(), loadCatalogCodes()])
  await load()
})
</script>

<style scoped>
.tip {
  margin-bottom: 12px;
}
.toolbar {
  margin-bottom: 12px;
}
.muted {
  color: #909399;
}
</style>
