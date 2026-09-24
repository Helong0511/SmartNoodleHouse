<template>
  <div class="store-info-page" v-loading="loading">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="activeStoreId"
        placeholder="选择门店"
        filterable
        style="width: 260px"
        @change="onStoreChange"
      >
        <el-option v-for="s in stores" :key="s.id" :label="`${s.name}（${s.store_no}）`" :value="s.id">
          <span style="float: left">{{ s.name }}</span>
          <span style="float: right; color: #909399; font-size: 12px">
            {{ s.status === 1 ? '营业' : '停用' }} · {{ s.store_no }}
          </span>
        </el-option>
      </el-select>
      <el-tag v-else-if="activeStore" type="info">门店：{{ activeStore.name }}</el-tag>
      <el-tag v-if="activeStore" :type="activeStore.status === 1 ? 'success' : 'info'" size="small">
        {{ activeStore.status === 1 ? '营业中' : '已停用' }}
      </el-tag>
      <el-button v-if="auth.isSuperAdmin" type="primary" @click="openCreateStore">新建门店</el-button>
      <el-button @click="reloadAll">刷新</el-button>
    </div>

    <el-empty v-if="!activeStoreId" description="请选择门店" />

    <template v-else>
      <el-tabs v-model="activeTab" class="store-tabs">
        <el-tab-pane label="基本信息" name="basic">
          <el-card shadow="never">
            <el-form label-width="90px" style="max-width: 560px">
              <el-form-item label="编号">
                <span>{{ activeStore?.store_no || '—' }}</span>
              </el-form-item>
              <el-form-item label="Logo">
                <div class="logo-row">
                  <el-image
                    v-if="form.logo"
                    :src="mediaUrl(form.logo)"
                    fit="cover"
                    class="logo-preview"
                    :preview-src-list="[mediaUrl(form.logo)]"
                  />
                  <div v-else class="logo-preview empty">暂无</div>
                  <div class="logo-actions">
                    <el-upload
                      v-if="canEditBasic"
                      :show-file-list="false"
                      accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                      :before-upload="beforeLogoUpload"
                      :http-request="uploadLogo"
                      :disabled="logoUploading"
                    >
                      <el-button type="primary" plain :loading="logoUploading">上传 Logo</el-button>
                    </el-upload>
                    <el-button v-if="canEditBasic && form.logo" link type="danger" @click="clearLogo">清除</el-button>
                    <p class="hint">支持 JPG/PNG/WebP，≤5MB；上传后自动裁为正方形 WebP（最大 512px），便于小程序加载</p>
                  </div>
                </div>
              </el-form-item>
              <el-form-item label="名称" required>
                <el-input v-model="form.name" maxlength="100" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item label="地址">
                <el-input v-model="form.address" maxlength="255" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item label="电话">
                <el-input v-model="form.phone" maxlength="20" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item label="纬度">
                <el-input v-model="form.latitude" placeholder="可选，天气口味用" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item label="经度">
                <el-input v-model="form.longitude" placeholder="可选，天气口味用" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item label="状态">
                <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="营业" inactive-text="停用" :disabled="!canEditBasic" />
              </el-form-item>
              <el-form-item>
                <el-button v-if="canEditBasic" type="primary" :loading="savingBasic" @click="saveBasic">保存基本信息</el-button>
                <el-button v-if="auth.hasPermission('settings:store') || auth.isStoreAdmin || auth.isSuperAdmin" link type="primary" @click="$router.push('/settings')">
                  本店业务参数（打印等）
                </el-button>
                <span v-if="!canEditBasic" class="muted tip">当前账号仅可查看基本信息</span>
              </el-form-item>
            </el-form>
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="营业时间" name="hours">
          <el-card shadow="never">
            <template #header>
              <div class="card-head">
                <span>营业时间与自动开/关店</span>
                <el-button v-if="canEditDelivery" type="primary" :loading="hoursSaving" @click="saveHours">保存营业时间</el-button>
              </div>
            </template>
            <el-alert
              v-if="!canEditDelivery"
              type="info"
              :closable="false"
              show-icon
              title="当前账号无外卖/菜品权限，营业时间只读"
              style="margin-bottom: 12px"
            />
            <el-table :data="hoursDays" size="small" border>
              <el-table-column label="星期" width="80">
                <template #default="{ row }">{{ weekdayNames[row.weekday - 1] }}</template>
              </el-table-column>
              <el-table-column label="营业" width="70" align="center">
                <template #default="{ row }">
                  <el-switch v-model="row.is_open" :disabled="!canEditDelivery" />
                </template>
              </el-table-column>
              <el-table-column label="时段一开始">
                <template #default="{ row }">
                  <el-input v-model="row.slots[0].open_time" placeholder="07:00" style="width: 110px" :disabled="!canEditDelivery" />
                </template>
              </el-table-column>
              <el-table-column label="时段一结束">
                <template #default="{ row }">
                  <el-input v-model="row.slots[0].close_time" placeholder="09:00" style="width: 110px" :disabled="!canEditDelivery" />
                </template>
              </el-table-column>
              <el-table-column label="时段二开始">
                <template #default="{ row }">
                  <el-input v-model="row.slots[1].open_time" placeholder="11:30" style="width: 110px" :disabled="!canEditDelivery" />
                </template>
              </el-table-column>
              <el-table-column label="时段二结束">
                <template #default="{ row }">
                  <el-input v-model="row.slots[1].close_time" placeholder="19:00" style="width: 110px" :disabled="!canEditDelivery" />
                </template>
              </el-table-column>
            </el-table>
            <p class="muted tip">
              结束时间早于或等于开始时间视为跨午夜（如 22:00–02:00）；某天关闭「营业」即全天休息。
              保存后，对下方开启「自动开关」的平台门店，系统将每分钟按营业时间自动开/关店。
            </p>
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="外卖绑定" name="delivery">
          <el-card shadow="never">
            <el-alert
              v-if="!canEditDelivery"
              type="info"
              :closable="false"
              show-icon
              title="当前账号无外卖/菜品权限，绑定信息只读"
              style="margin-bottom: 12px"
            />
            <el-form v-if="canEditDelivery" inline class="bind-form">
              <el-form-item label="平台">
                <el-select v-model="bindingForm.platform" style="width: 120px">
                  <el-option label="美团" value="MEITUAN" />
                  <el-option label="饿了么" value="ELEME" />
                </el-select>
              </el-form-item>
              <el-form-item label="平台门店ID">
                <el-input v-model="bindingForm.platform_shop_id" placeholder="app_poi_code / shopId" />
              </el-form-item>
              <el-form-item label="门店名称">
                <el-input v-model="bindingForm.platform_shop_name" />
              </el-form-item>
              <el-form-item label="Access Token">
                <el-input v-model="bindingForm.access_token" placeholder="可选" style="width: 180px" show-password />
              </el-form-item>
              <el-form-item label="Refresh Token">
                <el-input v-model="bindingForm.refresh_token" placeholder="饿了么 OAuth" style="width: 180px" show-password />
              </el-form-item>
              <el-button type="primary" @click="saveBinding">保存绑定</el-button>
              <el-button @click="loadBindings">刷新</el-button>
            </el-form>
            <el-table :data="bindings" size="small" stripe border>
              <el-table-column prop="platform" label="平台" width="90">
                <template #default="{ row }">{{ platformName(row.platform) }}</template>
              </el-table-column>
              <el-table-column prop="platform_shop_id" label="平台门店ID" min-width="140" />
              <el-table-column prop="platform_shop_name" label="名称" min-width="120" />
              <el-table-column label="已授权" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.has_token ? 'success' : 'info'">{{ row.has_token ? '是' : '否' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Token 过期" width="140">
                <template #default="{ row }">
                  <span v-if="!row.token_expires_at" class="muted">—</span>
                  <el-tag v-else size="small" :type="tokenExpiryTagType(row.token_expires_at)">
                    {{ fmtExpires(row.token_expires_at) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="70">
                <template #default="{ row }">{{ row.status === 1 ? '启用' : '停用' }}</template>
              </el-table-column>
              <el-table-column label="平台营业" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="openStatusMeta(row.platform_open_status).type">
                    {{ openStatusMeta(row.platform_open_status).text }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="自动开关" width="80" align="center">
                <template #default="{ row }">
                  <el-switch
                    :model-value="!!row.auto_sync_open"
                    :disabled="!canEditDelivery || row.status !== 1"
                    @change="(v) => toggleAutoSync(row, !!v)"
                  />
                </template>
              </el-table-column>
              <el-table-column v-if="canEditDelivery" label="操作" width="360" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="success" link :disabled="row.status !== 1" @click="changeStoreOpen(row, 'open')">开店</el-button>
                  <el-button size="small" type="warning" link :disabled="row.status !== 1" @click="changeStoreOpen(row, 'close')">关店</el-button>
                  <el-button
                    size="small"
                    type="primary"
                    link
                    :disabled="!row.has_refresh_token"
                    :loading="refreshingId === row.id"
                    @click="refreshBindingToken(row)"
                  >刷新Token</el-button>
                  <el-button
                    size="small"
                    :type="row.status === 1 ? 'warning' : 'success'"
                    link
                    @click="toggleBindingStatus(row)"
                  >{{ row.status === 1 ? '停用' : '启用' }}</el-button>
                  <el-button
                    size="small"
                    type="danger"
                    link
                    :loading="deletingId === row.id"
                    @click="deleteBinding(row)"
                  >解绑</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-dialog v-model="createStoreVisible" title="新建门店" width="420px" destroy-on-close>
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
          <el-input v-model="createStoreForm.latitude" placeholder="可选" />
        </el-form-item>
        <el-form-item label="经度">
          <el-input v-model="createStoreForm.longitude" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createStoreVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingStore" @click="createStore">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { mediaUrl } from '@/utils/media'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const loading = ref(false)
const stores = ref<any[]>([])
const activeStoreId = ref<number | undefined>()
const activeTab = ref('basic')
const savingBasic = ref(false)
const logoUploading = ref(false)
const hoursSaving = ref(false)
const createStoreVisible = ref(false)
const creatingStore = ref(false)
const refreshingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const bindings = ref<any[]>([])
const weekdayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function emptyHoursDays() {
  return Array.from({ length: 7 }, (_, i) => ({
    weekday: i + 1,
    is_open: false,
    slots: [
      { open_time: '', close_time: '' },
      { open_time: '', close_time: '' },
    ],
  }))
}

const hoursDays = ref<any[]>(emptyHoursDays())
const syncingQuery = ref(false)

const form = reactive({
  name: '',
  logo: '' as string | null,
  address: '',
  phone: '',
  latitude: '' as string | number,
  longitude: '' as string | number,
  status: 1,
})

const createStoreForm = reactive({
  store_no: '',
  name: '',
  address: '',
  phone: '',
  latitude: '' as string | number,
  longitude: '' as string | number,
})

const bindingForm = reactive({
  platform: 'MEITUAN',
  platform_shop_id: '',
  platform_shop_name: '',
  access_token: '',
  refresh_token: '',
})

const activeStore = computed(() => stores.value.find((s) => s.id === activeStoreId.value) || null)
const canEditBasic = computed(() => auth.isSuperAdmin || auth.isStoreAdmin)
const canEditDelivery = computed(
  () =>
    auth.isSuperAdmin
    || auth.isStoreAdmin
    || auth.hasPermission('product:update'),
)

function fillFormFromStore(s: any) {
  form.name = s?.name || ''
  form.logo = s?.logo || ''
  form.address = s?.address || ''
  form.phone = s?.phone || ''
  form.latitude = s?.latitude ?? ''
  form.longitude = s?.longitude ?? ''
  form.status = s?.status ?? 1
}

async function loadStores() {
  const res = await api.get('/settings/stores/options', { params: { include_inactive: true } })
  stores.value = res.data?.items || []
  if (!auth.isSuperAdmin && auth.user?.store_id) {
    activeStoreId.value = auth.user.store_id
  } else if (activeStoreId.value == null && stores.value.length) {
    const qSid = Number(route.query.store_id || 0)
    activeStoreId.value = qSid || stores.value[0].id
  }
}

async function loadHours() {
  if (!activeStoreId.value) return
  try {
    const res = await api.get(`/delivery/stores/${activeStoreId.value}/business-hours`)
    const days = res.data?.days || []
    const base = emptyHoursDays()
    for (const d of days) {
      const t = base.find((x) => x.weekday === d.weekday)
      if (!t) continue
      t.is_open = !!d.is_open
      ;(d.slots || []).slice(0, 2).forEach((s: any, i: number) => {
        t.slots[i] = { open_time: s.open_time || '', close_time: s.close_time || '' }
      })
    }
    hoursDays.value = base
  } catch (e: any) {
    hoursDays.value = emptyHoursDays()
    if (e?.response?.status !== 403) {
      ElMessage.error(e.message || '读取营业时间失败')
    }
  }
}

async function saveHours() {
  if (!activeStoreId.value) return
  hoursSaving.value = true
  try {
    const days = hoursDays.value.map((d) => ({
      weekday: d.weekday,
      is_open: d.is_open,
      slots: d.slots.filter((s: any) => s.open_time && s.close_time),
    }))
    await api.put(`/delivery/stores/${activeStoreId.value}/business-hours`, { days })
    ElMessage.success('营业时间已保存')
    await loadHours()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    hoursSaving.value = false
  }
}

async function loadBindings() {
  if (!activeStoreId.value) {
    bindings.value = []
    return
  }
  try {
    const res = await api.get('/delivery/store-bindings', {
      params: { store_id: activeStoreId.value },
    })
    const data = res.data
    bindings.value = Array.isArray(data) ? data : data?.items || []
  } catch (e: any) {
    bindings.value = []
    if (e?.response?.status !== 403) {
      ElMessage.error(e.message || '读取外卖绑定失败')
    }
  }
}

async function saveBasic() {
  if (!activeStoreId.value) return
  if (!form.name.trim()) {
    ElMessage.warning('门店名称不能为空')
    return
  }
  savingBasic.value = true
  try {
    const latRaw = String(form.latitude || '').trim()
    const lngRaw = String(form.longitude || '').trim()
    await api.put(`/settings/stores/${activeStoreId.value}`, {
      name: form.name.trim(),
      address: form.address?.trim() || null,
      phone: form.phone?.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
      status: form.status,
      logo: form.logo || null,
    })
    ElMessage.success('门店已更新')
    await loadStores()
    fillFormFromStore(activeStore.value)
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingBasic.value = false
  }
}

async function uploadLogo(opt: any) {
  if (!activeStoreId.value) return
  logoUploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', opt.file)
    const res: any = await api.post(`/settings/stores/${activeStoreId.value}/logo`, fd)
    form.logo = res.data?.logo || ''
    ElMessage.success('Logo 已上传')
    await loadStores()
  } catch (e: any) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    logoUploading.value = false
  }
}

const LOGO_MAX_BYTES = 5 * 1024 * 1024
const LOGO_OK_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

function beforeLogoUpload(file: File) {
  const name = (file.name || '').toLowerCase()
  const extOk = /\.(jpe?g|png|webp)$/.test(name)
  const typeOk = !file.type || LOGO_OK_TYPES.has(file.type)
  if (!extOk && !typeOk) {
    ElMessage.error('仅支持 JPG / PNG / WebP')
    return false
  }
  if (file.type && !LOGO_OK_TYPES.has(file.type) && !file.type.startsWith('application/octet-stream')) {
    ElMessage.error('仅支持 JPG / PNG / WebP')
    return false
  }
  if (file.size > LOGO_MAX_BYTES) {
    ElMessage.error('Logo 文件不能超过 5MB')
    return false
  }
  return true
}

async function clearLogo() {
  if (!activeStoreId.value) return
  try {
    await api.put(`/settings/stores/${activeStoreId.value}`, { logo: null })
    form.logo = ''
    ElMessage.success('已清除 Logo')
    await loadStores()
  } catch (e: any) {
    ElMessage.error(e.message || '清除失败')
  }
}

async function saveBinding() {
  if (!activeStoreId.value) return
  if (!bindingForm.platform_shop_id?.trim()) {
    ElMessage.warning('请填写平台门店ID')
    return
  }
  try {
    await api.post('/delivery/store-bindings', {
      ...bindingForm,
      store_id: activeStoreId.value,
    })
    ElMessage.success('保存成功')
    bindingForm.platform_shop_id = ''
    bindingForm.platform_shop_name = ''
    bindingForm.access_token = ''
    bindingForm.refresh_token = ''
    await loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function refreshBindingToken(row: any) {
  refreshingId.value = row.id
  try {
    const res = await api.post(`/delivery/store-bindings/${row.id}/refresh-token`)
    ElMessage.success(`Token 刷新成功，新过期：${fmtExpires(res.data.token_expires_at)}`)
    await loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '刷新失败')
  } finally {
    refreshingId.value = null
  }
}

async function toggleBindingStatus(row: any) {
  const next = row.status === 1 ? 0 : 1
  try {
    await api.patch(`/delivery/store-bindings/${row.id}/status`, { status: next })
    ElMessage.success(next === 1 ? '已启用' : '已停用')
    await loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '切换失败')
  }
}

async function deleteBinding(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认解绑 ${platformName(row.platform)} ${row.platform_shop_name || row.platform_shop_id}？`,
      '解绑确认',
      { confirmButtonText: '解绑', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  deletingId.value = row.id
  try {
    await api.delete(`/delivery/store-bindings/${row.id}`)
    ElMessage.success('已解绑')
    await loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '解绑失败')
  } finally {
    deletingId.value = null
  }
}

async function toggleAutoSync(row: any, next: boolean) {
  try {
    await api.patch(`/delivery/store-bindings/${row.id}/auto-sync-open`, { auto_sync_open: next })
    row.auto_sync_open = next
    ElMessage.success(next ? '已开启按营业时间自动开关店' : '已关闭自动开关店')
  } catch (e: any) {
    ElMessage.error(e.message || '设置失败')
  }
}

async function changeStoreOpen(row: any, act: 'open' | 'close') {
  try {
    const res = await api.post(`/delivery/stores/${row.store_id}/${act}`, { platform: row.platform })
    const fail = (res.data?.results || []).filter((r: any) => !r.ok)
    if (fail.length) ElMessage.warning('部分平台操作失败，请查看日志')
    else ElMessage.success(act === 'open' ? '已开店营业' : '已切换为休息')
    await loadBindings()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || e.message || '操作失败')
  }
}

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtExpires(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const diff = d.getTime() - Date.now()
  const days = Math.floor(diff / 86400000)
  const hours = Math.floor((diff % 86400000) / 3600000)
  if (diff < 0) return '已过期'
  if (days > 0) return `${days}天${hours}时`
  return `${hours}时`
}

function tokenExpiryTagType(v?: string | null) {
  if (!v) return 'info' as const
  const diff = new Date(v).getTime() - Date.now()
  if (diff < 0) return 'danger' as const
  if (diff < 24 * 3600 * 1000) return 'warning' as const
  return 'success' as const
}

function openStatusMeta(s: string | null | undefined) {
  if (s === 'OPEN') return { text: '营业中', type: 'success' as const }
  if (s === 'REST') return { text: '休息中', type: 'info' as const }
  if (s === 'OFFLINE') return { text: '已下线', type: 'danger' as const }
  return { text: '未同步', type: 'info' as const }
}

function openCreateStore() {
  Object.assign(createStoreForm, {
    store_no: '', name: '', address: '', phone: '', latitude: '', longitude: '',
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
    const res: any = await api.post('/settings/stores', {
      store_no: createStoreForm.store_no.trim(),
      name: createStoreForm.name.trim(),
      address: createStoreForm.address.trim() || null,
      phone: createStoreForm.phone.trim() || null,
      latitude: latRaw === '' ? null : Number(latRaw),
      longitude: lngRaw === '' ? null : Number(lngRaw),
    })
    ElMessage.success('门店已创建')
    createStoreVisible.value = false
    await loadStores()
    if (res.data?.id) {
      activeStoreId.value = res.data.id
      await onStoreChange()
    }
  } catch (e: any) {
    ElMessage.error(e.message || '创建失败')
  } finally {
    creatingStore.value = false
  }
}

async function onStoreChange() {
  fillFormFromStore(activeStore.value)
  await Promise.all([loadHours(), loadBindings()])
  syncQuery()
}

async function reloadAll() {
  loading.value = true
  try {
    await loadStores()
    fillFormFromStore(activeStore.value)
    await Promise.all([loadHours(), loadBindings()])
  } finally {
    loading.value = false
  }
}

function syncQuery() {
  if (syncingQuery.value) return
  syncingQuery.value = true
  const tab = activeTab.value
  const sid = activeStoreId.value
  router.replace({
    path: '/store-info',
    query: {
      ...(sid ? { store_id: String(sid) } : {}),
      ...(tab && tab !== 'basic' ? { tab } : {}),
    },
  }).finally(() => {
    syncingQuery.value = false
  })
}

watch(activeTab, () => syncQuery())

onMounted(async () => {
  const tab = String(route.query.tab || '')
  if (['basic', 'hours', 'delivery'].includes(tab)) activeTab.value = tab
  loading.value = true
  try {
    await loadStores()
    fillFormFromStore(activeStore.value)
    await Promise.all([loadHours(), loadBindings()])
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.store-info-page { min-height: 400px; }
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.store-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.logo-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.logo-preview {
  width: 88px;
  height: 88px;
  border-radius: 12px;
  border: 1px solid #ebeef5;
  background: #f5f7fa;
}
.logo-preview.empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  font-size: 13px;
}
.logo-actions .hint {
  margin: 6px 0 0;
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
.bind-form { margin-bottom: 8px; }
.muted { color: #c0c4cc; }
.tip { margin: 8px 0 0; font-size: 12px; color: #909399; line-height: 1.5; }
</style>
