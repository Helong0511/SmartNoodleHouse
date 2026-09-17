<template>
  <div class="bindings-page">
    <el-card shadow="never" class="hours-card" style="margin-bottom: 12px">
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px">
          <span style="font-weight: 600">营业时间与自动开/关店</span>
          <div style="display: flex; gap: 8px; align-items: center">
            <el-select v-model="hoursStoreId" placeholder="选择门店" style="width: 170px" @change="loadHours">
              <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
            <el-button @click="loadHours">读取</el-button>
            <el-button type="primary" :loading="hoursSaving" @click="saveHours">保存营业时间</el-button>
          </div>
        </div>
      </template>
      <el-table :data="hoursDays" size="small" border>
        <el-table-column label="星期" width="80">
          <template #default="{ row }">{{ weekdayNames[row.weekday - 1] }}</template>
        </el-table-column>
        <el-table-column label="营业" width="70" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.is_open" />
          </template>
        </el-table-column>
        <el-table-column label="时段一开始">
          <template #default="{ row }">
            <el-input v-model="row.slots[0].open_time" placeholder="07:00" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="时段一结束">
          <template #default="{ row }">
            <el-input v-model="row.slots[0].close_time" placeholder="09:00" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="时段二开始">
          <template #default="{ row }">
            <el-input v-model="row.slots[1].open_time" placeholder="11:30" style="width: 110px" />
          </template>
        </el-table-column>
        <el-table-column label="时段二结束">
          <template #default="{ row }">
            <el-input v-model="row.slots[1].close_time" placeholder="19:00" style="width: 110px" />
          </template>
        </el-table-column>
      </el-table>
      <p class="muted" style="margin: 8px 0 0; font-size: 12px">
        结束时间早于或等于开始时间视为跨午夜（如 22:00–02:00）；某天关闭「营业」即全天休息。
        保存后，对下方开启「自动开关」的平台门店，系统将每分钟按营业时间自动开/关店（默认关闭）。
      </p>
    </el-card>

    <el-form inline class="bind-form">
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
        <el-input v-model="bindingForm.access_token" placeholder="可选" style="width: 200px" show-password />
      </el-form-item>
      <el-form-item label="Refresh Token">
        <el-input v-model="bindingForm.refresh_token" placeholder="饿了么 OAuth" style="width: 200px" show-password />
      </el-form-item>
      <el-button type="primary" @click="saveBinding">保存绑定</el-button>
      <el-button @click="loadBindings">刷新</el-button>
    </el-form>
    <el-table :data="bindings" size="small" stripe>
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
      <el-table-column label="Token 过期" width="170">
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
            :disabled="row.status !== 1"
            @change="toggleAutoSync(row)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360" fixed="right">
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const stores = ref<any[]>([])
const bindings = ref<any[]>([])

// ── 营业时间 ──
const hoursStoreId = ref<number | undefined>()
const hoursSaving = ref(false)
const weekdayNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const hoursDays = ref<any[]>([])

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

async function loadHours() {
  if (!hoursStoreId.value) return
  try {
    const res = await api.get(`/delivery/stores/${hoursStoreId.value}/business-hours`)
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
    ElMessage.error(e.message || '读取营业时间失败')
  }
}

async function saveHours() {
  if (!hoursStoreId.value) {
    ElMessage.warning('请先选择门店')
    return
  }
  hoursSaving.value = true
  try {
    const days = hoursDays.value.map((d) => ({
      weekday: d.weekday,
      is_open: d.is_open,
      slots: d.slots.filter((s: any) => s.open_time && s.close_time),
    }))
    await api.put(`/delivery/stores/${hoursStoreId.value}/business-hours`, { days })
    ElMessage.success('营业时间已保存')
    await loadHours()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    hoursSaving.value = false
  }
}

// ── 绑定 ──
const bindingForm = reactive({
  platform: 'MEITUAN',
  platform_shop_id: '',
  platform_shop_name: '',
  access_token: '',
  refresh_token: '',
})
const refreshingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)

async function loadBindings() {
  const res = await api.get('/delivery/store-bindings')
  const data = res.data
  bindings.value = Array.isArray(data) ? data : data?.items || []
  const defaultSid = auth.user?.store_id || bindings.value[0]?.store_id
  if (defaultSid && hoursStoreId.value == null) {
    hoursStoreId.value = Number(defaultSid)
    loadHours()
  }
}

async function saveBinding() {
  if (!bindingForm.platform_shop_id?.trim()) {
    ElMessage.warning('请填写平台门店ID')
    return
  }
  try {
    await api.post('/delivery/store-bindings', bindingForm)
    ElMessage.success('保存成功')
    loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function refreshBindingToken(row: any) {
  refreshingId.value = row.id
  try {
    const res = await api.post(`/delivery/store-bindings/${row.id}/refresh-token`)
    ElMessage.success(`Token 刷新成功，新过期：${fmtExpires(res.data.token_expires_at)}`)
    loadBindings()
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
    loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '切换失败')
  }
}

async function deleteBinding(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认解绑 ${platformName(row.platform)} ${row.platform_shop_name || row.platform_shop_id}？\n解绑后该门店回调将被拒绝落单，且 Token 立即失效。`,
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
    loadBindings()
  } catch (e: any) {
    ElMessage.error(e.message || '解绑失败')
  } finally {
    deletingId.value = null
  }
}

async function toggleAutoSync(row: any) {
  const next = !!row.auto_sync_open
  try {
    await api.patch(`/delivery/store-bindings/${row.id}/auto-sync-open`, { auto_sync_open: next })
    ElMessage.success(next ? '已开启按营业时间自动开关店' : '已关闭自动开关店')
  } catch (e: any) {
    row.auto_sync_open = !next
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

// ── 工具函数 ──
function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtExpires(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const now = Date.now()
  const diff = d.getTime() - now
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
  if (s === 'OPEN') return { text: '营业中', type: 'success' }
  if (s === 'REST') return { text: '休息中', type: 'info' }
  if (s === 'OFFLINE') return { text: '已下线', type: 'danger' }
  return { text: '未同步', type: 'info' }
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

onMounted(async () => {
  await loadStores()
  await loadBindings()
})
</script>

<style scoped>
.bindings-page {
  min-height: 400px;
}
.bind-form {
  margin-bottom: 8px;
}
.muted {
  color: #c0c4cc;
}
</style>
