<template>
  <div v-loading="loading" class="role-perm-page">
    <div class="page-header">
      <div>
        <h2>角色权限</h2>
        <p class="hint">勾选后点「保存本列」生效。员工账号在「员工管理」里绑定角色；改权限后该角色员工需重新登录。</p>
      </div>
      <div class="header-actions">
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-for="(h, i) in hints"
      :key="i"
      type="info"
      show-icon
      :closable="false"
      class="tip"
      :title="h"
    />

    <div v-if="roles.length" class="matrix-wrap">
      <el-table :data="tableRows" border table-layout="fixed" class="matrix-table">
        <el-table-column label="模块" width="88" fixed>
          <template #default="{ row }">
            <span v-if="row._moduleFirst" class="mod-label">{{ row.moduleLabel }}</span>
          </template>
        </el-table-column>
        <el-table-column label="权限" min-width="160" fixed>
          <template #default="{ row }">
            <div class="perm-name">{{ row.name }}</div>
            <div class="muted">{{ row.code }}</div>
          </template>
        </el-table-column>
        <el-table-column
          v-for="role in roles"
          :key="role.id"
          :label="role.name"
          min-width="120"
          align="center"
        >
          <template #header>
            <div class="role-head">
              <div>{{ role.name }}</div>
              <div class="muted">{{ role.code }}</div>
              <el-button
                v-if="role.editable"
                type="primary"
                size="small"
                link
                :loading="savingRoleId === role.id"
                :disabled="!isDirty(role.id)"
                @click="saveRole(role)"
              >
                保存本列
              </el-button>
              <el-tag v-else size="small" type="warning">固定全部</el-tag>
            </div>
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="checked(role.id, row.code)"
              :disabled="!role.editable || row.code === 'system:manage'"
              @change="(v: boolean | string | number) => toggle(role.id, row.code, !!v)"
            />
          </template>
        </el-table-column>
      </el-table>
    </div>

    <p v-else-if="!loading" class="muted">暂无角色数据，请先执行数据库种子。</p>

    <el-divider />
    <p class="hint">
      给人配角色请到「门店 → 员工管理」。本页只改「角色拥有哪些权限」，不改员工账号本身。
    </p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'

type RoleCol = {
  id: number
  code: string
  name: string
  description?: string
  editable: boolean
  permission_codes: string[]
}

type PermRow = {
  id: number
  code: string
  name: string
  module: string
  moduleLabel: string
  _moduleFirst: boolean
}

const loading = ref(false)
const savingRoleId = ref<number | null>(null)
const roles = ref<RoleCol[]>([])
const tableRows = ref<PermRow[]>([])
const hints = ref<string[]>([])
/** roleId -> Set(permission codes) 当前编辑态 */
const draft = reactive<Record<number, Set<string>>>({})
const saved = reactive<Record<number, Set<string>>>({})

function checked(roleId: number, code: string) {
  return draft[roleId]?.has(code) ?? false
}

function toggle(roleId: number, code: string, on: boolean) {
  const set = draft[roleId]
  if (!set) return
  if (on) set.add(code)
  else set.delete(code)
}

function isDirty(roleId: number) {
  const a = draft[roleId]
  const b = saved[roleId]
  if (!a || !b) return false
  if (a.size !== b.size) return true
  for (const c of a) if (!b.has(c)) return true
  return false
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/users/role-permissions-matrix')
    const data = res.data || {}
    hints.value = data.hints || []
    roles.value = data.roles || []
    const rows: PermRow[] = []
    for (const mod of data.modules || []) {
      const items = mod.permissions || []
      items.forEach((p: any, idx: number) => {
        rows.push({
          id: p.id,
          code: p.code,
          name: p.name,
          module: mod.module,
          moduleLabel: mod.label || mod.module,
          _moduleFirst: idx === 0,
        })
      })
    }
    tableRows.value = rows

    for (const key of Object.keys(draft)) delete draft[Number(key)]
    for (const key of Object.keys(saved)) delete saved[Number(key)]
    for (const r of roles.value) {
      draft[r.id] = new Set(r.permission_codes || [])
      saved[r.id] = new Set(r.permission_codes || [])
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function saveRole(role: RoleCol) {
  if (!role.editable || !isDirty(role.id)) return
  const codes = [...(draft[role.id] || [])]
  try {
    await ElMessageBox.confirm(
      `确认保存角色「${role.name}」的权限？该角色下员工将被踢下线并需重新登录。`,
      '保存权限',
      { type: 'warning', confirmButtonText: '保存', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  savingRoleId.value = role.id
  try {
    const res = await api.put(`/users/roles/${role.id}/permissions`, { permission_codes: codes })
    const next = new Set<string>(res.data?.permission_codes || codes)
    draft[role.id] = new Set(next)
    saved[role.id] = new Set(next)
    const n = res.data?.affected_users ?? 0
    ElMessage.success(`已保存「${role.name}」权限${n ? `，已使 ${n} 名员工重新登录` : ''}`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.message || e?.message || '保存失败')
  } finally {
    savingRoleId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.role-perm-page { max-width: 100%; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.hint { margin: 0; color: #909399; font-size: 13px; line-height: 1.5; }
.tip { margin-bottom: 8px; }
.matrix-wrap { overflow-x: auto; margin-top: 12px; }
.mod-label { font-weight: 600; color: #303133; }
.perm-name { font-size: 13px; }
.muted { color: #909399; font-size: 12px; }
.role-head { display: flex; flex-direction: column; align-items: center; gap: 2px; line-height: 1.3; }
</style>
