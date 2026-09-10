<template>
  <div v-loading="loading" class="settings-page">
    <div class="page-header">
      <div>
        <h2>系统设置</h2>
        <p class="hint">分类业务参数可在线热更；.env 敏感项只读展示，修改后需重启后端</p>
      </div>
      <div class="header-actions">
        <el-tag v-if="overview" size="small" :type="overview.health?.redis ? 'success' : 'warning'">
          Redis {{ overview.health?.redis ? '正常' : '降级' }}
        </el-tag>
        <el-tag v-if="overview" size="small" type="info">缓存 TTL {{ overview.cache?.ttl_seconds ?? 15 }}s</el-tag>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="overview"
      type="info"
      show-icon
      :closable="false"
      class="env-tip"
      title="配置说明"
      :description="`环境 ${overview.app.env} · 进程角色 ${overview.app.process_role} · ${overview.cache?.multi_worker_note || ''} · 配置文件：${overview.env_file_hint}`"
    />

    <el-tabs v-if="overview" v-model="activeTab" class="settings-tabs" @tab-change="onTabChange">
      <el-tab-pane label="运行概览" name="overview">
        <el-row :gutter="16">
          <el-col :xs="24" :md="8">
            <el-card shadow="never">
              <template #header>
                <div class="card-head">
                  <span>服务状态</span>
                  <el-button link type="primary" size="small" @click="refreshHealth">检测</el-button>
                </div>
              </template>
              <div class="health-row">
                <span>数据库</span>
                <el-tag :type="overview.health.database ? 'success' : 'danger'" size="small">
                  {{ overview.health.database ? '正常' : '异常' }}
                </el-tag>
              </div>
              <div class="health-row">
                <span>Redis</span>
                <el-tag :type="overview.health.redis ? 'success' : 'warning'" size="small">
                  {{ overview.health.redis ? '正常' : '未连接' }}
                </el-tag>
              </div>
              <div class="health-row">
                <span>设置缓存版本</span>
                <code>{{ overview.health.settings_cache_ver ?? 0 }}</code>
              </div>
              <div class="health-row">
                <span>环境</span>
                <el-tag :type="overview.app.is_production ? 'danger' : 'success'" size="small">
                  {{ overview.app.is_production ? '生产' : '开发' }}
                </el-tag>
              </div>
            </el-card>
          </el-col>
          <el-col :xs="24" :md="16">
            <el-card shadow="never">
              <template #header>运维命令</template>
              <el-table :data="overview.scripts" size="small" border>
                <el-table-column prop="name" label="用途" width="140" />
                <el-table-column prop="command" label="命令" min-width="280" show-overflow-tooltip />
                <el-table-column prop="note" label="备注" width="160" show-overflow-tooltip />
                <el-table-column label="" width="80">
                  <template #default="{ row }">
                    <el-button link type="primary" size="small" @click="copyText(row.command)">复制</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>

        <el-card shadow="never" style="margin-top:16px">
          <template #header>
            <div class="card-head">
              <div>
                <span>门店信息</span>
                <span class="sub">（可编辑联系信息；新建门店后请为员工绑定 store_id）</span>
              </div>
              <el-button type="primary" size="small" @click="openCreateStore">新建门店</el-button>
            </div>
          </template>
          <el-table :data="overview.stores" border size="small">
            <el-table-column prop="store_no" label="编号" width="100" />
            <el-table-column label="名称" min-width="140">
              <template #default="{ row }">
                <el-input
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].name"
                  size="small"
                  maxlength="100"
                  show-word-limit
                />
                <span v-else>{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="地址" min-width="180">
              <template #default="{ row }">
                <el-input
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].address"
                  size="small"
                  maxlength="255"
                />
                <span v-else>{{ row.address || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="电话" width="140">
              <template #default="{ row }">
                <el-input
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].phone"
                  size="small"
                  maxlength="20"
                />
                <span v-else>{{ row.phone || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="纬度" width="110">
              <template #default="{ row }">
                <el-input
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].latitude"
                  size="small"
                  placeholder="如 31.23"
                />
                <span v-else>{{ row.latitude ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="经度" width="110">
              <template #default="{ row }">
                <el-input
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].longitude"
                  size="small"
                  placeholder="如 121.47"
                />
                <span v-else>{{ row.longitude ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-switch
                  v-if="storeEdits[row.id]"
                  v-model="storeEdits[row.id].status"
                  :active-value="1"
                  :inactive-value="0"
                />
                <el-tag v-else :type="row.status === 1 ? 'success' : 'info'" size="small">
                  {{ row.status === 1 ? '营业' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <template v-if="storeEdits[row.id]">
                  <el-button link type="primary" :loading="storeSaving === row.id" @click="saveStore(row.id)">保存</el-button>
                  <el-button link @click="cancelStoreEdit(row)">取消</el-button>
                </template>
                <el-button v-else link type="primary" @click="startStoreEdit(row)">编辑</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-dialog v-model="createStoreVisible" title="新建门店" width="420px">
          <el-form :model="createStoreForm" label-width="80px">
            <el-form-item label="编号" required>
              <el-input v-model="createStoreForm.store_no" placeholder="如 SNH002" maxlength="40" />
            </el-form-item>
            <el-form-item label="名称" required>
              <el-input v-model="createStoreForm.name" placeholder="门店名称" maxlength="100" />
            </el-form-item>
            <el-form-item label="地址">
              <el-input v-model="createStoreForm.address" maxlength="255" />
            </el-form-item>
            <el-form-item label="电话">
              <el-input v-model="createStoreForm.phone" maxlength="20" />
            </el-form-item>
            <el-form-item label="纬度">
              <el-input v-model="createStoreForm.latitude" placeholder="可选，天气口味用" />
            </el-form-item>
            <el-form-item label="经度">
              <el-input v-model="createStoreForm.longitude" placeholder="可选，天气口味用" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="createStoreVisible = false">取消</el-button>
            <el-button type="primary" :loading="creatingStore" @click="createStore">创建</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- 业务参数：按分类分页 -->
      <el-tab-pane
        v-for="g in businessGroups"
        :key="g.id"
        :name="`biz-${g.id}`"
      >
        <template #label>
          <span class="tab-label">
            {{ g.label }}
            <el-badge v-if="dirtyCountByGroup[g.id]" :value="dirtyCountByGroup[g.id]" class="tab-badge" />
          </span>
        </template>

        <el-alert
          type="success"
          show-icon
          :closable="false"
          :title="g.label"
          :description="`${g.description || ''} 保存在 system_settings，保存后约 15 秒内多 worker 同步。`"
          class="group-alert"
        />

        <el-form label-width="180px" class="business-form" @submit.prevent>
          <el-form-item v-for="item in itemsOfGroup(g.id)" :key="item.key" :label="item.label">
            <el-switch v-if="item.type === 'bool'" v-model="item.boolValue" />
            <el-select
              v-else-if="item.type === 'enum'"
              v-model="item.strValue"
              style="width: 280px"
            >
              <el-option
                v-for="c in item.choices || []"
                :key="c"
                :label="enumLabel(item.key, c)"
                :value="c"
              />
            </el-select>
            <el-input
              v-else-if="item.type === 'string' && isLongText(item)"
              v-model="item.strValue"
              type="textarea"
              :rows="3"
              :maxlength="item.max_length || 2000"
              show-word-limit
              style="max-width: 520px"
            />
            <el-input
              v-else-if="item.type === 'string'"
              v-model="item.strValue"
              :maxlength="item.max_length || 256"
              show-word-limit
              style="max-width: 420px"
              clearable
            />
            <el-input-number
              v-else-if="item.type === 'int'"
              v-model="item.numValue"
              :min="item.min"
              :max="item.max"
              :step="1"
              :controls="true"
            />
            <el-input-number
              v-else-if="item.type === 'float'"
              v-model="item.numValue"
              :min="item.min"
              :max="item.max"
              :step="item.step || 0.01"
              :precision="2"
            />
            <span v-if="item.unit" class="unit">{{ item.unit }}</span>
            <el-tag v-if="isDirty(item)" size="small" type="warning" style="margin-left:8px">已改</el-tag>
            <p class="field-desc">
              {{ item.description }}
              <template v-if="item.type === 'int' || item.type === 'float'">
                · 范围 {{ item.min }} ~ {{ item.max }}
              </template>
              <template v-else-if="item.type === 'enum'">
                · 可选 {{ (item.choices || []).join(' / ') }}
              </template>
            </p>
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              :loading="businessSaving"
              :disabled="!canSaveGroup(g.id)"
              @click="saveBusinessGroup(g.id)"
            >
              保存「{{ g.label }}」
            </el-button>
            <el-button :disabled="!dirtyCountByGroup[g.id]" @click="resetBusinessGroup(g.id)">
              还原本页未保存修改
            </el-button>
            <span v-if="dirtyCountByGroup[g.id]" class="dirty-hint">本页 {{ dirtyCountByGroup[g.id] }} 项已改</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="环境变量" name="env">
        <el-alert
          type="warning"
          show-icon
          :closable="false"
          title="只读"
          description="以下为进程内 .env / 启动配置脱敏展示。标注「需要重启」的项改文件后须重启后端；标注「—」的多为业务参数种子，正式以左侧分类页为准。"
          class="group-alert"
        />
        <div class="env-toolbar">
          <el-input
            v-model="envKeyword"
            clearable
            placeholder="搜索配置项 / 说明"
            style="width: 280px"
          />
          <el-select v-model="envGroupFilter" clearable placeholder="分组" style="width: 200px">
            <el-option v-for="g in overview.env_groups" :key="g.title" :label="g.title" :value="g.title" />
          </el-select>
        </div>
        <div v-for="group in filteredEnvGroups" :key="group.title" class="env-group">
          <h4>{{ group.title }}</h4>
          <p v-if="group.description" class="group-desc">{{ group.description }}</p>
          <el-table :data="group.items" border size="small">
            <el-table-column prop="key" label="配置项" width="240">
              <template #default="{ row }">
                <code>{{ row.key }}</code>
              </template>
            </el-table-column>
            <el-table-column label="当前值" min-width="200">
              <template #default="{ row }">
                <span :class="{ muted: !row.configured }">{{ row.value || '(未配置)' }}</span>
                <el-tag v-if="row.sensitive" size="small" type="warning" style="margin-left:6px">敏感</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="row.configured ? 'success' : 'info'" size="small">
                  {{ row.configured ? '已配置' : '未配置' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="260" show-overflow-tooltip />
            <el-table-column label="重启" width="80">
              <template #default="{ row }">
                {{ row.restart_required ? '需要' : '—' }}
              </template>
            </el-table-column>
          </el-table>
        </div>
        <el-empty v-if="!filteredEnvGroups.length" description="无匹配配置项" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const loading = ref(false)
const activeTab = ref('overview')
const overview = ref<any>(null)
const businessForm = ref<any[]>([])
const businessGroups = ref<any[]>([])
const businessSaving = ref(false)
const storeEdits = reactive<
  Record<number, { name: string; address: string; phone: string; latitude: string; longitude: string; status: number }>
>({})
const storeSaving = ref<number | null>(null)
const createStoreVisible = ref(false)
const creatingStore = ref(false)
const createStoreForm = reactive({
  store_no: '',
  name: '',
  address: '',
  phone: '',
  latitude: '',
  longitude: '',
})
const envKeyword = ref('')
const envGroupFilter = ref('')

const ENUM_LABELS: Record<string, Record<string, string>> = {
  kitchen_ticket_order_no: {
    barcode: '条码+单号',
    text: '仅单号',
    off: '不打印',
  },
  printer_mode: {
    local: '局域网 ESC/POS',
    xpyun: '芯烨云',
  },
  mini_program_env_version: {
    auto: '自动（开发→develop / 生产→release）',
    develop: 'develop 开发版',
    trial: 'trial 体验版',
    release: 'release 正式版',
  },
}

function enumLabel(key: string, value: string) {
  return ENUM_LABELS[key]?.[value] || value
}

function isLongText(item: any) {
  const max = Number(item.max_length || 0)
  return max >= 500 || ['webhook_ip_allowlist', 'trusted_proxy_ips'].includes(item.key)
}

function itemsOfGroup(groupId: string) {
  return businessForm.value.filter((i) => i.group === groupId)
}

function isDirty(item: any) {
  if (item.type === 'bool') return item.boolValue !== item._savedBool
  if (item.type === 'string' || item.type === 'enum') return item.strValue !== item._savedStr
  return item.numValue !== item._saved
}

const dirtyCountByGroup = computed(() => {
  const map: Record<string, number> = {}
  for (const item of businessForm.value) {
    if (!isDirty(item)) continue
    map[item.group] = (map[item.group] || 0) + 1
  }
  return map
})

function canSaveGroup(groupId: string) {
  const items = itemsOfGroup(groupId)
  if (!items.length) return false
  if (!items.some((i) => isDirty(i))) return false
  return items.every((i) => {
    if (i.type === 'bool' || i.type === 'string' || i.type === 'enum') return true
    return Number.isFinite(i.numValue)
  })
}

const filteredEnvGroups = computed(() => {
  const groups = overview.value?.env_groups || []
  const kw = envKeyword.value.trim().toLowerCase()
  return groups
    .filter((g: any) => !envGroupFilter.value || g.title === envGroupFilter.value)
    .map((g: any) => {
      const items = (g.items || []).filter((it: any) => {
        if (!kw) return true
        return (
          String(it.key || '')
            .toLowerCase()
            .includes(kw) ||
          String(it.description || '')
            .toLowerCase()
            .includes(kw) ||
          String(it.value || '')
            .toLowerCase()
            .includes(kw)
        )
      })
      return { ...g, items }
    })
    .filter((g: any) => g.items.length > 0)
})

function parseNum(item: any) {
  const n = item.type === 'float' ? parseFloat(item.value) : parseInt(item.value, 10)
  if (!Number.isFinite(n)) {
    return item.type === 'float' ? item.min ?? 0 : item.min ?? 0
  }
  return n
}

function parseBool(item: any) {
  if (typeof item.bool_value === 'boolean') return item.bool_value
  const s = String(item.value ?? '').trim().toLowerCase()
  return ['1', 'true', 'yes', 'on', 'y'].includes(s)
}

function buildBusinessForm(items: any[]) {
  businessForm.value = items.map((item) => {
    if (item.type === 'bool') {
      const boolValue = parseBool(item)
      return { ...item, boolValue, _savedBool: boolValue }
    }
    if (item.type === 'string' || item.type === 'enum') {
      const strValue = item.value == null ? '' : String(item.value)
      return { ...item, strValue, _savedStr: strValue }
    }
    const numValue = parseNum(item)
    return {
      ...item,
      numValue,
      _saved: numValue,
    }
  })
}

function applyBusinessPayload(data: any) {
  buildBusinessForm(data.business || [])
  businessGroups.value = data.business_groups || []
  if (!businessGroups.value.length && data.business_group_defs) {
    businessGroups.value = (data.business_group_defs || []).map((g: any) => ({
      ...g,
      items: [],
    }))
  }
}

function resetBusinessGroup(groupId: string) {
  for (const item of itemsOfGroup(groupId)) {
    if (item.type === 'bool') item.boolValue = item._savedBool
    else if (item.type === 'string' || item.type === 'enum') item.strValue = item._savedStr
    else item.numValue = item._saved
  }
}

function payloadOfItem(item: any): string | number | boolean {
  if (item.type === 'bool') return item.boolValue
  if (item.type === 'string' || item.type === 'enum') return item.strValue
  return item.numValue
}

async function saveBusinessGroup(groupId: string) {
  const dirty = itemsOfGroup(groupId).filter((i) => isDirty(i))
  if (!dirty.length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  for (const item of dirty) {
    if (item.type === 'bool' || item.type === 'string' || item.type === 'enum') continue
    if (!Number.isFinite(item.numValue)) {
      ElMessage.warning(`${item.label} 数值无效`)
      return
    }
    if (item.numValue < item.min || item.numValue > item.max) {
      ElMessage.warning(`${item.label} 须在 ${item.min}~${item.max}`)
      return
    }
  }
  businessSaving.value = true
  try {
    const settings: Record<string, string | number | boolean> = {}
    for (const item of dirty) {
      settings[item.key] = payloadOfItem(item)
    }
    const res = await api.put('/settings/business', { settings })
    applyBusinessPayload(res.data)
    ElMessage.success(`已保存 ${dirty.length} 项`)
    refreshHealth(false)
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    businessSaving.value = false
  }
}

function onTabChange() {
  // 预留：切换分类时无需额外请求
}

async function load() {
  loading.value = true
  try {
    overview.value = (await api.get('/settings')).data
    applyBusinessPayload(overview.value)
  } catch (e: any) {
    ElMessage.error(e.message || '加载设置失败')
  } finally {
    loading.value = false
  }
}

async function refreshHealth(showToast = true) {
  try {
    const res = await api.get('/settings/health')
    if (overview.value) overview.value.health = res.data
    if (showToast) ElMessage.success('健康检查已更新')
  } catch (e: any) {
    if (showToast) ElMessage.error(e.message || '检测失败')
  }
}

function startStoreEdit(row: any) {
  storeEdits[row.id] = {
    name: row.name,
    address: row.address || '',
    phone: row.phone || '',
    latitude: row.latitude != null ? String(row.latitude) : '',
    longitude: row.longitude != null ? String(row.longitude) : '',
    status: row.status,
  }
}

function cancelStoreEdit(row: any) {
  delete storeEdits[row.id]
}

async function saveStore(id: number) {
  const edit = storeEdits[id]
  if (!edit?.name?.trim()) {
    ElMessage.warning('门店名称不能为空')
    return
  }
  storeSaving.value = id
  try {
    const latRaw = String(edit.latitude || '').trim()
    const lngRaw = String(edit.longitude || '').trim()
    await api.put(`/settings/stores/${id}`, {
      name: edit.name.trim(),
      address: edit.address?.trim() || null,
      phone: edit.phone?.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
      status: edit.status,
    })
    ElMessage.success('门店已更新')
    delete storeEdits[id]
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    storeSaving.value = null
  }
}

function openCreateStore() {
  Object.assign(createStoreForm, {
    store_no: '',
    name: '',
    address: '',
    phone: '',
    latitude: '',
    longitude: '',
  })
  createStoreVisible.value = true
}

async function createStore() {
  if (!createStoreForm.store_no.trim() || !createStoreForm.name.trim()) {
    ElMessage.warning('请填写门店编号和名称')
    return
  }
  creatingStore.value = true
  try {
    const latRaw = String(createStoreForm.latitude || '').trim()
    const lngRaw = String(createStoreForm.longitude || '').trim()
    await api.post('/settings/stores', {
      store_no: createStoreForm.store_no.trim(),
      name: createStoreForm.name.trim(),
      address: createStoreForm.address.trim() || null,
      phone: createStoreForm.phone.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
    })
    ElMessage.success('门店已创建')
    createStoreVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  } finally {
    creatingStore.value = false
  }
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.info(text)
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page { min-height: 360px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; gap: 12px; }
.page-header h2 { margin: 0; font-size: 18px; }
.hint { margin: 4px 0 0; color: #909399; font-size: 12px; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.env-tip { margin-bottom: 16px; }
.group-alert { margin-bottom: 16px; }
.settings-tabs { margin-top: 8px; }
.tab-label { display: inline-flex; align-items: center; gap: 4px; }
.tab-badge { margin-left: 2px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.health-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.health-row:last-child { border-bottom: none; }
.sub { font-size: 12px; color: #999; font-weight: normal; margin-left: 8px; }
.business-form { max-width: 780px; margin-top: 8px; }
.field-desc { margin: 4px 0 0; font-size: 12px; color: #999; }
.unit { margin-left: 8px; color: #666; font-size: 13px; }
.dirty-hint { margin-left: 12px; color: #e6a23c; font-size: 12px; }
.group-desc { color: #666; margin: 0 0 12px; font-size: 13px; }
.env-toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.env-group { margin-bottom: 24px; }
.env-group h4 { margin: 0 0 8px; }
.muted { color: #999; }
code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
</style>
