<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-button v-if="canCreateProduct" type="primary" @click="openGroupForm()">新增规格组</el-button>
      <el-button v-if="isSuper && canCreateProduct" type="success" plain :disabled="!selectedIds.length" @click="openCopy">复制到门店</el-button>
      <el-button @click="load">刷新</el-button>
      <el-select v-if="isSuper" v-model="filterStoreId" clearable placeholder="门店筛选（空=全部可见）"
        style="width:220px;margin-left:12px" @change="onStoreFilterChange">
        <el-option label="仅总部共享" :value="0" />
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="组名 / 编码"
        clearable
        style="width:180px;margin-left:12px"
        @keyup.enter="resetPage"
        @clear="resetPage"
      />
      <el-checkbox v-model="showDisabled" style="margin-left:12px" @change="onStoreFilterChange">显示已禁用</el-checkbox>
    </div>
    <p class="hint">编码规则：总部 <code>0000-Liang</code>；分店 <code>SH01-Liang</code>；选项
      <code>SH01-Liang-XiaoWan</code>。可填逻辑码（如 Liang），保存时自动加前缀。复制到门店会按目标店 store_no 重写编码。
    </p>

    <el-table :key="tableKey" :data="pagedGroups" style="margin-top:12px" border row-key="id"
      @selection-change="onSelectionChange">
      <el-table-column v-if="isSuper" type="selection" width="48" />
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="opts-panel">
            <el-button
              v-if="canCreateProduct"
              size="small"
              type="primary"
              :disabled="!canEditGroup(row)"
              @click="openOptionForm(row)"
            >新增选项</el-button>
            <el-table :data="row.options" size="small" border style="margin-top:8px">
              <el-table-column prop="name" label="选项名" width="180" />
              <el-table-column prop="code" label="编码" width="250" />
              <el-table-column prop="extra_price" label="加价" width="90">
                <template #default="{ row: o }">¥{{ o.extra_price }}</template>
              </el-table-column>
              <el-table-column prop="sort_order" label="排序" width="70" />
              <el-table-column label="默认" width="70">
                <template #default="{ row: o }">
                  <el-tag v-if="isDefaultOption(o)" type="warning" size="small">默认</el-tag>
                  <span v-else class="muted">-</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80">
                <template #default="{ row: o }">
                  <el-tag :type="o.status === 1 ? 'success' : 'info'" size="small">{{ o.status === 1 ? '启用' : '禁用'
                  }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="320">
                <template #default="{ row: o }">
                  <el-button link type="primary" :disabled="!canEditGroup(row)"
                    @click="openOptionForm(row, o)">编辑</el-button>
                  <el-button v-if="row.selection_type === 'SINGLE' && !isDefaultOption(o) && o.status === 1" link
                    type="warning" :disabled="!canEditGroup(row)" @click="setDefaultOption(o)">设为默认</el-button>
                  <el-button link type="danger" :disabled="!canEditGroup(row)" @click="removeOption(o)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="规格组" width="160" />
      <el-table-column prop="code" label="编码" width="160" />
      <el-table-column label="归属" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.store_id == null" type="success" size="small">总部共享</el-tag>
          <span v-else>{{ storeName(row.store_id) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="90">
        <template #default="{ row }">{{ row.selection_type === 'MULTIPLE' ? '多选' : '单选' }}</template>
      </el-table-column>
      <el-table-column label="必选" width="70">
        <template #default="{ row }">{{ row.is_required ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="70" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">{{ row.status === 1 ? '启用' : '禁用'
          }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button
            v-if="canUpdateProduct"
            link
            type="primary"
            :disabled="!canEditGroup(row)"
            @click="openGroupForm(row)"
          >编辑</el-button>
          <el-button
            v-if="canDeleteProduct"
            link
            type="danger"
            :disabled="!canEditGroup(row)"
            @click="removeGroup(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="pager.page"
        v-model:page-size="pager.pageSize"
        :total="filteredGroups.length"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="onPageChange"
        @size-change="resetPage"
      />
    </div>

    <el-dialog v-model="groupDialog" :title="groupForm.id ? '编辑规格组' : '新增规格组'" width="480px">
      <el-form :model="groupForm" label-width="90px">
        <el-form-item v-if="isSuper && !groupForm.id" label="归属">
          <el-select v-model="groupForm.store_id" clearable placeholder="空=总部共享" style="width:100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="groupForm.name" @input="onGroupNameInput" />
        </el-form-item>
        <el-form-item label="编码" required>
          <el-input v-model="groupForm.code" :disabled="!!groupForm.id" :placeholder="groupCodePlaceholder"
            @input="groupCodeTouched = true" />
          <div class="form-tip">{{ groupCodeTip }}</div>
        </el-form-item>
        <el-form-item label="选择类型">
          <el-radio-group v-model="groupForm.selection_type">
            <el-radio value="SINGLE">单选</el-radio>
            <el-radio value="MULTIPLE">多选</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="必选"><el-switch v-model="groupForm.is_required" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="groupForm.sort_order" :min="0" /></el-form-item>
        <el-form-item v-if="groupForm.id" label="状态">
          <el-switch v-model="groupForm.status" :active-value="1" :inactive-value="0" active-text="启用"
            inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="groupDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveGroup">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="optionDialog" :title="optionForm.id ? '编辑选项' : '新增选项'" width="440px">
      <el-form :model="optionForm" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="optionForm.name" @input="onOptionNameInput" />
        </el-form-item>
        <el-form-item label="编码" required>
          <el-input v-model="optionForm.code" :disabled="!!optionForm.id" :placeholder="optionCodePlaceholder"
            @input="optionCodeTouched = true" />
          <div class="form-tip">可只填 XiaoWan，保存为 前缀-组逻辑-XiaoWan；输入名称后自动建议拼音</div>
        </el-form-item>
        <el-form-item label="加价"><el-input-number v-model="optionForm.extra_price" :min="0" :precision="2"
            :step="0.5" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="optionForm.sort_order" :min="0" /></el-form-item>
        <el-form-item v-if="optionGroupType === 'SINGLE'" label="默认项">
          <el-switch v-model="optionForm.is_default" active-text="设为默认" inactive-text="否" />
          <div class="form-tip">菜单页点「+」将按默认规格加购</div>
        </el-form-item>
        <el-form-item v-if="optionForm.id" label="状态">
          <el-switch v-model="optionForm.status" :active-value="1" :inactive-value="0" active-text="启用"
            inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="optionDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveOption">保存</el-button>
      </template>
    </el-dialog>

    <CopyToStoreDialog v-model="copyVisible" :stores="stores" :exclude-store-id="copySourceStoreId"
      :selected-count="selectedIds.length" :option-group-ids="selectedIds" clone-shared-option-groups
      hint="复制后编码按目标店编号重写：0000-Liang → SH01-Liang，选项同步为 SH01-Liang-XiaoWan。" @done="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import CopyToStoreDialog from '@/components/CopyToStoreDialog.vue'
import { suggestCodeFromName } from '@/utils/bizCode'

const auth = useAuthStore()
const isSuper = computed(() => !!auth.isSuperAdmin)
const boundStoreId = computed(() => auth.user?.store_id || null)
const canCreateProduct = computed(
  () => auth.isSuperAdmin || auth.hasPermission('product:create'),
)
const canUpdateProduct = computed(
  () => auth.isSuperAdmin || auth.hasPermission('product:update'),
)
const canDeleteProduct = computed(
  () => auth.isSuperAdmin || auth.hasPermission('product:delete'),
)

const loading = ref(false)
const saving = ref(false)
const groups = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const filterStoreId = ref<number | null>(null)
const keyword = ref('')
const pager = reactive({ page: 1, pageSize: 20 })
const tableKey = ref(0)
const showDisabled = ref(false)
const groupDialog = ref(false)
const optionDialog = ref(false)
const optionGroupType = ref('SINGLE')
const copyVisible = ref(false)
const selectedRows = ref<any[]>([])
const editingGroupCode = ref('')
const groupCodeTouched = ref(false)
const optionCodeTouched = ref(false)
const editingOptionGroupId = ref(0)

const selectedIds = computed(() => selectedRows.value.map((r) => r.id))
const copySourceStoreId = computed(() => {
  const privateRows = selectedRows.value.filter((r) => r.store_id != null)
  if (!privateRows.length) return null
  const storesSet = new Set(privateRows.map((r) => Number(r.store_id)))
  return storesSet.size === 1 ? [...storesSet][0] : null
})

const filteredGroups = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return groups.value
  return groups.value.filter((g) => {
    const name = String(g.name || '').toLowerCase()
    const code = String(g.code || '').toLowerCase()
    if (name.includes(kw) || code.includes(kw)) return true
    const opts = Array.isArray(g.options) ? g.options : []
    return opts.some((o: any) => {
      const on = String(o.name || '').toLowerCase()
      const oc = String(o.code || '').toLowerCase()
      return on.includes(kw) || oc.includes(kw)
    })
  })
})

const pagedGroups = computed(() => {
  const start = (pager.page - 1) * pager.pageSize
  return filteredGroups.value.slice(start, start + pager.pageSize)
})

function resetPage() {
  pager.page = 1
  selectedRows.value = []
}

function onPageChange() {
  selectedRows.value = []
}

async function onStoreFilterChange() {
  resetPage()
  await load()
}

function storePrefix(storeId: number | null | undefined) {
  if (storeId == null) return '0000'
  const s = stores.value.find((x) => Number(x.id) === Number(storeId))
  return (s?.store_no || `S${storeId}`).toString()
}

const groupCodePlaceholder = computed(() => {
  const p = storePrefix(groupForm.store_id)
  return `逻辑码如 Liang（保存为 ${p}-Liang）`
})

const groupCodeTip = computed(() => {
  const p = storePrefix(groupForm.store_id)
  return groupForm.id
    ? '编码创建后不可改'
    : `归属为空=总部前缀 0000；选门店则用该店编号。例：${p}-Liang；输入名称后自动建议拼音`
})

const optionCodePlaceholder = computed(() => {
  const g = editingGroupCode.value || 'Liang'
  const stem = g.includes('-') ? g.split('-').slice(1).join('-') : g
  const p = g.includes('-') ? g.split('-')[0] : '0000'
  return `如 XiaoWan → ${p}-${stem}-XiaoWan`
})

function onGroupNameInput() {
  if (groupForm.id || groupCodeTouched.value) return
  suggestCodeFromName(
    { name: groupForm.name, kind: 'group', store_id: groupForm.store_id },
    (code) => {
      if (!groupCodeTouched.value) groupForm.code = code
    },
  )
}

function onOptionNameInput() {
  if (optionForm.id || optionCodeTouched.value || !editingOptionGroupId.value) return
  suggestCodeFromName(
    { name: optionForm.name, kind: 'option', group_id: editingOptionGroupId.value },
    (code) => {
      if (!optionCodeTouched.value) optionForm.code = code
    },
  )
}
function onSelectionChange(rows: any[]) {
  selectedRows.value = rows
}

function openCopy() {
  if (!selectedIds.value.length) {
    ElMessage.warning('请先勾选规格组')
    return
  }
  const privateRows = selectedRows.value.filter((r) => r.store_id != null)
  const storesSet = new Set(privateRows.map((r) => Number(r.store_id)))
  if (storesSet.size > 1) {
    ElMessage.warning('请不要同时勾选多个门店的私有规格组')
    return
  }
  copyVisible.value = true
}

const groupForm = reactive({
  id: 0,
  store_id: null as number | null,
  name: '',
  code: '',
  selection_type: 'SINGLE',
  is_required: true,
  sort_order: 0,
  status: 1,
})

const optionForm = reactive({
  id: 0,
  group_id: 0,
  name: '',
  code: '',
  extra_price: 0,
  sort_order: 0,
  is_default: false,
  status: 1,
})

function isDefaultOption(o: any) {
  return o?.is_default === true || o?.is_default === 1 || o?.is_default === '1'
}

function storeName(sid: number) {
  const s = stores.value.find((x) => x.id === sid)
  return s ? s.name : `门店#${sid}`
}

function canEditGroup(row: any) {
  if (!canUpdateProduct.value && !canDeleteProduct.value) return false
  if (isSuper.value) return true
  if (row.store_id == null) return false
  return boundStoreId.value != null && Number(row.store_id) === Number(boundStoreId.value)
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {}
    if (showDisabled.value) params.include_disabled = true
    if (isSuper.value && filterStoreId.value === 0) {
      // 后端无「仅总部」过滤：前端筛选
    } else if (isSuper.value && filterStoreId.value) {
      params.store_id = filterStoreId.value
    }
    let rows = (await api.get('/options/groups', { params })).data || []
    if (isSuper.value && filterStoreId.value === 0) {
      rows = rows.filter((g: any) => g.store_id == null)
    }
    groups.value = rows
    selectedRows.value = []
    tableKey.value += 1
  } finally {
    loading.value = false
  }
}

function openGroupForm(row?: any) {
  groupCodeTouched.value = !!row
  if (row) {
    Object.assign(groupForm, {
      id: row.id,
      store_id: row.store_id ?? null,
      name: row.name,
      code: row.code,
      selection_type: row.selection_type,
      is_required: row.is_required,
      sort_order: row.sort_order,
      status: row.status ?? 1,
    })
  } else {
    Object.assign(groupForm, {
      id: 0,
      store_id: isSuper.value ? null : boundStoreId.value,
      name: '',
      code: '',
      selection_type: 'SINGLE',
      is_required: true,
      sort_order: 0,
      status: 1,
    })
  }
  groupDialog.value = true
}

async function saveGroup() {
  if (!groupForm.name || !groupForm.code) {
    ElMessage.warning('请填写名称和编码')
    return
  }
  saving.value = true
  try {
    const payload: Record<string, any> = {
      name: groupForm.name,
      code: groupForm.code,
      selection_type: groupForm.selection_type,
      is_required: groupForm.is_required,
      sort_order: groupForm.sort_order,
    }
    if (!groupForm.id && isSuper.value) {
      payload.store_id = groupForm.store_id
    }
    if (groupForm.id) {
      await api.put(`/options/groups/${groupForm.id}`, { ...payload, status: groupForm.status })
    } else {
      await api.post('/options/groups', payload)
    }
    ElMessage.success('保存成功')
    groupDialog.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeGroup(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除规格组「${row.name}」？将禁用该组及下属选项（历史订单数据保留，默认从列表隐藏）。`,
      '确认删除',
      { type: 'warning' },
    )
    await api.delete(`/options/groups/${row.id}`)
    ElMessage.success('已禁用并从列表移除')
    load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(e.message || '删除失败')
    }
  }
}

function openOptionForm(group: any, opt?: any) {
  optionGroupType.value = group.selection_type || 'SINGLE'
  editingGroupCode.value = group.code || ''
  editingOptionGroupId.value = group.id
  optionCodeTouched.value = !!opt
  if (opt) {
    Object.assign(optionForm, {
      id: opt.id,
      group_id: group.id,
      name: opt.name,
      code: opt.code,
      extra_price: parseFloat(opt.extra_price),
      sort_order: opt.sort_order,
      is_default: isDefaultOption(opt),
      status: opt.status ?? 1,
    })
  } else {
    Object.assign(optionForm, {
      id: 0,
      group_id: group.id,
      name: '',
      code: '',
      extra_price: 0,
      sort_order: 0,
      is_default: false,
      status: 1,
    })
  }
  optionDialog.value = true
}

async function saveOption() {
  if (!optionForm.name || !optionForm.code) {
    ElMessage.warning('请填写名称和编码')
    return
  }
  saving.value = true
  try {
    const payload = {
      group_id: optionForm.group_id,
      name: optionForm.name,
      code: optionForm.code,
      extra_price: optionForm.extra_price,
      sort_order: optionForm.sort_order,
      is_default: optionGroupType.value === 'SINGLE' ? !!optionForm.is_default : false,
    }
    let saved: any
    if (optionForm.id) {
      saved = (await api.put(`/options/${optionForm.id}`, { ...payload, status: optionForm.status })).data
    } else {
      saved = (await api.post('/options', payload)).data
    }
    if (payload.is_default && saved && !isDefaultOption(saved)) {
      ElMessage.warning('默认项未生效：请确认已执行 sql/tools/009_option_is_default.sql 并重启后端')
    } else {
      ElMessage.success(payload.is_default ? '已保存并设为默认' : '保存成功')
    }
    optionDialog.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function setDefaultOption(opt: any) {
  saving.value = true
  try {
    await api.put(`/options/${opt.id}/default`)
    ElMessage.success(`已将「${opt.name}」设为默认`)
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '设置默认失败')
  } finally {
    saving.value = false
  }
}

async function removeOption(opt: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除选项「${opt.name}」？将禁用该选项（默认从列表隐藏）。`,
      '确认删除',
      { type: 'warning' },
    )
    await api.delete(`/options/${opt.id}`)
    ElMessage.success('已禁用并从列表移除')
    load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(e.message || '删除失败')
    }
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: #909399;
}

.opts-panel {
  padding: 8px 16px 16px 48px;
}

.muted {
  color: #999;
  font-size: 12px;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
