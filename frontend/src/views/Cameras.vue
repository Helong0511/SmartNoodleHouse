<template>
  <div v-loading="loading" class="cameras-page">
    <div class="kpi-row">
      <div class="kpi" v-for="k in kpiCards" :key="k.label">
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
      <div class="kpi-actions">
        <el-select
          v-if="auth.isSuperAdmin"
          v-model="storeFilter"
          clearable
          placeholder="全部门店"
          style="width: 160px; margin-right: 8px"
          @change="onStoreFilterChange"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-tag v-else-if="boundStoreName" type="info" style="margin-right: 8px">
          门店：{{ boundStoreName }}
        </el-tag>
        <el-button type="primary" @click="openForm()">新增摄像头</el-button>
        <el-button @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <el-table :data="cameras" border size="small" stripe>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="camera_type" label="类型" width="80" />
      <el-table-column prop="source_url" label="地址" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="运行" width="120">
        <template #default="{ row }">
          <span v-if="row.runtime?.has_frame">有帧 {{ row.runtime.frame_age_sec ?? '-' }}s</span>
          <span v-else class="muted">无帧</span>
        </template>
      </el-table-column>
      <el-table-column prop="fps" label="采集FPS" width="80" />
      <el-table-column prop="detect_fps" label="识别FPS" width="80" />
      <el-table-column prop="store_id" label="门店" width="70" />
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
          <el-button link size="small" @click="openForm(row)">编辑</el-button>
          <el-button link type="success" size="small" @click="startCam(row.id)">启动</el-button>
          <el-button link type="warning" size="small" @click="restartCam(row.id)">重启</el-button>
          <el-button link size="small" @click="stopCam(row.id)">停止</el-button>
          <el-button link type="danger" size="small" @click="removeCam(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-card shadow="never" style="margin-top:16px">
      <template #header>
        <div class="card-head">
          <span>最近访客事件</span>
          <el-button link type="primary" @click="loadEvents">刷新</el-button>
        </div>
        <div class="event-filters">
          <el-select
            v-model="eventFilter.cameraId"
            clearable
            filterable
            placeholder="全部摄像头"
            style="width: 200px"
            @change="reloadEvents"
          >
            <el-option
              v-for="c in cameras"
              :key="c.id"
              :label="cameraLabel(c)"
              :value="c.id"
            />
          </el-select>
          <el-select
            v-model="eventFilter.eventType"
            clearable
            filterable
            allow-create
            default-first-option
            placeholder="全部类型"
            style="width: 130px"
            @change="reloadEvents"
          >
            <el-option v-for="t in eventTypes" :key="t" :label="t" :value="t" />
          </el-select>
          <el-date-picker
            v-model="eventFilter.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 260px"
            @change="reloadEvents"
          />
        </div>
      </template>
      <el-table :data="events" size="small" border v-loading="eventsLoading">
        <el-table-column prop="event_time" label="时间" width="170">
          <template #default="{ row }">{{ fmt(row.event_time) }}</template>
        </el-table-column>
        <el-table-column prop="visitor_no" label="访客号" min-width="140" />
        <el-table-column label="摄像头" min-width="120">
          <template #default="{ row }">{{ cameraName(row.camera_id) }}</template>
        </el-table-column>
        <el-table-column prop="event_type" label="类型" width="90" />
      </el-table>
      <div class="pager">
        <el-pagination
          v-model:current-page="eventPager.page"
          v-model:page-size="eventPager.pageSize"
          :total="eventPager.total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          background
          small
          @current-change="loadEvents"
          @size-change="reloadEvents"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑摄像头' : '新增摄像头'" width="560px" destroy-on-close>
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required><el-input v-model="form.name" maxlength="100" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.camera_type" style="width:100%">
            <el-option label="USB" value="USB" />
            <el-option label="RTSP" value="RTSP" />
          </el-select>
        </el-form-item>
        <el-form-item label="源地址" required>
          <el-input v-model="form.source_url" placeholder="USB 填 0；RTSP 填 rtsp://..." />
        </el-form-item>
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password :placeholder="editingId ? '留空则不修改' : ''" />
        </el-form-item>
        <el-form-item v-if="editingId" label="清除密码">
          <el-switch v-model="form.clear_password" />
        </el-form-item>
        <el-form-item label="采集帧率"><el-input-number v-model="form.fps" :min="1" :max="60" /></el-form-item>
        <el-form-item label="识别帧率"><el-input-number v-model="form.detect_fps" :min="1" :max="30" /></el-form-item>
        <el-form-item v-if="!editingId" label="门店">
          <el-select v-model="form.store_id" placeholder="选择门店" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editingId" label="创建后启动">
          <el-switch v-model="form.auto_start" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="previewVisible" :title="previewTitle" size="640px" destroy-on-close @closed="onPreviewClose">
      <div v-if="previewUrl" class="preview-wrap">
        <img :src="previewUrl" alt="preview" class="preview-img" />
        <p class="muted">预览票据约 2 分钟有效；无画面请检查启动状态与源地址</p>
      </div>
      <el-empty v-else description="无法加载预览" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const cameras = ref<any[]>([])
const stores = ref<any[]>([])
const storeFilter = ref<number | undefined>()
const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})
const events = ref<any[]>([])
const eventsLoading = ref(false)
const eventTypes = ref<string[]>([])
const eventFilter = reactive<{ cameraId?: number; eventType?: string; dateRange: string[] | null }>({
  cameraId: undefined,
  eventType: undefined,
  dateRange: null,
})
const eventPager = reactive({ page: 1, pageSize: 20, total: 0 })
const overview = ref<any>({})
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const previewVisible = ref(false)
const previewUrl = ref('')
const previewTitle = ref('预览')
let pollTimer: ReturnType<typeof setInterval> | null = null

const form = reactive({
  name: '',
  camera_type: 'USB',
  source_url: '0',
  username: '',
  password: '',
  clear_password: false,
  fps: 25,
  detect_fps: 3,
  store_id: undefined as number | undefined,
  auto_start: true,
})

const kpiCards = computed(() => [
  { label: '摄像头', value: overview.value.camera_count ?? cameras.value.length },
  { label: '在线', value: overview.value.online ?? 0 },
  { label: '重连中', value: overview.value.reconnecting ?? 0 },
  { label: '今日访客', value: overview.value.visitor_today ?? 0 },
  { label: '累计访客', value: overview.value.visitor_total ?? 0 },
])

function statusType(s: string) {
  if (s === 'ONLINE') return 'success'
  if (s === 'RECONNECTING') return 'warning'
  return 'info'
}
function statusLabel(s: string) {
  return ({ ONLINE: '在线', OFFLINE: '离线', RECONNECTING: '重连中', DISABLED: '停用' } as any)[s] || s
}
function fmt(v?: string) {
  return v ? String(v).replace('T', ' ').slice(0, 19) : '-'
}

async function loadOverview() {
  try {
    overview.value = (await api.get('/cameras/overview')).data || {}
  } catch {
    overview.value = {}
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {}
    if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    const res = await api.get('/cameras', { params })
    const data = res.data
    cameras.value = Array.isArray(data) ? data : data?.items || []
  } catch (e: any) {
    // 10 秒轮询的网络抖动不应持续抛未处理 rejection
  } finally {
    loading.value = false
  }
}

function onStoreFilterChange() {
  eventFilter.cameraId = undefined
  load()
  loadEvents()
}

function cameraLabel(c: any) {
  // 超管跨店查看时，摄像头名后附门店，避免同名设备混淆
  if (auth.isSuperAdmin) {
    const sname = stores.value.find((s) => s.id === c.store_id)?.name
    return sname ? `${c.name}（${sname}）` : `${c.name}（门店#${c.store_id}）`
  }
  return c.name
}

function cameraName(id: number) {
  const c = cameras.value.find((x) => x.id === id)
  return c ? cameraLabel(c) : `#${id}`
}

async function loadEvents() {
  eventsLoading.value = true
  try {
    const params: Record<string, any> = {
      page: eventPager.page,
      page_size: eventPager.pageSize,
    }
    if (eventFilter.cameraId) params.camera_id = eventFilter.cameraId
    if (eventFilter.eventType) params.event_type = eventFilter.eventType
    if (eventFilter.dateRange?.length === 2) {
      params.date_from = eventFilter.dateRange[0]
      params.date_to = eventFilter.dateRange[1]
    }
    const res = await api.get('/cameras/visitors/events', { params })
    const list = res.data?.items || []
    // 数据留存过期清理后末页可能变空，自动回退到上一页
    if (list.length === 0 && eventPager.page > 1) {
      eventPager.page -= 1
      eventsLoading.value = false
      await loadEvents()
      return
    }
    events.value = list
    eventPager.total = res.data?.total || 0
    if (Array.isArray(res.data?.event_types)) eventTypes.value = res.data.event_types
  } catch {
    events.value = []
  } finally {
    eventsLoading.value = false
  }
}

function reloadEvents() {
  eventPager.page = 1
  return loadEvents()
}

async function refreshAll() {
  await Promise.all([loadOverview(), load(), loadEvents()])
}

function openForm(row?: any) {
  editingId.value = row?.id || null
  if (row) {
    Object.assign(form, {
      name: row.name,
      camera_type: row.camera_type,
      source_url: row.source_url,
      username: row.username || '',
      password: '',
      clear_password: false,
      fps: row.fps || 25,
      detect_fps: row.detect_fps || 3,
      store_id: row.store_id,
      auto_start: true,
    })
  } else {
    const sid = auth.user?.store_id || stores.value[0]?.id
    Object.assign(form, {
      name: '',
      camera_type: 'USB',
      source_url: '0',
      username: '',
      password: '',
      clear_password: false,
      fps: 25,
      detect_fps: 3,
      store_id: sid,
      auto_start: true,
    })
  }
  dialogVisible.value = true
}

async function loadStores() {
  try {
    stores.value = (await api.get('/settings/stores/options')).data.items || []
  } catch {
    stores.value = []
  }
}

async function save() {
  if (!form.name?.trim() || !form.source_url?.trim()) {
    ElMessage.warning('请填写名称和源地址')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      const body: any = {
        name: form.name.trim(),
        camera_type: form.camera_type,
        source_url: form.source_url.trim(),
        username: form.username || null,
        fps: form.fps,
        detect_fps: form.detect_fps,
        clear_password: form.clear_password,
      }
      if (form.password) body.password = form.password
      await api.put(`/cameras/${editingId.value}`, body)
      ElMessage.success('已更新')
    } else {
      await api.post('/cameras', {
        name: form.name.trim(),
        camera_type: form.camera_type,
        source_url: form.source_url.trim(),
        username: form.username || null,
        password: form.password || null,
        fps: form.fps,
        detect_fps: form.detect_fps,
        store_id: form.store_id,
        auto_start: form.auto_start,
      })
      ElMessage.success('摄像头已创建')
    }
    dialogVisible.value = false
    refreshAll()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function openPreview(row: any) {
  previewTitle.value = `预览 · ${row.name}`
  previewVisible.value = true
  previewUrl.value = ''
  try {
    const res = await api.post(`/cameras/${row.id}/stream-ticket`)
    const ticket = res.data?.ticket
    if (!ticket) throw new Error('无法签发预览票据')
    previewUrl.value = `/api/cameras/${row.id}/stream?ticket=${encodeURIComponent(ticket)}`
  } catch (e: any) {
    ElMessage.error(e.message || '打开失败')
    previewVisible.value = false
  }
}

function onPreviewClose() {
  previewUrl.value = ''
}

async function startCam(id: number) {
  try {
    await api.post(`/cameras/${id}/start`)
    ElMessage.success('已启动')
    refreshAll()
  } catch (e: any) {
    ElMessage.error(e.message || '启动失败')
  }
}

async function restartCam(id: number) {
  try {
    await api.post(`/cameras/${id}/restart`)
    ElMessage.success('已重启')
    refreshAll()
  } catch (e: any) {
    ElMessage.error(e.message || '重启失败')
  }
}

async function stopCam(id: number) {
  try {
    await api.post(`/cameras/${id}/stop`)
    ElMessage.success('已停止')
    refreshAll()
  } catch (e: any) {
    ElMessage.error(e.message || '停止失败')
  }
}

async function removeCam(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除摄像头「${row.name}」？`, '删除确认', { type: 'warning' })
    await api.delete(`/cameras/${row.id}`)
    ElMessage.success('已删除')
    refreshAll()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

onMounted(() => {
  loadStores()
  refreshAll()
  pollTimer = setInterval(() => {
    loadOverview()
    load()
  }, 10000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.cameras-page { min-height: 360px; }
.kpi-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: stretch; }
.kpi { min-width: 100px; padding: 12px 14px; background: #f7f8fa; border-radius: 8px; }
.kpi-val { font-size: 22px; font-weight: 700; }
.kpi-label { font-size: 12px; color: #909399; margin-top: 4px; }
.kpi-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.event-filters { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
.muted { color: #c0c4cc; font-size: 12px; }
.preview-wrap { text-align: center; }
.preview-img { max-width: 100%; max-height: 70vh; background: #111; border-radius: 4px; }
</style>
