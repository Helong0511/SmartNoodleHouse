<template>
  <div v-loading="loading">
    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="coupon-tip"
      title="与营销活动的关系"
      description="本页维护券模板与手动发券/里程碑。批量按人群发券请：客户分群（圈人物化）→ 营销活动（选模板发布）→ 营销报表（核销与 ROI）。社会贡献身份折扣与券互斥、与会员折取更优，勿与营销发券混淆。"
    />
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="持券记录" name="issued">
        <el-form inline class="toolbar">
          <el-form-item>
            <el-input v-model="issuedKeyword" placeholder="券号 / 顾客编号 / 手机 / 展示名 / 昵称" clearable style="width: 280px"
              @keyup.enter="reloadIssued" />
          </el-form-item>
          <el-form-item>
            <el-select v-model="issuedStatus" placeholder="状态" clearable style="width: 130px" @change="reloadIssued">
              <el-option label="可用" value="AVAILABLE" />
              <el-option label="下单预占" value="RESERVED" />
              <el-option label="已使用" value="USED" />
              <el-option label="已过期" value="EXPIRED" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-select v-model="issuedCouponType" placeholder="券类型" clearable style="width: 120px"
              @change="reloadIssued">
              <el-option label="满减券" value="FIXED" />
              <el-option label="折扣券" value="PERCENT" />
              <el-option label="代金券" value="VOUCHER" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-select v-model="issuedTemplateId" placeholder="模板" clearable filterable style="width: 180px"
              @change="reloadIssued">
              <el-option v-for="t in templates" :key="t.id" :label="t.status === 1 ? t.name : `${t.name}（停用）`"
                :value="t.id" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="auth.isSuperAdmin">
            <el-select v-model="issuedStoreId" placeholder="发券门店" clearable style="width: 160px" @change="reloadIssued">
              <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
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
              <div>{{ formatCustomerNameLines(row).primary }}</div>
              <div v-if="formatCustomerNameLines(row).secondary" class="muted">{{ formatCustomerNameLines(row).secondary }}</div>
              <div class="muted">{{ row.customer_no }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="customer_phone" label="手机" width="120">
            <template #default="{ row }">{{ row.customer_phone || '—' }}</template>
          </el-table-column>
          <el-table-column v-if="auth.isSuperAdmin" label="发券门店" width="120">
            <template #default="{ row }">{{ issuedStoreName(row.issued_at_store_id) }}</template>
          </el-table-column>
          <el-table-column label="发券人" width="110">
            <template #default="{ row }">{{ row.issued_by_name || (row.issued_by_user_id ? `#${row.issued_by_user_id}` :
              '系统发放') }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="obtained_at" label="发券时间" width="280">
            <template #default="{ row }">{{ row.obtained_at || '—' }}</template>
          </el-table-column>
          <el-table-column prop="expires_at" label="过期时间" width="280" />
          <el-table-column prop="used_at" label="使用时间" width="280">
            <template #default="{ row }">{{ row.used_at || '—' }}</template>
          </el-table-column>
        </el-table>

        <el-pagination v-model:current-page="issuedPage" v-model:page-size="issuedPageSize" :total="issuedTotal"
          :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" style="margin-top:16px"
          @current-change="loadIssued" @size-change="reloadIssued" />
      </el-tab-pane>

      <el-tab-pane label="券模板" name="templates">
        <div class="toolbar">
          <el-select v-model="templateScope" clearable placeholder="归属：总部或门店" style="width: 200px">
            <el-option label="总部" value="HQ" />
            <el-option v-for="s in templateStoreFilters" :key="s.id" :label="s.name" :value="String(s.id)" />
          </el-select>
          <el-button v-if="canManage" type="primary" @click="openTemplateForm()">新增模板</el-button>
          <el-button @click="loadTemplates">刷新</el-button>
        </div>
        <el-table :data="filteredTemplates" border style="margin-top:12px">
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
              {{ couponTypeLabel(row.coupon_type) }}
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

      <el-tab-pane label="成单里程碑" name="milestones">
        <el-alert type="info" :closable="false" show-icon title="按本店累计成功支付单数自动发券（如 20/50/100 单）"
          description="每位顾客每个档位只发一次；支付成功后自动下发。请先在「券模板」创建对应满减/代金券模板。" class="milestone-alert" />
        <div class="toolbar" style="margin-top:12px">
          <el-select v-if="auth.isSuperAdmin" v-model="milestoneStoreId" placeholder="选择门店" filterable
            style="width: 200px" @change="loadMilestones">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-button @click="loadMilestones">刷新</el-button>
          <el-button v-if="canManage" @click="addMilestoneRow">添加档位</el-button>
          <el-button v-if="canManage" type="primary" :loading="milestoneSaving" @click="saveMilestones">保存</el-button>
        </div>
        <el-table :data="milestoneRows" border style="margin-top:12px">
          <el-table-column label="累计成单数" width="160">
            <template #default="{ row }">
              <el-input-number v-model="row.threshold_orders" :min="1" :max="100000" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column label="发放券模板" min-width="220">
            <template #default="{ row }">
              <el-select v-model="row.template_id" filterable placeholder="选择模板" :disabled="!canManage"
                style="width: 100%">
                <el-option v-for="t in enabledTemplates" :key="t.id"
                  :label="`${t.name}（${t.summary || couponTypeLabel(t.coupon_type)}）`" :value="t.id" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="90">
            <template #default="{ row }">
              <el-switch v-model="row.status" :active-value="1" :inactive-value="0" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column v-if="canManage" label="操作" width="90">
            <template #default="{ $index }">
              <el-button link type="danger" @click="milestoneRows.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p class="hint" style="margin-top:8px">建议档位：20 / 50 / 100。留空并保存即清空本店规则。</p>
      </el-tab-pane>

      <el-tab-pane label="积分兑换选项" name="pointExchange">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="小程序会员中心积分兑券"
          description="后台可同时维护总部与门店选项；顾客端按「券模板+积分成本」合并，门店覆盖总部，避免重复卡片。保存门店覆盖档时会提示。"
          class="milestone-alert"
        />
        <div class="toolbar" style="margin-top:12px">
          <el-select
            v-if="auth.isSuperAdmin"
            v-model="exchangeScope"
            placeholder="维护范围"
            style="width: 220px"
            @change="loadPointExchange"
          >
            <el-option label="总部（全店）" value="HQ" />
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="String(s.id)" />
          </el-select>
          <el-button @click="loadPointExchange">刷新</el-button>
          <el-button v-if="canManage" @click="addExchangeRow">添加</el-button>
          <el-button v-if="canManage" type="primary" :loading="exchangeSaving" @click="savePointExchange">保存</el-button>
        </div>
        <el-alert
          v-if="exchangeHqHint.length"
          type="warning"
          :closable="false"
          show-icon
          class="milestone-alert"
          style="margin-top:8px"
          :title="`总部另有 ${exchangeHqHint.length} 项（只读），同模板同积分成本将被本店覆盖`"
          :description="exchangeHqHint.map((x) => `${x.name} / ${x.points_cost}积分 / ${x.template_name || '模板#' + x.template_id}`).join('；')"
        />
        <el-table :data="exchangeRows" border style="margin-top:12px">
          <el-table-column label="选项名称" min-width="160">
            <template #default="{ row }">
              <el-input v-model="row.name" maxlength="100" :disabled="!canManage" placeholder="如：兑10元券" />
            </template>
          </el-table-column>
          <el-table-column label="券模板" min-width="220">
            <template #default="{ row }">
              <el-select v-model="row.template_id" filterable placeholder="选择模板" :disabled="!canManage" style="width: 100%">
                <el-option
                  v-for="t in enabledTemplates"
                  :key="t.id"
                  :label="`${t.name}（${t.summary || couponTypeLabel(t.coupon_type)}）`"
                  :value="t.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="所需积分" width="140">
            <template #default="{ row }">
              <el-input-number v-model="row.points_cost" :min="1" :max="999999" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column label="排序" width="120">
            <template #default="{ row }">
              <el-input-number v-model="row.sort_order" :min="0" :max="9999" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column label="启用" width="90">
            <template #default="{ row }">
              <el-switch v-model="row.status" :active-value="1" :inactive-value="0" :disabled="!canManage" />
            </template>
          </el-table-column>
          <el-table-column v-if="canManage" label="操作" width="90">
            <template #default="{ $index }">
              <el-button link type="danger" @click="exchangeRows.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p class="hint" style="margin-top:8px">整表替换：清空后保存即删除当前范围（总部或本店）全部兑换选项。</p>
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
        <el-form-item v-if="!templateForm.id && auth.isSuperAdmin && templateForm.scope === 'STORE'" label="门店"
          required>
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
          required>
          <el-input-number v-model="templateForm.discount_value"
            :min="templateForm.coupon_type === 'PERCENT' ? 0.01 : 0.01"
            :max="templateForm.coupon_type === 'PERCENT' ? 0.99 : 9999"
            :step="templateForm.coupon_type === 'PERCENT' ? 0.01 : 1"
            :precision="templateForm.coupon_type === 'PERCENT' ? 2 : 2" style="width: 100%" />
          <div class="hint">
            {{
              templateForm.coupon_type === 'PERCENT'
                ? '如 0.90 表示 9 折。与会员折互斥：力度相同禁用券；力度更优则取折扣券（不折上折）'
                : templateForm.coupon_type === 'VOUCHER'
                  ? '代金面额（元），打完折扣后再抵扣（可与优惠券叠加）'
                  : '固定减免金额（元，按会员折后餐品小计）'
            }}
          </div>
        </el-form-item>
        <el-form-item label="使用门槛">
          <el-input-number v-model="templateForm.min_order_amount" :min="0" :precision="2" style="width: 100%" />
          <div class="hint">按可参与优惠的餐品小计计算（PERCENT 取优时为未打会员折基数），0 表示无门槛</div>
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
          <el-select v-model="issueForm.customer_id" filterable remote reserve-keyword placeholder="输入编号/手机/展示名/昵称搜索"
            :remote-method="searchCustomers" :loading="customerSearching" style="width: 100%">
            <el-option v-for="c in customerOptions" :key="c.id"
              :label="`${formatCustomerNameLines(c).primary === '—' ? '顾客' : formatCustomerNameLines(c).primary}（${c.customer_no}）`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="券模板" required>
          <el-select v-model="issueForm.template_id" placeholder="选择模板" style="width: 100%">
            <el-option v-for="t in enabledTemplates" :key="t.id" :label="`${t.name} — ${t.summary}`" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="issueQuantityLabel" required>
          <el-input-number v-model="issueForm.quantity" :min="1" :max="50" :step="1" :disabled="!issueForm.template_id"
            style="width: 100%" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { COUPON_STATUS, COUPON_TYPE, couponStatusTagType, label } from '@/utils/labels'
import { formatCustomerNameLines } from '@/utils/customerName'
import { notifySaveWithOverride } from '@/utils/overrideWarning'

const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('coupon:manage'))
const loading = ref(false)
const saving = ref(false)
const issuing = ref(false)
const activeTab = ref('issued')
const { storeOptions: stores, loadStores: fetchStores } = useStoreOptions()
const milestoneStoreId = ref<number | undefined>()
const milestoneRows = ref<
  { threshold_orders: number; template_id?: number; status: number }[]
>([])
const milestoneSaving = ref(false)

/** 超管：HQ 或门店 id 字符串；门店账号固定本店 */
const exchangeScope = ref<string>('HQ')
const exchangeRows = ref<
  { name: string; template_id?: number; points_cost: number; sort_order: number; status: number }[]
>([])
const exchangeHqHint = ref<any[]>([])
const exchangeSaving = ref(false)

const templates = ref<any[]>([])
const templateScope = ref<string | undefined>()
const templateStoreFilters = computed(() => {
  if (auth.isSuperAdmin && stores.value.length) return stores.value
  const map = new Map<number, string>()
  for (const t of templates.value) {
    if (!t?.store_id) continue
    map.set(Number(t.store_id), t.store_name || `门店#${t.store_id}`)
  }
  return [...map.entries()].map(([id, name]) => ({ id, name }))
})
const filteredTemplates = computed(() => {
  const scope = templateScope.value
  if (!scope) return templates.value
  if (scope === 'HQ') return templates.value.filter((t) => !t.store_id || t.store_scope === 'HQ')
  const sid = Number(scope)
  return templates.value.filter((t) => Number(t.store_id) === sid)
})
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
  await fetchStores()
}

const issued = ref<any[]>([])
const issuedKeyword = ref('')
const issuedStatus = ref<string | undefined>()
const issuedTemplateId = ref<number | undefined>()
const issuedCouponType = ref<string | undefined>()
const issuedStoreId = ref<number | undefined>()
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
  return label(COUPON_STATUS, s)
}
function statusTag(s: string) {
  return couponStatusTagType(s)
}
function couponTypeLabel(t: string) {
  return label(COUPON_TYPE, t)
}

async function loadTemplates() {
  loading.value = true
  try {
    const res = await api.get('/coupons/templates', { params: { include_disabled: true } })
    templates.value = res.data?.items || []
  } finally {
    loading.value = false
  }
}

function issuedStoreName(storeId?: number | null) {
  if (!storeId) return '不限'
  return stores.value.find((s) => s.id === storeId)?.name || `门店#${storeId}`
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
        coupon_type: issuedCouponType.value || undefined,
        issued_store_id: auth.isSuperAdmin ? issuedStoreId.value || undefined : undefined,
      },
    })
    const list = res.data?.items || []
    // 券核销后离开"可用"集合，末页可能变空，自动回退避免空白页
    if (list.length === 0 && issuedPage.value > 1) {
      issuedPage.value -= 1
      loading.value = false
      await loadIssued()
      return
    }
    issued.value = list
    issuedTotal.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'issued' && !issued.value.length) loadIssued()
  if (name === 'milestones') loadMilestones()
  if (name === 'pointExchange') loadPointExchange()
}

/** null=总部；number=门店；门店账号恒为本店 */
function exchangeTargetStoreId(): number | null {
  if (!auth.isSuperAdmin) {
    const sid = Number(auth.user?.store_id)
    return Number.isFinite(sid) && sid > 0 ? sid : null
  }
  if (exchangeScope.value === 'HQ') return null
  const sid = Number(exchangeScope.value)
  return Number.isFinite(sid) && sid > 0 ? sid : null
}

function mapExchangeRows(items: any[]) {
  return items.map((x: any) => ({
    name: x.name || '',
    template_id: Number(x.template_id),
    points_cost: Number(x.points_cost) || 1,
    sort_order: Number(x.sort_order || 0),
    status: Number(x.status ?? 1),
  }))
}

async function loadPointExchange() {
  if (auth.isSuperAdmin && !stores.value.length) await loadStores()
  if (auth.isSuperAdmin && exchangeScope.value !== 'HQ' && !exchangeScope.value) {
    exchangeScope.value = 'HQ'
  }
  loading.value = true
  try {
    const target = exchangeTargetStoreId()
    const params: Record<string, number> = {}
    // 超管指定门店时传 store_id；门店账号后端按绑定店返回 HQ∪本店
    if (auth.isSuperAdmin && target != null) params.store_id = target
    const res = await api.get('/coupons/point-exchange-options', { params })
    const items = res.data?.items || []
    if (target != null) {
      exchangeHqHint.value = items.filter((x: any) => !x.store_id)
      exchangeRows.value = mapExchangeRows(
        items.filter((x: any) => Number(x.store_id) === Number(target)),
      )
    } else {
      exchangeHqHint.value = []
      exchangeRows.value = mapExchangeRows(items.filter((x: any) => !x.store_id))
    }
  } catch (e: any) {
    ElMessage.error(e.message || '加载积分兑换选项失败')
    exchangeRows.value = []
    exchangeHqHint.value = []
  } finally {
    loading.value = false
  }
}

function addExchangeRow() {
  exchangeRows.value.push({
    name: '',
    template_id: enabledTemplates.value[0]?.id,
    points_cost: 100,
    sort_order: exchangeRows.value.length,
    status: 1,
  })
}

async function savePointExchange() {
  const target = exchangeTargetStoreId()
  if (auth.isSuperAdmin && exchangeScope.value !== 'HQ' && target == null) {
    ElMessage.warning('请选择门店')
    return
  }
  if (!auth.isSuperAdmin && target == null) {
    ElMessage.warning('账号未绑定门店，无法保存')
    return
  }
  for (const row of exchangeRows.value) {
    if (!row.name.trim()) {
      ElMessage.warning('请填写选项名称')
      return
    }
    if (!row.template_id) {
      ElMessage.warning('请为每个选项选择券模板')
      return
    }
  }
  if (!exchangeRows.value.length) {
    try {
      await ElMessageBox.confirm(
        target == null
          ? '当前无任何选项，保存后将清空总部全部积分兑换选项。是否继续？'
          : '当前无任何选项，保存后将清空本店全部积分兑换选项（总部项保留）。是否继续？',
        '清空确认',
        { type: 'warning', confirmButtonText: '清空并保存', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  exchangeSaving.value = true
  try {
    const body: Record<string, unknown> = {
      items: exchangeRows.value.map((r) => ({
        name: r.name.trim(),
        template_id: r.template_id,
        points_cost: r.points_cost,
        status: r.status,
        sort_order: r.sort_order,
      })),
    }
    // 超管显式传 store_id（null=总部）；门店账号由后端按绑定店写入
    if (auth.isSuperAdmin) {
      body.store_id = target
    }
    const res: any = await api.put('/coupons/point-exchange-options', body)
    notifySaveWithOverride(
      res,
      exchangeRows.value.length ? '积分兑换选项已保存' : '已清空兑换选项',
    )
    await loadPointExchange()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    exchangeSaving.value = false
  }
}

async function loadMilestones() {
  if (auth.isSuperAdmin && !milestoneStoreId.value) {
    if (!stores.value.length) await loadStores()
    milestoneStoreId.value = stores.value[0]?.id
  }
  if (auth.isSuperAdmin && !milestoneStoreId.value) {
    milestoneRows.value = []
    return
  }
  loading.value = true
  try {
    const res = await api.get('/coupons/milestones', {
      params: auth.isSuperAdmin ? { store_id: milestoneStoreId.value } : undefined,
    })
    milestoneRows.value = (res.data?.items || []).map((x: any) => ({
      threshold_orders: Number(x.threshold_orders),
      template_id: Number(x.template_id),
      status: Number(x.status ?? 1),
    }))
  } catch (e: any) {
    ElMessage.error(e.message || '加载里程碑失败')
    milestoneRows.value = []
  } finally {
    loading.value = false
  }
}

function addMilestoneRow() {
  const defaults = [20, 50, 100]
  const used = new Set(milestoneRows.value.map((r) => r.threshold_orders))
  const next = defaults.find((n) => !used.has(n)) || Math.max(0, ...used, 0) + 10
  milestoneRows.value.push({ threshold_orders: next, template_id: undefined, status: 1 })
}

async function saveMilestones() {
  if (auth.isSuperAdmin && !milestoneStoreId.value) {
    ElMessage.warning('请选择门店')
    return
  }
  if (!milestoneRows.value.length) {
    try {
      await ElMessageBox.confirm(
        '当前无任何档位，保存后将清空本店全部成单里程碑规则。是否继续？',
        '清空确认',
        { type: 'warning', confirmButtonText: '清空并保存', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  } else {
    for (const row of milestoneRows.value) {
      if (!row.template_id) {
        ElMessage.warning('请为每个档位选择券模板')
        return
      }
    }
  }
  milestoneSaving.value = true
  try {
    await api.put('/coupons/milestones', {
      store_id: auth.isSuperAdmin ? milestoneStoreId.value : undefined,
      items: milestoneRows.value.map((r) => ({
        threshold_orders: r.threshold_orders,
        template_id: r.template_id,
        status: r.status,
      })),
    })
    ElMessage.success(milestoneRows.value.length ? '里程碑规则已保存' : '已清空本店里程碑规则')
    await loadMilestones()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    milestoneSaving.value = false
  }
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
    const res = templateForm.id
      ? await api.put(`/coupons/templates/${templateForm.id}`, payload)
      : await api.post('/coupons/templates', payload)
    notifySaveWithOverride(res, '保存成功')
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
  await loadIssued()
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

.milestone-alert {
  margin-bottom: 4px;
}

.coupon-tip {
  margin-bottom: 12px;
}
</style>
