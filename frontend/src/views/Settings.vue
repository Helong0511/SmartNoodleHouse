<template>
  <div v-loading="loading" class="settings-page">
    <div class="page-header">
      <div>
        <h2>系统设置</h2>
        <p class="hint">平台级安全、小程序主体、夜间批处理与 .env 运维项。门店业务参数请到「门店 → 门店信息 → 业务参数」</p>
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

    <el-tabs v-if="pageReady" v-model="activeTab" class="settings-tabs" @tab-change="onTabChange">
      <el-tab-pane v-if="canManagePlatform" label="运行概览" name="overview">
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

      </el-tab-pane>

      <el-tab-pane
        v-for="g in platformGroups"
        :key="`plat-${g.id}`"
        :name="`plat-${g.id}`"
      >
        <template #label>
          <span class="tab-label">平台·{{ g.label }}</span>
        </template>
        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="`平台 · ${g.label}`"
          description="以下项全部门店共用。点餐页开关、打印、叫号屏、新客赠券等请到「门店 → 门店信息 → 业务参数」按店配置。"
          class="group-alert"
        />
        <el-form label-width="180px" class="business-form" @submit.prevent>
          <el-form-item v-for="item in platformItemsOfGroup(g.id)" :key="item.key" :label="item.label">
            <el-switch v-if="item.type === 'bool'" v-model="item.boolValue" />
            <el-select v-else-if="item.type === 'enum'" v-model="item.strValue" style="width: 280px">
              <el-option v-for="c in item.choices || []" :key="c" :label="enumLabel(item.key, c)" :value="c" />
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
            <p class="field-desc">{{ item.description }}</p>
          </el-form-item>
          <el-form-item v-if="platformItemsOfGroup(g.id).length">
            <el-button
              type="primary"
              :loading="businessSaving"
              :disabled="!canSavePlatformGroup(g.id)"
              @click="savePlatformGroup(g.id)"
            >
              {{ `保存平台「${g.label}」` }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane v-if="canManagePlatform" label="环境变量" name="env">
        <el-alert
          type="warning"
          show-icon
          :closable="false"
          title="只读"
          description="以下为进程内 .env / 启动配置脱敏展示。标注「需要重启」的项改文件后须重启后端；门店业务项以「门店 → 门店信息 → 业务参数」为准，平台项以本页平台分类为准。"
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

      <el-tab-pane v-if="canManagePlatform" label="定时任务" name="scheduler">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          class="group-alert"
          :title="schedulerSummary"
          :description="schedulerJobs?.note || '定时任务仅在 Leader 进程执行。'"
        />
        <div class="env-toolbar">
          <el-input
            v-model="schedulerKeyword"
            clearable
            placeholder="搜索任务 / 分类 / 说明"
            style="width: 280px"
          />
          <el-select v-model="schedulerCategory" clearable placeholder="分类" style="width: 160px">
            <el-option v-for="c in schedulerCategories" :key="c" :label="c" :value="c" />
          </el-select>
          <el-button :loading="schedulerLoading" @click="loadSchedulerJobs">刷新</el-button>
        </div>
        <el-table v-loading="schedulerLoading" :data="filteredSchedulerJobs" border size="small">
          <el-table-column prop="category" label="分类" width="90" />
          <el-table-column prop="title" label="任务" min-width="110" />
          <el-table-column label="启用" width="90" align="center">
            <template #default="{ row }">
              <el-switch
                :model-value="row.enabled"
                :loading="schedulerToggling === row.id"
                :disabled="!canEditScheduler"
                @change="(v: string | number | boolean) => toggleSchedulerJob(row, !!v)"
              />
            </template>
          </el-table-column>
          <el-table-column label="周期" width="150">
            <template #default="{ row }">
              <div>{{ row.trigger }}</div>
              <div v-if="row.overridden" class="muted">默认 {{ row.default_trigger }}</div>
            </template>
          </el-table-column>
          <el-table-column label="下次执行" width="180">
            <template #default="{ row }">
              <span v-if="!row.enabled" class="muted">已停用</span>
              <span v-else-if="row.next_run_at">{{ formatSchedulerTime(row.next_run_at) }}</span>
              <span v-else class="muted">{{ row.registered ? '—' : '等待 Leader 同步' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="上次执行" width="180">
            <template #default="{ row }">
              <span v-if="row.last_run_at">{{ formatSchedulerTime(row.last_run_at) }}</span>
              <span v-else class="muted">暂无记录</span>
            </template>
          </el-table-column>
          <el-table-column label="最近结果" width="130">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.last_status === 'error' && row.last_error"
                :content="row.last_error"
                placement="top"
              >
                <el-tag size="small" type="danger">失败</el-tag>
              </el-tooltip>
              <el-tag v-else-if="row.last_status === 'error'" size="small" type="danger">失败</el-tag>
              <el-tag v-else-if="row.last_status === 'ok'" size="small" type="success">成功</el-tag>
              <el-tooltip
                v-else-if="row.last_status === 'skipped'"
                :content="row.last_error || '主动跳过或基础设施暂不可用（不计入失败）'"
                placement="top"
              >
                <el-tag size="small" type="info">跳过</el-tag>
              </el-tooltip>
              <span v-else class="muted">—</span>
              <div v-if="row.last_duration_ms != null" class="muted scheduler-dur">
                {{ formatSchedulerDuration(row.last_duration_ms) }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="累计" width="130">
            <template #default="{ row }">
              <span class="muted" :title="'跳过含锁忙/库重建等瞬态，不计入失败'">
                成 {{ row.ok_count || 0 }} / 败
                <span :class="{ 'scheduler-fail-n': Number(row.error_count || 0) > 0 }">
                  {{ row.error_count || 0 }}
                </span>
                <template v-if="Number(row.skip_count || 0) > 0">
                  / 跳 {{ row.skip_count }}
                </template>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="调度" width="100">
            <template #default="{ row }">
              <el-tag v-if="!row.enabled" size="small" type="info">停用</el-tag>
              <el-tag v-else-if="row.registered" size="small" type="success">已注册</el-tag>
              <el-tag v-else size="small" type="warning">仅目录</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :disabled="!canEditScheduler" @click="openSchedulerEdit(row)">
                修改周期
              </el-button>
              <el-button
                v-if="row.overridden"
                link
                type="warning"
                :disabled="!canEditScheduler"
                @click="resetSchedulerJob(row)"
              >
                恢复默认
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!schedulerLoading && !filteredSchedulerJobs.length" description="无匹配任务" />

        <el-dialog
          v-model="schedulerDlgVisible"
          :title="schedulerEditing ? `修改周期 · ${schedulerEditing.title}` : '修改周期'"
          width="440px"
          destroy-on-close
        >
          <el-form v-if="schedulerForm" label-width="100px">
            <el-form-item label="任务 ID">
              <code>{{ schedulerEditing?.id }}</code>
            </el-form-item>
            <el-form-item label="启用">
              <el-switch v-model="schedulerForm.enabled" />
            </el-form-item>
            <template v-if="schedulerForm.type === 'interval'">
              <el-form-item label="间隔">
                <el-input-number v-model="schedulerForm.every" :min="1" :max="schedulerEveryMax" />
                <el-select v-model="schedulerForm.unit" style="width: 110px; margin-left: 8px">
                  <el-option label="秒" value="seconds" />
                  <el-option label="分钟" value="minutes" />
                  <el-option label="小时" value="hours" />
                </el-select>
              </el-form-item>
            </template>
            <template v-else-if="schedulerForm.type === 'cron'">
              <el-form-item label="每天">
                <el-input-number v-model="schedulerForm.hour" :min="0" :max="23" />
                <span style="margin: 0 6px">时</span>
                <el-input-number v-model="schedulerForm.minute" :min="0" :max="59" />
                <span style="margin-left: 6px">分</span>
              </el-form-item>
            </template>
            <template v-else-if="schedulerForm.type === 'cron_interval'">
              <el-form-item label="每隔">
                <el-input-number v-model="schedulerForm.every_minutes" :min="1" :max="60" />
                <span style="margin-left: 8px">分钟</span>
              </el-form-item>
            </template>
            <p class="field-desc">默认周期：{{ schedulerEditing?.default_trigger }}</p>
          </el-form>
          <template #footer>
            <el-button @click="schedulerDlgVisible = false">取消</el-button>
            <el-button type="primary" :loading="schedulerSaving" @click="saveSchedulerJob">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const activeTab = ref('overview')
const overview = ref<any>(null)
const platformForm = ref<any[]>([])
const businessSaving = ref(false)
const canManagePlatform = computed(() => auth.hasPermission('system:manage'))
const pageReady = computed(() => !!overview.value)
const platformGroups = computed(() => {
  if (!canManagePlatform.value) return []
  const ids = new Set(platformForm.value.map((i) => i.group))
  const defs = overview.value?.business_group_defs || overview.value?.business_groups || []
  return defs.filter((g: any) => ids.has(g.id)).map((g: any) => ({
    id: g.id,
    label: g.label,
    description: g.description,
  }))
})
const envKeyword = ref('')
const envGroupFilter = ref('')
const schedulerLoading = ref(false)
const schedulerJobs = ref<any>(null)
const schedulerKeyword = ref('')
const schedulerCategory = ref('')
const schedulerToggling = ref('')
const schedulerDlgVisible = ref(false)
const schedulerEditing = ref<any>(null)
const schedulerSaving = ref(false)
const schedulerForm = ref<any>(null)

const canEditScheduler = computed(() => true)

const ENUM_LABELS: Record<string, Record<string, string>> = {
  mini_program_env_version: {
    auto: '自动（开发→开发版 / 生产→正式版）',
    develop: '开发版',
    trial: '体验版',
    release: '正式版',
  },
}

function enumLabel(key: string, value: string) {
  return ENUM_LABELS[key]?.[value] || value
}

function isLongText(item: any) {
  const max = Number(item.max_length || 0)
  return max >= 500 || ['webhook_ip_allowlist', 'trusted_proxy_ips'].includes(item.key)
}

function platformItemsOfGroup(groupId: string) {
  return platformForm.value.filter((i) => i.group === groupId)
}

function isDirty(item: any) {
  if (item.type === 'bool') return item.boolValue !== item._savedBool
  if (item.type === 'string' || item.type === 'enum') return item.strValue !== item._savedStr
  return item.numValue !== item._saved
}

function groupSavable(items: any[]) {
  if (!items.length) return false
  if (!items.some((i) => isDirty(i))) return false
  return items.every((i) => {
    if (i.type === 'bool' || i.type === 'string' || i.type === 'enum') return true
    return Number.isFinite(i.numValue)
  })
}

function canSavePlatformGroup(groupId: string) {
  return groupSavable(platformItemsOfGroup(groupId))
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

const schedulerCategories = computed(() => {
  const items = schedulerJobs.value?.items || []
  return Array.from(new Set(items.map((i: any) => i.category).filter(Boolean))) as string[]
})

const filteredSchedulerJobs = computed(() => {
  const items = schedulerJobs.value?.items || []
  const kw = schedulerKeyword.value.trim().toLowerCase()
  return items.filter((it: any) => {
    if (schedulerCategory.value && it.category !== schedulerCategory.value) return false
    if (!kw) return true
    return (
      String(it.title || '').toLowerCase().includes(kw)
      || String(it.category || '').toLowerCase().includes(kw)
      || String(it.description || '').toLowerCase().includes(kw)
      || String(it.id || '').toLowerCase().includes(kw)
      || String(it.trigger || '').toLowerCase().includes(kw)
    )
  })
})

const schedulerSummary = computed(() => {
  const s = schedulerJobs.value
  if (!s) return '定时任务'
  const items = s.items || []
  const ran = items.filter((i: any) => i.last_run_at).length
  const failed = items.filter((i: any) => i.last_status === 'error').length
  const parts = [
    `共 ${s.total ?? 0} 个`,
    `有记录 ${ran}`,
    failed ? `最近失败 ${failed}` : '最近无失败',
    `角色 ${s.process_role || '-'}`,
    s.leader_held ? '持有 Leader 锁' : '未持有 Leader 锁',
    s.scheduler_running ? '调度器运行中' : '调度器未运行',
  ]
  return parts.join(' · ')
})

function formatSchedulerTime(raw: string | null | undefined) {
  if (!raw) return '—'
  const s = String(raw).trim()
  // 后端已规范为业务时区墙钟：2026-10-19 14:00:00
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) return s
  // 兼容旧版 ISO（按 Asia/Shanghai 转墙钟，禁止直接砍掉时区导致差 8 小时）
  const d = new Date(s.includes(' ') && !s.includes('T') ? s.replace(' ', 'T') : s)
  if (Number.isNaN(d.getTime())) return s
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).formatToParts(d)
    const get = (t: string) => parts.find((p) => p.type === t)?.value || ''
    const hour = get('hour') === '24' ? '00' : get('hour')
    return `${get('year')}-${get('month')}-${get('day')} ${hour}:${get('minute')}:${get('second')}`
  } catch {
    return s.replace('T', ' ').slice(0, 19)
  }
}

function formatSchedulerDuration(ms: number | null | undefined) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return ''
  if (n < 1000) return `${Math.round(n)} ms`
  if (n < 60_000) return `${(n / 1000).toFixed(1)} s`
  return `${(n / 60_000).toFixed(1)} min`
}

const schedulerEveryMax = computed(() => {
  const unit = schedulerForm.value?.unit
  if (unit === 'seconds') return 86400
  if (unit === 'hours') return 168
  return 1440
})

function buildSchedulePayload(form: any) {
  if (form.type === 'interval') {
    return { type: 'interval', unit: form.unit, every: Number(form.every) }
  }
  if (form.type === 'cron') {
    return { type: 'cron', hour: Number(form.hour), minute: Number(form.minute) }
  }
  if (form.type === 'cron_interval') {
    return { type: 'cron_interval', every_minutes: Number(form.every_minutes) }
  }
  return null
}

function openSchedulerEdit(row: any) {
  const sch = row.schedule || row.default_schedule || {}
  schedulerEditing.value = row
  schedulerForm.value = {
    enabled: !!row.enabled,
    type: sch.type || 'interval',
    unit: sch.unit || 'minutes',
    every: Number(sch.every ?? 1),
    hour: Number(sch.hour ?? 3),
    minute: Number(sch.minute ?? 0),
    every_minutes: Number(sch.every_minutes ?? 2),
  }
  schedulerDlgVisible.value = true
}

async function toggleSchedulerJob(row: any, enabled: boolean) {
  schedulerToggling.value = row.id
  try {
    const res = (await api.put(`/settings/scheduler-jobs/${row.id}`, { enabled })) as {
      data: any
      message?: string
    }
    schedulerJobs.value = res.data
    ElMessage.success(res.message || (enabled ? '已启用' : '已停用'))
  } catch (e: any) {
    ElMessage.error(e.message || '更新失败')
    await loadSchedulerJobs()
  } finally {
    schedulerToggling.value = ''
  }
}

async function saveSchedulerJob() {
  if (!schedulerEditing.value || !schedulerForm.value) return
  const schedule = buildSchedulePayload(schedulerForm.value)
  if (!schedule) {
    ElMessage.warning('无法识别的周期类型')
    return
  }
  schedulerSaving.value = true
  try {
    const res = (await api.put(`/settings/scheduler-jobs/${schedulerEditing.value.id}`, {
      enabled: !!schedulerForm.value.enabled,
      schedule,
    })) as { data: any; message?: string }
    schedulerJobs.value = res.data
    schedulerDlgVisible.value = false
    ElMessage.success(res.message || '已保存')
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    schedulerSaving.value = false
  }
}

async function resetSchedulerJob(row: any) {
  try {
    const res = (await api.put(`/settings/scheduler-jobs/${row.id}`, { reset: true })) as {
      data: any
      message?: string
    }
    schedulerJobs.value = res.data
    ElMessage.success(res.message || '已恢复默认')
  } catch (e: any) {
    ElMessage.error(e.message || '恢复失败')
  }
}

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

function buildPlatformForm(items: any[]) {
  const platform = (items || []).filter((it) => it.scope === 'platform' || it.store_override === false)
  platformForm.value = platform.map((item) => {
    if (item.type === 'bool') {
      const boolValue = parseBool(item)
      return { ...item, boolValue, _savedBool: boolValue }
    }
    if (item.type === 'string' || item.type === 'enum') {
      const strValue = item.value == null ? '' : String(item.value)
      return { ...item, strValue, _savedStr: strValue }
    }
    const numValue = parseNum(item)
    return { ...item, numValue, _saved: numValue }
  })
}

function payloadOfItem(item: any): string | number | boolean {
  if (item.type === 'bool') return item.boolValue
  if (item.type === 'string' || item.type === 'enum') return item.strValue
  return item.numValue
}

async function savePlatformGroup(groupId: string) {
  const dirty = platformItemsOfGroup(groupId).filter((i) => isDirty(i))
  if (!dirty.length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  businessSaving.value = true
  try {
    const settings: Record<string, string | number | boolean> = {}
    for (const item of dirty) {
      settings[item.key] = payloadOfItem(item)
    }
    const res = await api.put('/settings/business', { settings })
    buildPlatformForm(res.data?.business || [])
    ElMessage.success(`已保存平台 ${dirty.length} 项`)
    refreshHealth(false)
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    businessSaving.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'scheduler' && !schedulerJobs.value) {
    loadSchedulerJobs()
  }
}

async function loadSchedulerJobs() {
  schedulerLoading.value = true
  try {
    schedulerJobs.value = (await api.get('/settings/scheduler-jobs')).data
  } catch (e: any) {
    ElMessage.error(e.message || '加载定时任务失败')
  } finally {
    schedulerLoading.value = false
  }
}

async function load() {
  if (!canManagePlatform.value) {
    await router.replace({ path: '/store-info', query: { tab: 'biz' } })
    return
  }
  loading.value = true
  try {
    overview.value = (await api.get('/settings')).data
    buildPlatformForm(overview.value?.business || [])
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
.scheduler-dur { margin-top: 2px; font-size: 12px; line-height: 1.2; }
.scheduler-fail-n { color: #f56c6c; font-weight: 600; }
code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
</style>
