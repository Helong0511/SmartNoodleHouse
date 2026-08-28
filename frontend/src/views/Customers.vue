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
    </el-form>

    <el-table :data="customers" border>
      <el-table-column prop="customer_no" label="编号" min-width="260" show-overflow-tooltip />
      <el-table-column label="昵称" min-width="100">
        <template #default="{ row }">{{ row.display_name || row.nickname || '—' }}</template>
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
      <el-table-column label="智能口味" min-width="200">
        <template #default="{ row }">
          <template v-if="row.preferences?.length">
            <el-tag
              v-for="(p, i) in row.preferences"
              :key="i"
              size="small"
              type="danger"
              effect="plain"
              style="margin:2px"
            >{{ p.label }}</el-tag>
          </template>
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
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row.id)">详情</el-button>
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
        <el-descriptions :column="1" border>
          <el-descriptions-item label="编号">{{ detail.customer_no }}</el-descriptions-item>
          <el-descriptions-item label="昵称">{{ detail.display_name || detail.nickname || '—' }}</el-descriptions-item>
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
          <h4>优惠券</h4>
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
          <li v-for="(w, i) in detail.wechat_accounts" :key="'w'+i">
            <code>{{ w.openid }}</code>
            <el-tag v-if="w.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
          </li>
        </ul>

        <h4>支付宝账号</h4>
        <p v-if="!detail.alipay_accounts?.length" class="muted">无</p>
        <ul v-else class="acct">
          <li v-for="(a, i) in detail.alipay_accounts" :key="'a'+i">
            <code>{{ a.alipay_user_id }}</code>
            <el-tag v-if="a.is_mock" size="small" type="info" style="margin-left:6px">开发 Mock</el-tag>
          </li>
        </ul>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'

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

onMounted(async () => {
  await loadStores()
  await load()
})
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.muted { color: #999; }
.tip { font-size: 13px; margin: 4px 0 10px; }
.pref-block { margin-top: 18px; }
.pref-block h4 { margin: 0 0 4px; }
.acct { padding-left: 18px; word-break: break-all; }
.acct code { font-size: 12px; }
</style>
