<template>
  <div v-loading="loading">
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="券模板" name="templates">
        <div class="toolbar">
          <el-button v-if="canManage" type="primary" @click="openTemplateForm()">新增模板</el-button>
          <el-button @click="loadTemplates">刷新</el-button>
        </div>
        <el-table :data="templates" border style="margin-top:12px">
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="归属" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.store_scope === 'HQ' || !row.store_id ? 'info' : 'success'">
                {{ scopeLabel(row) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              {{ row.coupon_type === 'PERCENT' ? '折扣券' : row.coupon_type === 'VOUCHER' ? '代金券' : '满减券' }}
            </template>
          </el-table-column>
          <el-table-column prop="summary" label="规则" min-width="160" />
          <el-table-column prop="valid_days" label="有效天数" width="100" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
                {{ row.status === 1 ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="canManage" label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <template v-if="canEditTemplate(row)">
                <el-button link type="primary" @click="openTemplateForm(row)">编辑</el-button>
                <el-button link type="warning" @click="toggleTemplate(row)">
                  {{ row.status === 1 ? '停用' : '启用' }}
                </el-button>
              </template>
              <span v-else class="muted">总部只读</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="持券记录" name="issued">
        <el-form inline class="toolbar">
          <el-form-item>
            <el-input
              v-model="issuedKeyword"
              placeholder="券号 / 顾客编号 / 手机 / 昵称"
              clearable
              style="width: 240px"
              @keyup.enter="reloadIssued"
            />
          </el-form-item>
          <el-form-item>
            <el-select v-model="issuedStatus" placeholder="状态" clearable style="width: 120px" @change="reloadIssued">
              <el-option label="可用" value="AVAILABLE" />
              <el-option label="已使用" value="USED" />
              <el-option label="已过期" value="EXPIRED" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-select v-model="issuedTemplateId" placeholder="模板" clearable style="width: 160px" @change="reloadIssued">
              <el-option v-for="t in enabledTemplates" :key="t.id" :label="t.name" :value="t.id" />
            </el-select>
          </el-form-item>
          <el-button type="primary" @click="reloadIssued">搜索</el-button>
          <el-button v-if="canManage" type="success" @click="openIssueDialog">手动发券</el-button>
        </el-form>

        <el-table :data="issued" border>
          <el-table-column prop="coupon_no" label="券号" min-width="220" show-overflow-tooltip />
          <el-table-column prop="template_name" label="模板" width="130" />
          <el-table-column prop="summary" label="规则" min-width="140" />
          <el-table-column label="顾客" min-width="160">
            <template #default="{ row }">
              {{ row.customer_name || '—' }}
              <div class="muted">{{ row.customer_no }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="customer_phone" label="手机" width="120">
            <template #default="{ row }">{{ row.customer_phone || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="expires_at" label="过期时间" width="170" />
          <el-table-column prop="used_at" label="使用时间" width="170">
            <template #default="{ row }">{{ row.used_at || '—' }}</template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-model:current-page="issuedPage"
          v-model:page-size="issuedPageSize"
          :total="issuedTotal"
          layout="total, prev, pager, next"
          style="margin-top:16px"
          @current-change="loadIssued"
        />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="templateDialog" :title="templateForm.id ? '编辑模板' : '新增模板'" width="480px" destroy-on-close>
      <el-form :model="templateForm" label-width="100px">
        <el-form-item v-if="!templateForm.id && auth.isSuperAdmin" label="归属" required>
          <el-radio-group v-model="templateForm.scope">
            <el-radio value="HQ">总部（全店可用）</el-radio>
            <el-radio value="STORE">指定门店</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="!templateForm.id && auth.isSuperAdmin && templateForm.scope === 'STORE'"
          label="门店"
          required
        >
          <el-select v-model="templateForm.store_id" placeholder="选择门店" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="templateForm.name" maxlength="100" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-radio-group v-model="templateForm.coupon_type">
            <el-radio value="FIXED">满减</el-radio>
            <el-radio value="PERCENT">折扣</el-radio>
            <el-radio value="VOUCHER">代金券</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          :label="templateForm.coupon_type === 'PERCENT' ? '折扣比例' : templateForm.coupon_type === 'VOUCHER' ? '代金面额' : '减免金额'"
          required
        >
          <el-input-number
            v-model="templateForm.discount_value"
            :min="templateForm.coupon_type === 'PERCENT' ? 0.01 : 0.01"
            :max="templateForm.coupon_type === 'PERCENT' ? 0.99 : 9999"
            :step="templateForm.coupon_type === 'PERCENT' ? 0.01 : 1"
            :precision="templateForm.coupon_type === 'PERCENT' ? 2 : 2"
            style="width: 100%"
          />
          <div class="hint">
            {{
              templateForm.coupon_type === 'PERCENT'
                ? '如 0.90 表示 9 折（按折后商品小计）'
                : templateForm.coupon_type === 'VOUCHER'
                  ? '代金面额（元），按应付总额抵扣（可与优惠券叠加）'
                  : '固定减免金额（元，按折后商品小计）'
            }}
          </div>
        </el-form-item>
        <el-form-item label="使用门槛">
          <el-input-number v-model="templateForm.min_order_amount" :min="0" :precision="2" style="width: 100%" />
          <div class="hint">按会员折后商品小计计算，0 表示无门槛</div>
        </el-form-item>
        <el-form-item label="有效天数">
          <el-input-number v-model="templateForm.valid_days" :min="1" :max="365" style="width: 100%" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="templateForm.status" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="templateDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="issueDialog" title="手动发券" width="480px" destroy-on-close>
      <el-form :model="issueForm" label-width="90px">
        <el-form-item label="顾客" required>
          <el-select
            v-model="issueForm.customer_id"
            filterable
            remote
            reserve-keyword
            placeholder="输入编号/手机/昵称搜索"
            :remote-method="searchCustomers"
            :loading="customerSearching"
            style="width: 100%"
          >
            <el-option
              v-for="c in customerOptions"
              :key="c.id"
              :label="`${c.display_name || c.nickname || '顾客'}（${c.customer_no}）`"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="券模板" required>
          <el-select v-model="issueForm.template_id" placeholder="选择模板" style="width: 100%">
            <el-option
              v-for="t in enabledTemplates"
              :key="t.id"
              :label="`${t.name} — ${t.summary}`"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="issueQuantityLabel" required>
          <el-input-number
            v-model="issueForm.quantity"
            :min="1"
            :max="50"
            :step="1"
            :disabled="!issueForm.template_id"
            style="width: 100%"
          />
          <div class="hint">同模板可多张，单次最多 50 张</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="issueDialog = false">取消</el-button>
        <el-button type="primary" :loading="issuing" @click="submitIssue">发放</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('coupon:manage'))
const loading = ref(false)
const saving = ref(false)
const issuing = ref(false)
const activeTab = ref('templates')
const stores = ref<{ id: number; name: string }[]>([])

const templates = ref<any[]>([])
const templateDialog = ref(false)
const templateForm = reactive({
  id: 0,
  name: '',
  coupon_type: 'FIXED',
  discount_value: 5,
  min_order_amount: 30,
  valid_days: 30,
  status: 1,
  scope: 'HQ' as 'HQ' | 'STORE',
  store_id: undefined as number | undefined,
})

function scopeLabel(row: any) {
  if (!row?.store_id || row.store_scope === 'HQ') return '总部'
  const s = stores.value.find((x) => x.id === row.store_id)
  return row.store_name || s?.name || `门店#${row.store_id}`
}

/** 门店账号不可改总部模板；超管可改全部 */
function canEditTemplate(row: any) {
  if (!canManage.value) return false
  if (auth.isSuperAdmin) return true
  return !!(row?.store_id)
}

async function loadStores() {
  if (!auth.isSuperAdmin) return
  try {
    const res = await api.get('/settings/stores/options')
    stores.value = res.data?.items || []
  } catch {
    stores.value = []
  }
}

const issued = ref<any[]>([])
const issuedKeyword = ref('')
const issuedStatus = ref<string | undefined>()
const issuedTemplateId = ref<number | undefined>()
const issuedPage = ref(1)
const issuedPageSize = ref(20)
const issuedTotal = ref(0)

const issueDialog = ref(false)
const issueForm = reactive({
  customer_id: undefined as number | undefined,
  template_id: undefined as number | undefined,
  quantity: 1,
})
const customerOptions = ref<any[]>([])
const customerSearching = ref(false)

const enabledTemplates = computed(() => templates.value.filter((t) => t.status === 1))

const selectedIssueTemplate = computed(() =>
  enabledTemplates.value.find((t) => t.id === issueForm.template_id),
)

const issueQuantityLabel = computed(() => {
  const t = selectedIssueTemplate.value
  if (!t) return '发放张数'
  return t.coupon_type === 'VOUCHER' ? '代金券张数' : '优惠券张数'
})

function statusLabel(s: string) {
  return { AVAILABLE: '可用', USED: '已使用', EXPIRED: '已过期' }[s] || s
}
function statusTag(s: string) {
  return { AVAILABLE: 'success', USED: 'info', EXPIRED: 'warning' }[s] || 'info'
}

async function loadTemplates() {
  loading.value = true
  try {
    const res = await api.get('/coupons/templates', { params: { all: true } })
    templates.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

async function loadIssued() {
  loading.value = true
  try {
    const res = await api.get('/coupons/issued', {
      params: {
        page: issuedPage.value,
        page_size: issuedPageSize.value,
        keyword: issuedKeyword.value || undefined,
        status: issuedStatus.value || undefined,
        template_id: issuedTemplateId.value || undefined,
      },
    })
    issued.value = res.data?.items || []
    issuedTotal.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'issued' && !issued.value.length) loadIssued()
}

function reloadIssued() {
  issuedPage.value = 1
  loadIssued()
}

function openTemplateForm(row?: any) {
  if (row) {
    if (!canEditTemplate(row)) {
      ElMessage.warning('无权修改总部优惠券模板')
      return
    }
    Object.assign(templateForm, {
      id: row.id,
      name: row.name,
      coupon_type: row.coupon_type,
      discount_value: Number(row.discount_value),
      min_order_amount: Number(row.min_order_amount),
      valid_days: row.valid_days,
      status: row.status,
      scope: row.store_id ? 'STORE' : 'HQ',
      store_id: row.store_id || undefined,
    })
  } else {
    Object.assign(templateForm, {
      id: 0,
      name: '',
      coupon_type: 'FIXED',
      discount_value: 5,
      min_order_amount: 30,
      valid_days: 30,
      status: 1,
      scope: auth.isSuperAdmin ? 'HQ' : 'STORE',
      store_id: auth.isSuperAdmin ? stores.value[0]?.id : auth.user?.store_id || undefined,
    })
  }
  templateDialog.value = true
}

async function saveTemplate() {
  if (!templateForm.name.trim()) {
    ElMessage.warning('请输入模板名称')
    return
  }
  if (
    !templateForm.id &&
    auth.isSuperAdmin &&
    templateForm.scope === 'STORE' &&
    !templateForm.store_id
  ) {
    ElMessage.warning('请选择门店')
    return
  }
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      name: templateForm.name.trim(),
      coupon_type: templateForm.coupon_type,
      discount_value: templateForm.discount_value,
      min_order_amount: templateForm.min_order_amount,
      valid_days: templateForm.valid_days,
      status: templateForm.status,
    }
    if (!templateForm.id && auth.isSuperAdmin) {
      payload.store_id = templateForm.scope === 'STORE' ? templateForm.store_id : null
    }
    if (templateForm.id) {
      await api.put(`/coupons/templates/${templateForm.id}`, payload)
    } else {
      await api.post('/coupons/templates', payload)
    }
    ElMessage.success('保存成功')
    templateDialog.value = false
    loadTemplates()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleTemplate(row: any) {
  const next = row.status === 1 ? 0 : 1
  await api.patch(`/coupons/templates/${row.id}/status`, { status: next })
  ElMessage.success('已更新')
  loadTemplates()
}

function openIssueDialog() {
  issueForm.customer_id = undefined
  issueForm.template_id = enabledTemplates.value[0]?.id
  issueForm.quantity = 1
  customerOptions.value = []
  issueDialog.value = true
}

async function searchCustomers(q: string) {
  const kw = (q || '').trim()
  if (!kw) {
    customerOptions.value = []
    return
  }
  customerSearching.value = true
  try {
    const res = await api.get('/coupons/customers/search', { params: { keyword: kw, page_size: 20 } })
    customerOptions.value = res.data?.items || []
  } finally {
    customerSearching.value = false
  }
}

async function submitIssue() {
  if (!issueForm.customer_id || !issueForm.template_id) {
    ElMessage.warning('请选择顾客和券模板')
    return
  }
  const quantity = Math.max(1, Math.min(50, Number(issueForm.quantity) || 1))
  issuing.value = true
  try {
    const res = (await api.post('/coupons/issue', {
      customer_id: issueForm.customer_id,
      template_id: issueForm.template_id,
      quantity,
    })) as { message?: string }
    ElMessage.success(res.message || `已发放 ${quantity} 张`)
    issueDialog.value = false
    if (activeTab.value === 'issued') loadIssued()
  } catch (e: any) {
    ElMessage.error(e.message || '发券失败')
  } finally {
    issuing.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await loadTemplates()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.muted {
  color: #999;
  font-size: 12px;
}
.hint {
  color: #999;
  font-size: 12px;
  margin-top: 4px;
}
</style>
