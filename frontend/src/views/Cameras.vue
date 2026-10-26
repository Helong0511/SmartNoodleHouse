<template>
  <div v-loading="loading" class="cameras-page">
    <div class="kpi-row">
      <div class="kpi" v-for="k in kpiCards" :key="k.label">
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
      <div class="kpi-actions">
        <el-select v-if="auth.isSuperAdmin" v-model="storeFilter" clearable placeholder="全部门店"
          style="width: 160px; margin-right: 8px" @change="onStoreFilterChange">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-tag v-else-if="boundStoreName" type="info" style="margin-right: 8px">
          门店：{{ boundStoreName }}
        </el-tag>
        <el-button v-if="canManageCameras" type="primary" @click="openForm()">新增摄像头</el-button>
        <el-button @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <el-table :data="cameras" border size="small" stripe>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="类型" width="130">
        <template #default="{ row }">{{ label(CAMERA_TYPE, row.camera_type) }}</template>
      </el-table-column>
      <el-table-column prop="source_url" label="地址" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ label(CAMERA_STATUS, row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="运行" width="120">
        <template #default="{ row }">
          <span v-if="row.runtime?.has_frame">有帧 {{ row.runtime.frame_age_sec ?? '-' }}s</span>
          <span v-else class="muted">无帧</span>
        </template>
      </el-table-column>
      <el-table-column label="识别耗时" width="168">
        <template #default="{ row }">
          <span v-if="row.runtime?.recognition?.detect_extract_ms != null" class="mono">
            检{{ row.runtime.recognition.detect_extract_ms }}
            <template v-if="row.runtime.recognition.refine_extract_ms != null">
              /精{{ row.runtime.recognition.refine_extract_ms }}
            </template>
            /配{{ row.runtime.recognition.match_ms ?? '-' }} ms
            <template v-if="row.runtime.recognition.face_side_px">
              ·脸{{ row.runtime.recognition.face_side_px }}px
            </template>
          </span>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column prop="fps" label="采集FPS" width="80" />
      <el-table-column prop="detect_fps" label="识别FPS" width="80" />
      <el-table-column prop="store_id" label="门店" width="70" />
      <el-table-column label="操作" width="560" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
          <template v-if="canManageCameras">
            <el-button link size="small" @click="openForm(row)">编辑</el-button>
            <el-button link type="success" size="small" @click="startCam(row.id)">启动</el-button>
            <el-button link type="warning" size="small" @click="restartCam(row.id)">重启</el-button>
            <el-button link type="primary" size="small" @click="probeFace(row.id)">探测人脸</el-button>
            <el-button link type="primary" size="small" @click="testSpeak(row.id, 'pc')">试听电脑</el-button>
            <el-button link type="primary" size="small" @click="testSpeak(row.id, 'camera')">试听摄像头</el-button>
            <el-button link size="small" @click="stopCam(row.id)">停止</el-button>
            <el-button link type="danger" size="small" @click="removeCam(row)">删除</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-card shadow="never" style="margin-top:16px">
      <template #header>
        <div class="card-head">
          <span>最近访客事件</span>
          <div class="card-head-actions">
            <el-button v-if="canManageCameras" link type="danger" @click="clearEvents">清空访客事件</el-button>
            <el-button link type="primary" @click="loadEvents">刷新</el-button>
          </div>
        </div>
        <div class="event-filters">
          <el-select v-model="eventFilter.cameraId" clearable filterable placeholder="全部摄像头" style="width: 200px"
            @change="reloadEvents">
            <el-option v-for="c in cameras" :key="c.id" :label="cameraLabel(c)" :value="c.id" />
          </el-select>
          <el-select v-model="eventFilter.eventType" clearable filterable allow-create default-first-option
            placeholder="全部类型" style="width: 130px" @change="reloadEvents">
            <el-option v-for="t in eventTypes" :key="t" :label="label(VISITOR_EVENT_TYPE, t)" :value="t" />
          </el-select>
          <el-date-picker v-model="eventFilter.dateRange" type="daterange" value-format="YYYY-MM-DD"
            start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" @change="reloadEvents" />
        </div>
      </template>
      <el-table :data="events" size="small" border v-loading="eventsLoading">
        <el-table-column label="抓拍" width="88" align="center">
          <template #default="{ row }">
            <el-image
              v-if="row.snapshot_path"
              :src="snapUrl(row.snapshot_path)"
              :preview-src-list="[snapUrl(row.snapshot_path)]"
              :preview-teleported="true"
              fit="cover"
              class="snap-thumb"
            />
            <span v-else class="muted">无图</span>
          </template>
        </el-table-column>
        <el-table-column prop="event_time" label="时间" width="170">
          <template #default="{ row }">{{ fmt(row.event_time) }}</template>
        </el-table-column>
        <el-table-column label="访客/顾客" min-width="180">
          <template #default="{ row }">
            <div v-if="row.is_member || row.customer_id" class="cust-cell">
              <el-tag size="small" type="success">会员</el-tag>
              <span>{{ row.customer_name || row.customer_display_name || row.customer_nickname || '-' }}</span>
              <span class="muted small">{{ row.visitor_no || row.customer_no }}</span>
            </div>
            <div v-else class="cust-cell">
              <el-tag size="small" type="info">临时</el-tag>
              <span>{{ row.visitor_no }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="摄像头" min-width="120">
          <template #default="{ row }">{{ cameraName(row.camera_id) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ label(VISITOR_EVENT_TYPE, row.event_type) }}</template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="eventPager.page" v-model:page-size="eventPager.pageSize"
          :total="eventPager.total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" background
          small @current-change="loadEvents" @size-change="reloadEvents" />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑摄像头' : '新增摄像头'" width="560px" destroy-on-close>
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required><el-input v-model="form.name" maxlength="100" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.camera_type" style="width:100%">
            <el-option label="USB 摄像头" value="USB" />
            <el-option label="网络摄像头(RTSP)" value="RTSP" />
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
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import {
  CAMERA_STATUS,
  CAMERA_TYPE,
  VISITOR_EVENT_TYPE,
  cameraStatusTagType,
  label,
} from '@/utils/labels'
import { mediaUrl } from '@/utils/media'

const auth = useAuthStore()
const canManageCameras = computed(
  () => auth.isSuperAdmin || auth.hasPermission('camera:manage'),
)
const loading = ref(false)
const saving = ref(false)
const cameras = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
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
/** 打开编辑时的原名称；后端仅在名称真正变化时同步 OSD */
let editNameSnapshot = ''

const kpiCards = computed(() => [
  { label: '摄像头', value: overview.value.camera_count ?? cameras.value.length },
  { label: '在线', value: overview.value.online ?? 0 },
  { label: '重连中', value: overview.value.reconnecting ?? 0 },
  { label: '今日访客', value: overview.value.visitor_today ?? 0 },
  { label: '累计访客', value: overview.value.visitor_total ?? 0 },
])

function statusType(s: string) {
  return cameraStatusTagType(s)
}
function fmt(v?: string) {
  return v ? String(v).replace('T', ' ').slice(0, 19) : '-'
}
function snapUrl(path?: string | null) {
  return mediaUrl(path)
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

async function clearEvents() {
  const camHint = eventFilter.cameraId
    ? `摄像头「${cameraName(eventFilter.cameraId)}」`
    : auth.isSuperAdmin && storeFilter.value
      ? `门店「${stores.value.find((s) => s.id === storeFilter.value)?.name || storeFilter.value}」`
      : '当前门店'
  try {
    await ElMessageBox.confirm(
      `确定清空${camHint}的全部最近访客事件及抓拍图？此操作不可恢复。`,
      '清空访客事件',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const params: Record<string, any> = {}
    if (eventFilter.cameraId) params.camera_id = eventFilter.cameraId
    else if (auth.isSuperAdmin && storeFilter.value) params.store_id = storeFilter.value
    else if (auth.user?.store_id) params.store_id = auth.user.store_id
    const res = await api.delete('/cameras/visitors/events', { params })
    const n = Number(res.data?.deleted_rows ?? 0)
    const files = Number(res.data?.deleted_files ?? 0)
    const orphans = Number(res.data?.orphan_files ?? 0)
    const orphanHint = orphans > 0 ? `（含孤儿图 ${orphans}）` : ''
    ElMessage.success(`已清空 ${n} 条访客事件，删除抓拍图 ${files} 张${orphanHint}`)
    await Promise.all([loadOverview(), reloadEvents()])
  } catch (e: any) {
    ElMessage.error(e.message || '清空失败')
  }
}

async function refreshAll() {
  await Promise.all([loadOverview(), load(), loadEvents()])
}

function openForm(row?: any) {
  editingId.value = row?.id || null
  if (row) {
    editNameSnapshot = String(row.name || '').trim()
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
    editNameSnapshot = ''
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

async function save() {
  if (!form.name?.trim() || !form.source_url?.trim()) {
    ElMessage.warning('请填写名称和源地址')
    return
  }
  saving.value = true
  try {
    const notifyOsd = (res: any) => {
      const osd = res?.data?.osd_sync
      if (!osd) return
      if (osd.ok) ElMessage.success(`画面角标已同步为「${osd.name || form.name.trim()}」`)
      else if (osd.reason && osd.reason !== 'not_isapi') {
        ElMessage.warning('摄像头已保存，但画面角标同步失败（可检查设备账号/网络）')
      }
    }
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
      const res = await api.put(`/cameras/${editingId.value}`, body)
      ElMessage.success('已更新')
      notifyOsd(res)
    } else {
      const res = await api.post('/cameras', {
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
      notifyOsd(res)
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

async function probeFace(id: number) {
  try {
    const res: any = await api.post(`/cameras/${id}/probe-face`)
    const d = res?.data ?? res
    if (!d?.ok) {
      ElMessage.warning(
        d?.reason === 'no_frame'
          ? `无有效帧（状态 ${d?.status || '-'}，帧龄 ${d?.age != null ? Number(d.age).toFixed(1) + 's' : '-'}）`
          : `未检测到人脸${d?.error ? '：' + d.error : ''}`,
      )
      return
    }
    const facePx =
      d.face_side_px != null && d.face_side_px > 0 ? `脸短边${d.face_side_px}px` : ''
    const timing =
      d.detect_extract_ms != null
        ? `检${d.detect_extract_ms}/配${d.match_ms ?? '-'}ms`
        : ''
    const extra = [facePx, timing].filter(Boolean).join(' · ')
    const hint = d.hint ? `；${d.hint}` : ''
    if (d.matched) {
      ElMessage.success(
        `已匹配会员 #${d.best_customer_id}，相似度 ${Number(d.best_similarity || 0).toFixed(4)}（阈值 ${Number(d.threshold || 0).toFixed(2)}）${extra ? ' · ' + extra : ''}${hint}`,
      )
    } else {
      ElMessage.info(
        `检测到人脸，未达匹配阈值（best=${d.best_customer_id ?? '-'} sim=${
          d.best_similarity != null ? Number(d.best_similarity).toFixed(4) : '-'
        } / ${Number(d.threshold || 0).toFixed(2)}）${extra ? ' · ' + extra : ''}${hint}`,
      )
    }
  } catch (e: any) {
    ElMessage.error(e.message || '探测人脸失败')
  }
}

async function testSpeak(id: number, target: 'pc' | 'camera' = 'pc') {
  const row = cameras.value.find((c: any) => c.id === id)
  const online = String(row?.status || '').toUpperCase() === 'ONLINE' || !!row?.runtime?.has_frame
  if (target === 'camera' && !online) {
    try {
      await ElMessageBox.confirm(
        '当前摄像头显示离线/无帧。试听摄像头前建议先「启动/重启」。仍要直接试对讲吗？',
        '摄像头可能离线',
        { type: 'warning', confirmButtonText: '仍试听', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  try {
    // 短句走服务端 WAV/G711 热缓存；连点由后端同摄像头试听「队尾替换」保证下一条能播
    const text = target === 'camera' ? '试听播报' : '电脑试听'
    const res: any = await api.post(`/cameras/${id}/test-speak`, { text, target })
    const d = res?.data ?? res
    const taskId = d?.task_id ? String(d.task_id) : ''
    const tid = taskId ? ` task=${taskId.slice(0, 8)}` : ''
    ElMessage.success(
      target === 'camera'
        ? `已入队摄像头对讲试听（仅入队≠已出声；可连点保留最后一次）${tid}`
        : `已触发电脑扬声器试听${tid}`,
    )
    if (taskId && target === 'camera') {
      void pollSpeakTask(taskId)
    }
  } catch (e: any) {
    ElMessage.error(e.message || '试听失败')
  }
}

/** 轮询播报任务状态（非设备扬声器 ACK）；超时仅提示。 */
async function pollSpeakTask(taskId: string) {
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500))
    try {
      const res: any = await api.get(`/cameras/speak-tasks/${taskId}`)
      const st = res?.data ?? res
      const status = String(st?.status || '')
      if (status === 'completed') {
        ElMessage.info(
          `试听推流结束（设备出声未验证${st?.device_playback ? '·' + st.device_playback : ''}）`,
        )
        return
      }
      if (status === 'failed' || status === 'cancelled') {
        ElMessage.warning(`试听任务${status}${st?.error ? '：' + st.error : ''}`)
        return
      }
    } catch {
      return
    }
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
.cameras-page {
  min-height: 360px;
}

.kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  align-items: stretch;
}

.kpi {
  position: relative;
  min-width: 110px;
  padding: 14px 16px;
  border: 1px solid rgba(120, 200, 255, 0.32);
  border-radius: 12px;
  background: rgba(26, 50, 92, 0.62);
  backdrop-filter: blur(12px) saturate(140%);
  -webkit-backdrop-filter: blur(12px) saturate(140%);
  box-shadow: 0 0 20px rgba(80, 165, 245, 0.18);
}

.kpi-val {
  font-size: 22px;
  font-weight: 700;
  color: #7fc8ff;
  text-shadow: 0 0 12px rgba(127, 200, 255, 0.5);
}

.kpi-label {
  font-size: 12px;
  color: #7a9cc4;
  margin-top: 4px;
}

.kpi-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  align-items: center;
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-head-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}

.snap-thumb {
  width: 56px;
  height: 56px;
  border-radius: 6px;
  cursor: zoom-in;
}

.cust-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.cust-cell .small {
  font-size: 12px;
}

.event-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.muted {
  color: #c0c4cc;
  font-size: 12px;
}

.preview-wrap {
  text-align: center;
}

.preview-img {
  max-width: 100%;
  max-height: 70vh;
  background: #111;
  border-radius: 4px;
}
</style>
