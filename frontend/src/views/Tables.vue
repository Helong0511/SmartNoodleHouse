<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="全部门店"
        style="width: 180px"
        @change="load"
      >
        <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-button type="primary" @click="openForm()">新增桌台</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="tables" style="margin-top:12px" border>
      <el-table-column v-if="auth.isSuperAdmin" prop="store_name" label="门店" min-width="40" />
      <el-table-column prop="table_no" label="桌号" width="100" />
      <el-table-column prop="table_name" label="名称" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="qr_token" label="Token" show-overflow-tooltip />
      <el-table-column label="桌码" width="100">
        <template #default="{ row }">
          <el-button link type="primary" @click="openPrint(row)">打印/查看</el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="warning" @click="regenerate(row.id)">重新生成</el-button>
          <el-button link :type="row.status === 1 ? 'danger' : 'success'" @click="toggleStatus(row)">
            {{ row.status === 1 ? '禁用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑桌台' : '新增桌台'" width="420px">
      <el-form :model="form" label-width="80px">
        <el-form-item v-if="!form.id && auth.isSuperAdmin" label="门店" required>
          <el-select v-model="form.store_id" placeholder="选择门店" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="桌号" required>
          <el-input v-model="form.table_no" placeholder="如 A01" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.table_name" placeholder="默认同桌号" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="printVisible" :title="`桌码 · ${printRow?.table_name || printRow?.table_no || ''}`" width="480px">
      <div v-if="printRow" class="print-box" id="table-qr-print">
        <img src="/logo.png?v=202609080938" alt="肠旺面" class="print-logo" />
        <div class="print-title">{{ printRow.table_name || printRow.table_no }}</div>
        <div class="print-sub">
          扫码点餐 · 桌号 {{ printRow.table_no }}
          <span v-if="printRow.store_name"> · {{ printRow.store_name }}</span>
        </div>
        <div class="qr-row">
          <div class="qr-item primary">
            <img
              v-if="printRow.scanQrDataUrl"
              :src="printRow.scanQrDataUrl"
              alt="一码通扫"
            />
            <div v-else class="qr-loading primary-loading">正在生成二维码…</div>
            <div class="qr-label">一码通扫（推荐）</div>
            <div class="qr-desc">微信 / 支付宝均可扫 · 中间为门店 Logo</div>
          </div>
        </div>
        <p class="hint">
          贴桌请用「一码通扫」。正式环境：开通 URL Link / 明文 Scheme 或「普通链接二维码」后，微信扫码可进小程序。
          本地开发：微信内扫通扫码只能开 H5；请用下方「微信开发调试码」（官方小程序码），或开发者工具真机调试后再在小程序内扫码绑桌。
        </p>
        <el-input :model-value="printRow.scan_url || printRow.scan_full" readonly>
          <template #append>
            <el-button @click="copyText(printRow.scan_url || printRow.scan_full)">复制链接</el-button>
          </template>
        </el-input>
        <details class="more-links" open>
          <summary>本地调试码（微信 / 支付宝单独扫）</summary>
          <div class="qr-row sub">
            <div class="qr-item">
              <img
                v-if="printRow.wechatMinicodeUrl"
                :src="printRow.wechatMinicodeUrl"
                alt="微信小程序码"
              />
              <div v-else-if="printRow.wechatMinicodeError" class="qr-error">
                {{ printRow.wechatMinicodeError }}
              </div>
              <div v-else class="qr-loading">正在生成微信小程序码…</div>
              <div>微信开发调试码</div>
              <div class="qr-desc">官方小程序码（develop），请用微信扫；勿用 weixin:// 普通二维码</div>
            </div>
            <div class="qr-item">
              <img
                v-if="printRow.alipayQrDataUrl"
                :src="printRow.alipayQrDataUrl"
                alt="支付宝"
              />
              <div v-else class="qr-loading">正在生成…</div>
              <div>支付宝 Scheme</div>
              <div class="qr-desc">需配置 ALIPAY_APP_ID；开发版先开联调</div>
            </div>
          </div>
          <p class="hint debug-steps">
            若仍无法打开：1）开发者工具先「真机预览/真机调试」一次；2）手机微信号须是该小程序的开发者/体验者；
            3）公众平台确认 AppID 与 .env 一致。
          </p>
          <el-button
            class="copy-scheme"
            size="small"
            @click="copyText(printRow.wechat_scheme || printRow.wechat_qr_url)"
          >复制微信 Scheme（备用，系统浏览器打开）</el-button>
        </details>
      </div>
      <template #footer>
        <el-button @click="printVisible = false">关闭</el-button>
        <el-button type="primary" @click="doPrint">打印</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { qrDataUrlWithLogo } from '@/utils/qrWithLogo'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const tables = ref<any[]>([])
const stores = ref<any[]>([])
const filterStoreId = ref<number | undefined>()
const dialogVisible = ref(false)
const printVisible = ref(false)
const printRow = ref<any>(null)
const form = reactive<{ id: number; store_id?: number; table_no: string; table_name: string }>({
  id: 0,
  store_id: undefined,
  table_no: '',
  table_name: '',
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function h5Full(path: string) {
  const origin = window.location.origin
  if (!path) return origin
  if (path.startsWith('http')) return path
  return `${origin}${path.startsWith('/') ? '' : '/'}${path}`
}

async function buildPrintQrs(row: any) {
  const scan = row.scan_url || row.scan_full || ''
  const alipay = row.alipay_scheme || row.alipay_qr_url || ''
  const [scanQrDataUrl, alipayQrDataUrl] = await Promise.all([
    scan ? qrDataUrlWithLogo(scan, { size: 440 }) : Promise.resolve(''),
    alipay ? qrDataUrlWithLogo(alipay, { size: 360 }) : Promise.resolve(''),
  ])
  if (!printRow.value || printRow.value.id !== row.id) return
  printRow.value.scanQrDataUrl = scanQrDataUrl
  printRow.value.alipayQrDataUrl = alipayQrDataUrl
}

async function loadStores() {
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data.items || []
  } catch {
    stores.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/tables', {
      params: {
        page_size: 200,
        store_id: auth.isSuperAdmin ? filterStoreId.value || undefined : undefined,
      },
    })
    tables.value = res.data.items || []
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
      store_id: row.store_id,
      table_no: row.table_no,
      table_name: row.table_name,
    })
  } else {
    const defaultStore =
      filterStoreId.value || auth.user?.store_id || stores.value[0]?.id || undefined
    Object.assign(form, { id: 0, store_id: defaultStore, table_no: '', table_name: '' })
  }
  dialogVisible.value = true
}

async function openPrint(row: any) {
  const scanUrl = row.scan_url || h5Full(`/scan?token=${encodeURIComponent(row.qr_token)}`)
  if (printRow.value?.wechatMinicodeUrl?.startsWith('blob:')) {
    URL.revokeObjectURL(printRow.value.wechatMinicodeUrl)
  }
  printRow.value = {
    ...row,
    scan_url: scanUrl,
    scan_full: scanUrl,
    // 优先用接口现算的 scheme（含 env_version），勿用库里可能过期的 wechat_qr_url
    wechat_scheme: row.wechat_scheme || row.wechat_qr_url,
    alipay_scheme: row.alipay_scheme || row.alipay_qr_url,
    h5_full: h5Full(row.h5_order_url || row.h5_fallback || `/order?token=${row.qr_token}`),
    wechatMinicodeUrl: '',
    wechatMinicodeError: '',
    scanQrDataUrl: '',
    alipayQrDataUrl: '',
  }
  printVisible.value = true
  await Promise.all([
    buildPrintQrs(printRow.value),
    loadWechatMinicode(row.qr_token),
  ])
}

async function loadWechatMinicode(token: string) {
  if (!token || !printRow.value) return
  try {
    const res = await api.get('/miniprogram/wechat-debug-qrcode', {
      params: { token },
      responseType: 'blob',
    })
    const blob = res instanceof Blob ? res : (res as any)?.data
    if (!(blob instanceof Blob)) {
      printRow.value.wechatMinicodeError = '小程序码返回格式异常'
      return
    }
    if (blob.type && blob.type.includes('json')) {
      const text = await blob.text()
      try {
        const j = JSON.parse(text)
        printRow.value.wechatMinicodeError = j.message || '生成微信小程序码失败'
      } catch {
        printRow.value.wechatMinicodeError = '生成微信小程序码失败'
      }
      return
    }
    printRow.value.wechatMinicodeUrl = URL.createObjectURL(blob)
    printRow.value.wechatMinicodeError = ''
  } catch (e: any) {
    if (printRow.value) {
      printRow.value.wechatMinicodeError = e.message || '生成微信小程序码失败'
    }
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

function doPrint() {
  window.print()
}

async function save() {
  if (!form.table_no.trim()) {
    ElMessage.warning('请输入桌号')
    return
  }
  if (!form.id && auth.isSuperAdmin && !form.store_id) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await api.put(`/tables/${form.id}`, {
        table_no: form.table_no,
        table_name: form.table_name || form.table_no,
      })
      ElMessage.success('已更新')
    } else {
      await api.post('/tables', {
        store_id: form.store_id,
        table_no: form.table_no,
        table_name: form.table_name || form.table_no,
      })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row: any) {
  try {
    await api.put(`/tables/${row.id}`, { status: row.status === 1 ? 0 : 1 })
    ElMessage.success('已更新')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

async function regenerate(id: number) {
  try {
    await api.post(`/tables/${id}/regenerate-qr`)
    ElMessage.success('二维码已重新生成')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.print-box { text-align: center; }
.print-logo {
  display: block;
  width: 88px;
  height: 88px;
  object-fit: contain;
  margin: 0 auto 12px;
  border-radius: 12px;
  background: #fff;
}
.print-title { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
.print-sub { color: #666; margin-bottom: 16px; }
.qr-row { display: flex; justify-content: center; gap: 24px; margin-bottom: 12px; }
.qr-item { font-size: 12px; color: #666; }
.qr-item img { display: block; width: 180px; height: 180px; margin: 0 auto 8px; }
.qr-item.primary img { width: 220px; height: 220px; }
.qr-loading.primary-loading {
  width: 220px;
  height: 220px;
}
.qr-label { font-size: 14px; font-weight: 600; color: #333; margin-top: 4px; }
.qr-desc { font-size: 12px; color: #888; }
.qr-loading, .qr-error {
  width: 140px;
  height: 140px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 8px;
  font-size: 12px;
  line-height: 1.4;
  background: #f5f5f5;
  border-radius: 8px;
  box-sizing: border-box;
}
.qr-error { color: #c45656; background: #fff1f0; }
.hint.debug-steps { text-align: left; margin-top: 10px; }
.more-links { margin-top: 16px; text-align: left; font-size: 13px; color: #666; }
.copy-scheme { margin-top: 10px; }
.qr-row.sub { margin-top: 12px; }
.qr-row.sub .qr-item img { width: 140px; height: 140px; }
.hint { color: #999; font-size: 12px; margin: 8px 0 12px; }
@media print {
  body * { visibility: hidden; }
  #table-qr-print, #table-qr-print * { visibility: visible; }
  #table-qr-print { position: absolute; left: 0; top: 0; width: 100%; }
}
</style>
