<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="reload"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-input
          v-model="keyword"
          placeholder="昵称 / 手机 / 编号"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
      </el-form-item>
      <el-form-item>
        <el-select v-model="channel" placeholder="渠道" clearable style="width: 120px" @change="reload">
          <el-option label="微信" value="WECHAT" />
          <el-option label="支付宝" value="ALIPAY" />
        </el-select>
      </el-form-item>
      <el-button type="primary" @click="reload">搜索</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <el-button @click="goMemberLevels">会员等级设置</el-button>
    </el-form>

    <el-table :data="customers" border class="customer-table">
      <el-table-column label="头像" width="72" align="center">
        <template #default="{ row }">
          <el-image
            v-if="row.avatar_url"
            :src="mediaUrl(row.avatar_url)"
            fit="cover"
            class="avatar-thumb"
            :preview-src-list="[mediaUrl(row.avatar_url)]"
            preview-teleported
          />
          <span v-else class="no-avatar">无</span>
        </template>
      </el-table-column>
      <el-table-column prop="customer_no" label="编号" min-width="260" show-overflow-tooltip />
      <el-table-column label="昵称" min-width="100">
        <template #default="{ row }">{{ row.display_name || row.nickname || '—' }}</template>
      </el-table-column>
      <el-table-column label="注册门店" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.registered_store_name">
            {{ row.registered_store_name }}
            <span v-if="row.registered_table_name" class="muted"> · {{ row.registered_table_name }}</span>
          </span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="phone" label="手机" width="120">
        <template #default="{ row }">{{ row.phone || '—' }}</template>
      </el-table-column>
      <el-table-column label="会员" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.is_member ? 'success' : 'info'">
            {{ row.is_member ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="会员等级" width="110">
        <template #default="{ row }">{{ row.member_level_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="积分" width="80">
        <template #default="{ row }">{{ row.member_points ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="折扣" width="80">
        <template #default="{ row }">
          {{
            row.member_discount_rate != null && Number(row.member_discount_rate) < 1
              ? `${(Number(row.member_discount_rate) * 10).toFixed(1)}折`
              : (row.is_member ? '无折扣' : '—')
          }}
        </template>
      </el-table-column>
      <el-table-column label="渠道" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.channels?.includes('WECHAT')" size="small" type="success" style="margin-right:4px">微信</el-tag>
          <el-tag v-if="row.channels?.includes('ALIPAY')" size="small" type="warning">支付宝</el-tag>
          <span v-if="!row.channels?.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="人脸" width="70">
        <template #default="{ row }">
          <el-tag size="small" :type="row.has_face ? 'success' : 'info'">{{ row.has_face ? '已开' : '未开' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="智能口味" min-width="380" class-name="pref-col">
        <template #default="{ row }">
          <div
            v-if="row.preferences?.length"
            class="pref-tags"
            :title="row.preferences.map((p: any) => p.label).join('、')"
          >
            <el-tag
              v-for="(p, i) in row.preferences"
              :key="`${p.option_code || i}-${i}`"
              size="small"
              type="danger"
              effect="plain"
              class="pref-tag"
            >{{ p.label }}</el-tag>
            <span class="pref-count">共{{ row.preferences.length }}项</span>
          </div>
          <span v-else class="muted">待累计</span>
        </template>
      </el-table-column>
      <el-table-column prop="order_count" label="订单" width="70" />
      <el-table-column prop="last_order_at" label="最近下单" width="160">
        <template #default="{ row }">{{ row.last_order_at || '—' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="注册时间" width="160">
        <template #default="{ row }">{{ row.created_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row.id)">详情</el-button>
          <el-button v-if="canIssueCoupon" link type="success" @click="openIssueDialog(row)">发券</el-button>
          <el-button v-if="canMergeCustomer" link type="warning" @click="openMergeDialog(row)">合并入</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
    />

    <el-drawer v-model="drawer" title="顾客详情 · 会员与口味档案" size="560px">
      <template v-if="detail">
        <div class="detail-head">
          <el-image
            v-if="detail.avatar_url"
            :src="mediaUrl(detail.avatar_url)"
            fit="cover"
            class="avatar-lg"
            :preview-src-list="[mediaUrl(detail.avatar_url)]"
            preview-teleported
          />
          <div v-else class="avatar-lg placeholder">无头像</div>
          <div class="detail-head-meta">
            <div class="detail-name">{{ detail.display_name || detail.nickname || '—' }}</div>
            <div class="muted">{{ detail.customer_no }}</div>
          </div>
        </div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="编号">{{ detail.customer_no }}</el-descriptions-item>
          <el-descriptions-item label="昵称">{{ detail.display_name || detail.nickname || '—' }}</el-descriptions-item>
          <el-descriptions-item label="注册门店">
            <template v-if="detail.registered_store_name">
              {{ detail.registered_store_name }}
              <span v-if="detail.registered_table_name"> · {{ detail.registered_table_name }}</span>
              <span v-if="detail.registered_table_no" class="muted">（{{ detail.registered_table_no }}）</span>
            </template>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="手机">{{ detail.phone || '—' }}</el-descriptions-item>
          <el-descriptions-item label="是否会员">
            <el-tag size="small" :type="detail.is_member ? 'success' : 'info'">
              {{ detail.is_member ? '已注册会员' : '未注册/游客' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="会员等级">{{ detail.member_level_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="当前积分">{{ detail.member_points ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="会员折扣">
            {{
              detail.member_discount_rate != null && Number(detail.member_discount_rate) < 1
                ? `${(Number(detail.member_discount_rate) * 10).toFixed(1)}折`
                : (detail.is_member ? '无折扣' : '—')
            }}
          </el-descriptions-item>
          <el-descriptions-item label="等级积分门槛">
            {{ detail.member_min_points != null ? detail.member_min_points : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="订单数">{{ detail.order_count }}</el-descriptions-item>
          <el-descriptions-item label="人脸迎宾">{{ detail.has_face ? '已授权（到店摄像头识别）' : '未开启' }}</el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ detail.created_at || '—' }}</el-descriptions-item>
          <el-descriptions-item label="最近下单">{{ detail.last_order_at || '—' }}</el-descriptions-item>
        </el-descriptions>

        <div class="pref-block">
          <div class="section-head">
            <h4>优惠券</h4>
            <el-button v-if="canIssueCoupon" type="success" size="small" @click="openIssueDialog(detail)">手动发券</el-button>
          </div>
          <p v-if="couponsLoading" class="muted">加载中…</p>
          <el-table v-else-if="customerCoupons.length" :data="customerCoupons" size="small" border>
            <el-table-column prop="template_name" label="名称" min-width="100" />
            <el-table-column prop="summary" label="规则" min-width="110" />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="couponStatusTag(row.status)">{{ couponStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="过期" width="100">
              <template #default="{ row }">{{ formatDate(row.expires_at) }}</template>
            </el-table-column>
          </el-table>
          <p v-else class="muted">暂无优惠券</p>

          <h4 style="margin-top:16px">代金券</h4>
          <p v-if="couponsLoading" class="muted">加载中…</p>
          <el-table v-else-if="customerVouchers.length" :data="customerVouchers" size="small" border>
            <el-table-column prop="template_name" label="名称" min-width="100" />
            <el-table-column prop="summary" label="规则" min-width="110" />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="couponStatusTag(row.status)">{{ couponStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="过期" width="100">
              <template #default="{ row }">{{ formatDate(row.expires_at) }}</template>
            </el-table-column>
          </el-table>
          <p v-else class="muted">暂无代金券</p>
        </div>

        <div class="pref-block">
          <h4>智能记录的口味爱好</h4>
          <p class="muted tip">完成订单后自动累计规格偏好；人脸迎宾会播报「今天还是老样子」。</p>
          <el-alert
            v-if="detail.welcome_hint"
            :title="detail.welcome_hint"
            type="success"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
          />
          <el-table v-if="detail.preferences?.length" :data="detail.preferences" size="small" border>
            <el-table-column prop="category" label="类别" width="100" />
            <el-table-column label="口味" min-width="120">
              <template #default="{ row }">{{ row.option_name || row.option_code }}</template>
            </el-table-column>
            <el-table-column label="频次" width="70">
              <template #default="{ row }">{{ row.order_count }}次</template>
            </el-table-column>
            <el-table-column label="偏好度" width="90">
              <template #default="{ row }">{{ formatScore(row.score) }}</template>
            </el-table-column>
          </el-table>
          <p v-else class="muted">暂无偏好（需顾客完成至少 1 笔带规格的订单）</p>
        </div>

        <h4 style="margin-top:16px">微信账号</h4>
        <p v-if="!detail.wechat_accounts?.length" class="muted">无</p>
        <ul v-else class="acct">
          <li v-for="(w, i) in detail.wechat_accounts" :key="'w'+i" class="acct-row">
            <el-image
              v-if="w.avatar_url"
              :src="mediaUrl(w.avatar_url)"
              fit="cover"
              class="avatar-sm"
              :preview-src-list="[mediaUrl(w.avatar_url)]"
              preview-teleported
            />
            <div class="acct-meta">
              <div v-if="w.nickname">{{ w.nickname }}</div>
              <code>{{ w.openid }}</code>
              <el-tag v-if="w.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
            </div>
          </li>
        </ul>

        <h4>支付宝账号</h4>
        <p v-if="!detail.alipay_accounts?.length" class="muted">无</p>
        <ul v-else class="acct">
          <li v-for="(a, i) in detail.alipay_accounts" :key="'a'+i" class="acct-row">
            <el-image
              v-if="a.avatar_url"
              :src="mediaUrl(a.avatar_url)"
              fit="cover"
              class="avatar-sm"
              :preview-src-list="[mediaUrl(a.avatar_url)]"
              preview-teleported
            />
            <div class="acct-meta">
              <div v-if="a.nickname">{{ a.nickname }}</div>
              <code>{{ a.alipay_user_id }}</code>
              <el-tag v-if="a.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
            </div>
          </li>
        </ul>
      </template>
    </el-drawer>

    <el-dialog v-model="issueDialog" title="手动发券" width="480px" destroy-on-close>
      <el-form :model="issueForm" label-width="90px">
        <el-form-item label="顾客">
          <span>{{ issueCustomerLabel }}</span>
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
          <el-input-number v-model="issueForm.quantity" :min="1" :max="50" :step="1" style="width: 100%" />
          <div class="hint">单次最多发放 50 张，适用于优惠券与代金券</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="issueDialog = false">取消</el-button>
        <el-button type="primary" :loading="issuing" @click="submitIssue">发放</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mergeDialog" title="合并顾客到当前档案" width="480px" destroy-on-close>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="不可逆操作"
        description="将源顾客的账号、订单、券、积分等迁入目标顾客，源顾客停用。请核对 ID。"
        style="margin-bottom: 12px"
      />
      <el-form label-width="100px">
        <el-form-item label="目标顾客">
          <span>{{ mergeTargetLabel }}</span>
        </el-form-item>
        <el-form-item label="源顾客 ID" required>
          <el-input-number v-model="mergeSourceId" :min="1" :controls="false" style="width: 100%" placeholder="被合并的顾客主键" />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="mergeReason" maxlength="255" show-word-limit placeholder="如：同人双号合并" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeDialog = false">取消</el-button>
        <el-button type="warning" :loading="merging" @click="submitMerge">确认合并</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { mediaUrl } from '@/utils/media'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const customers = ref<any[]>([])
const stores = ref<any[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const channel = ref<string | undefined>()
const storeFilter = ref<number | undefined>()
const drawer = ref(false)
const detail = ref<any>(null)
const customerCoupons = ref<any[]>([])
const customerVouchers = ref<any[]>([])
const couponsLoading = ref(false)
const canIssueCoupon = computed(() => auth.hasPermission('coupon:manage'))
const canMergeCustomer = computed(
  () => auth.isSuperAdmin || auth.hasPermission('customer:write'),
)

const couponTemplates = ref<any[]>([])
const issueDialog = ref(false)
const issueTarget = ref<any>(null)
const issuing = ref(false)
const issueForm = reactive({
  template_id: undefined as number | undefined,
  quantity: 1,
})

const mergeDialog = ref(false)
const mergeTarget = ref<any>(null)
const mergeSourceId = ref<number | undefined>()
const mergeReason = ref('')
const merging = ref(false)

const mergeTargetLabel = computed(() => {
  const c = mergeTarget.value
  if (!c) return '—'
  const name = c.display_name || c.nickname || '顾客'
  return `${name}（ID ${c.id} / ${c.customer_no}）`
})

const enabledTemplates = computed(() => couponTemplates.value.filter((t) => t.status === 1))

const issueCustomerLabel = computed(() => {
  const c = issueTarget.value
  if (!c) return '—'
  const name = c.display_name || c.nickname || '顾客'
  return `${name}（${c.customer_no}）`
})

const selectedIssueTemplate = computed(() =>
  enabledTemplates.value.find((t) => t.id === issueForm.template_id),
)

const issueQuantityLabel = computed(() => {
  const t = selectedIssueTemplate.value
  if (!t) return '发放张数'
  return t.coupon_type === 'VOUCHER' ? '代金券张数' : '优惠券张数'
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

function formatScore(s: number) {
  if (s == null) return '—'
  return `${Math.round(Number(s) * 100)}%`
}

function formatDate(iso?: string | null) {
  if (!iso) return '—'
  return String(iso).slice(0, 10)
}

function couponStatusLabel(s: string) {
  return { AVAILABLE: '可用', USED: '已使用', EXPIRED: '已过期' }[s] || s
}

function couponStatusTag(s: string) {
  return { AVAILABLE: 'success', USED: 'info', EXPIRED: 'warning' }[s] || 'info'
}

function reload() {
  page.value = 1
  load()
}

function resetFilters() {
  storeFilter.value = undefined
  keyword.value = ''
  channel.value = undefined
  reload()
}

function goMemberLevels() {
  router.push('/member-levels')
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
    const res = await api.get('/customers', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        keyword: keyword.value.trim() || undefined,
        channel: channel.value || undefined,
        store_id: storeFilter.value,
      },
    })
    customers.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function loadCustomerCoupons(customerId: number) {
  couponsLoading.value = true
  customerCoupons.value = []
  customerVouchers.value = []
  try {
    const res = await api.get('/coupons/issued', {
      params: { customer_id: customerId, page: 1, page_size: 100 },
    })
    const items = res.data?.items || []
    customerCoupons.value = items.filter((x: any) => x.coupon_type !== 'VOUCHER')
    customerVouchers.value = items.filter((x: any) => x.coupon_type === 'VOUCHER')
  } catch {
    customerCoupons.value = []
    customerVouchers.value = []
  } finally {
    couponsLoading.value = false
  }
}

async function showDetail(id: number) {
  drawer.value = true
  detail.value = null
  customerCoupons.value = []
  customerVouchers.value = []
  const [detailRes] = await Promise.all([
    api.get(`/customers/${id}`),
    loadCustomerCoupons(id),
  ])
  detail.value = detailRes.data
}

async function loadCouponTemplates() {
  if (couponTemplates.value.length) return
  try {
    const res = await api.get('/coupons/templates', { params: { all: true } })
    couponTemplates.value = res.data?.items || []
  } catch {
    couponTemplates.value = []
  }
}

async function openIssueDialog(customer: any) {
  if (!customer?.id) return
  await loadCouponTemplates()
  issueTarget.value = customer
  issueForm.template_id = enabledTemplates.value[0]?.id
  issueForm.quantity = 1
  issueDialog.value = true
}

async function submitIssue() {
  const customerId = issueTarget.value?.id
  if (!customerId || !issueForm.template_id) {
    ElMessage.warning('请选择券模板')
    return
  }
  const quantity = Math.max(1, Math.min(50, Number(issueForm.quantity) || 1))
  issuing.value = true
  try {
    const res = (await api.post('/coupons/issue', {
      customer_id: customerId,
      template_id: issueForm.template_id,
      quantity,
    })) as { message?: string }
    ElMessage.success(res.message || `已发放 ${quantity} 张`)
    issueDialog.value = false
    if (drawer.value && detail.value?.id === customerId) {
      await loadCustomerCoupons(customerId)
    }
  } catch (e: any) {
    ElMessage.error(e.message || '发券失败')
  } finally {
    issuing.value = false
  }
}

function openMergeDialog(customer: any) {
  if (!customer?.id) return
  mergeTarget.value = customer
  mergeSourceId.value = undefined
  mergeReason.value = ''
  mergeDialog.value = true
}

async function submitMerge() {
  const targetId = mergeTarget.value?.id
  const sourceId = Number(mergeSourceId.value)
  if (!targetId || !sourceId) {
    ElMessage.warning('请填写源顾客 ID')
    return
  }
  if (sourceId === Number(targetId)) {
    ElMessage.warning('源与目标不能相同')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认将顾客 #${sourceId} 合并入 #${targetId}？此操作不可逆。`,
      '确认合并',
      { type: 'warning', confirmButtonText: '合并', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  merging.value = true
  try {
    await api.post(`/customers/${targetId}/merge`, {
      source_customer_id: sourceId,
      reason: mergeReason.value || undefined,
    })
    ElMessage.success('合并成功')
    mergeDialog.value = false
    await load()
    if (drawer.value && detail.value?.id === targetId) {
      await showDetail(targetId)
    }
  } catch (e: any) {
    ElMessage.error(e.message || '合并失败')
  } finally {
    merging.value = false
  }
}

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.muted { color: #999; }
.tip { font-size: 13px; margin: 4px 0 10px; }
.avatar-thumb {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: block;
  margin: 0 auto;
}
.avatar-sm {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}
.avatar-lg {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  flex-shrink: 0;
}
.avatar-lg.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f2f3f5;
  color: #909399;
  font-size: 12px;
}
.no-avatar { color: #c0c4cc; font-size: 12px; }
.detail-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
}
.detail-head-meta { min-width: 0; }
.detail-name {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.3;
  word-break: break-word;
}
.pref-block { margin-top: 18px; }
.pref-block h4 { margin: 0; }
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.hint { color: #999; font-size: 12px; margin-top: 4px; }
.acct { padding-left: 0; list-style: none; word-break: break-all; }
.acct-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.acct-meta { min-width: 0; }
.acct code { font-size: 12px; }
.pref-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  align-items: center;
  max-width: 100%;
}
.pref-tag {
  max-width: 100%;
  height: auto !important;
  white-space: normal !important;
  line-height: 1.35 !important;
  padding: 2px 6px !important;
}
.pref-count {
  color: #909399;
  font-size: 12px;
  white-space: nowrap;
}
:deep(.customer-table .pref-col .cell) {
  white-space: normal !important;
  word-break: break-word;
  line-height: 1.5;
  overflow: visible !important;
  text-overflow: clip !important;
}
:deep(.customer-table .pref-col) {
  vertical-align: top;
}
</style>
