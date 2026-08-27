<template>
  <div class="delivery-page" v-loading="pageLoading">
    <!-- KPI -->
    <div class="kpi-row">
      <div
        v-for="k in kpiCards"
        :key="k.key"
        class="kpi-card"
        :class="[{ active: quickFilter === k.filter, alert: k.alert && Number(k.value) > 0 }, k.tone]"
        @click="applyQuick(k.filter)"
      >
        <div class="kpi-val">{{ k.value }}</div>
        <div class="kpi-label">{{ k.label }}</div>
      </div>
      <div class="kpi-meta">
        <div class="conn" :class="connStatus">{{ connLabel }}</div>
        <el-button size="small" @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <!-- 订单 -->
      <el-tab-pane name="orders">
        <template #label>
          外卖订单
          <el-badge v-if="stats.pending" :value="stats.pending" class="tab-badge" />
        </template>

        <div class="toolbar">
          <el-select v-model="filters.platform" placeholder="平台" clearable style="width: 110px" @change="reloadOrders">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
          <el-select v-model="filters.delivery_status" placeholder="接单状态" clearable style="width: 130px" @change="reloadOrders">
            <el-option v-for="(t, v) in statusOptions" :key="v" :label="t" :value="v" />
          </el-select>
          <el-select v-model="filters.refund_status" placeholder="退款" clearable style="width: 110px" @change="reloadOrders">
            <el-option label="无退款" value="NONE" />
            <el-option label="待处理" value="PENDING" />
            <el-option label="已同意" value="APPROVED" />
            <el-option label="已拒绝" value="REJECTED" />
          </el-select>
          <el-input
            v-model="filters.keyword"
            clearable
            placeholder="单号 / 姓名 / 电话 / 地址"
            style="width: 220px"
            @keyup.enter="reloadOrders"
            @clear="reloadOrders"
          />
          <el-input
            v-model="filters.day_seq"
            clearable
            placeholder="流水#"
            style="width: 90px"
            @keyup.enter="reloadOrders"
          />
          <el-date-picker
            v-model="filters.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
            style="width: 240px"
            @change="reloadOrders"
          />
          <el-checkbox v-model="filters.only_urge" @change="reloadOrders">仅催单</el-checkbox>
          <el-button type="primary" @click="reloadOrders">查询</el-button>
        </div>

        <el-table
          :data="orders"
          size="small"
          stripe
          highlight-current-row
          :row-class-name="orderRowClass"
          @row-click="(row: any) => openDetail(row)"
        >
          <el-table-column label="平台" width="72">
            <template #default="{ row }">
              <el-tag size="small" :type="row.platform === 'MEITUAN' ? 'warning' : 'success'" effect="plain">
                {{ platformName(row.platform) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="platform_day_seq" label="#" width="56" />
          <el-table-column label="金额" width="88">
            <template #default="{ row }">
              <span class="money">¥{{ row.total_amount || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="接单" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="deliveryStatusTagType(row.delivery_status)">
                {{ label(DELIVERY_STATUS, row.delivery_status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="物流" width="100">
            <template #default="{ row }">
              {{ label(LOGISTICS_STATUS, row.logistics_status, row.logistics_status || '-') }}
            </template>
          </el-table-column>
          <el-table-column label="退款" width="80">
            <template #default="{ row }">
              <el-tag
                v-if="row.refund_status && row.refund_status !== 'NONE'"
                size="small"
                :type="row.refund_status === 'PENDING' ? 'danger' : 'info'"
              >
                {{ label(DELIVERY_REFUND_STATUS, row.refund_status) }}
              </el-tag>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="催" width="50">
            <template #default="{ row }">
              <el-badge v-if="row.urge_count > 0" :value="row.urge_count" type="danger" />
              <span v-else class="muted">0</span>
            </template>
          </el-table-column>
          <el-table-column prop="recipient_name" label="顾客" width="72" show-overflow-tooltip />
          <el-table-column prop="recipient_phone" label="隐私号" width="120" show-overflow-tooltip />
          <el-table-column prop="recipient_address" label="地址" min-width="160" show-overflow-tooltip />
          <el-table-column label="骑手" width="100" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.rider_name">{{ row.rider_name }}</span>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="下单" width="150">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="openDetail(row)">详情</el-button>
              <el-button
                v-if="row.delivery_status === 'PENDING'"
                link
                type="success"
                size="small"
                :loading="actingId === row.order_id"
                @click.stop="confirmOrder(row)"
              >
                接单
              </el-button>
              <el-button
                v-if="canCancel(row)"
                link
                type="danger"
                size="small"
                @click.stop="askCancel(row)"
              >
                取消
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            v-model:current-page="pager.page"
            v-model:page-size="pager.pageSize"
            :total="pager.total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next"
            background
            small
            @current-change="loadOrders"
            @size-change="reloadOrders"
          />
        </div>
      </el-tab-pane>

      <!-- 退款 -->
      <el-tab-pane name="refunds">
        <template #label>
          退款处理
          <el-badge v-if="stats.refund_pending" :value="stats.refund_pending" class="tab-badge" type="warning" />
        </template>
        <div class="toolbar">
          <el-select v-model="refundFilter" placeholder="状态" clearable style="width: 120px" @change="reloadRefunds">
            <el-option label="待审核" value="PENDING" />
            <el-option label="已同意" value="APPROVED" />
            <el-option label="已拒绝" value="REJECTED" />
          </el-select>
          <el-button @click="reloadRefunds">刷新</el-button>
        </div>
        <el-table :data="refunds" size="small" stripe>
          <el-table-column label="平台" width="72">
            <template #default="{ row }">{{ platformName(row.platform) }}</template>
          </el-table-column>
          <el-table-column prop="platform_day_seq" label="#" width="56" />
          <el-table-column prop="platform_order_id" label="平台单号" min-width="140" show-overflow-tooltip />
          <el-table-column prop="amount" label="金额" width="88">
            <template #default="{ row }"><span class="money">¥{{ row.amount }}</span></template>
          </el-table-column>
          <el-table-column prop="refund_type" label="类型" width="80" />
          <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'PENDING' ? 'danger' : 'info'">
                {{ label(REFUND_APPLY_STATUS, row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="申请时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <template v-if="row.status === 'PENDING'">
                <el-button size="small" type="success" @click="handleRefund(row.id, true)">同意</el-button>
                <el-button size="small" type="danger" @click="askRejectRefund(row)">拒绝</el-button>
              </template>
              <span v-else class="muted">已处理</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination
            v-model:current-page="refundPager.page"
            v-model:page-size="refundPager.pageSize"
            :total="refundPager.total"
            layout="total, prev, pager, next"
            background
            small
            @current-change="loadRefunds"
            @size-change="reloadRefunds"
          />
        </div>
      </el-tab-pane>

      <!-- 绑定 -->
      <el-tab-pane label="门店绑定" name="bindings">
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
          <el-table-column label="状态" width="80">
            <template #default="{ row }">{{ row.status === 1 ? '启用' : '停用' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 映射 -->
      <el-tab-pane label="商品映射" name="maps">
        <div class="toolbar">
          <el-button type="primary" @click="showMapDialog = true">新增映射</el-button>
          <el-select v-model="syncPlatform" style="width: 100px">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
          <el-button @click="syncMenu">同步菜单</el-button>
          <el-button @click="syncPrices">同步价格</el-button>
          <el-button @click="syncStock">同步库存</el-button>
          <el-button @click="loadMaps">刷新</el-button>
        </div>
        <el-table :data="maps" size="small" stripe>
          <el-table-column prop="platform" label="平台" width="80">
            <template #default="{ row }">{{ platformName(row.platform) }}</template>
          </el-table-column>
          <el-table-column prop="platform_sku_id" label="平台SKU" min-width="120" show-overflow-tooltip />
          <el-table-column prop="platform_sku_name" label="平台品名" min-width="120" show-overflow-tooltip />
          <el-table-column prop="product_id" label="本系统ID" width="90" />
          <el-table-column prop="product_name" label="本系统商品" min-width="120" show-overflow-tooltip />
          <el-table-column prop="app_food_code" label="菜品码" width="100" show-overflow-tooltip />
          <el-table-column label="状态" width="70">
            <template #default="{ row }">{{ row.status === 1 ? '启用' : '停用' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 回调 -->
      <el-tab-pane label="回调日志" name="logs">
        <div class="toolbar">
          <el-select v-model="logPlatform" clearable placeholder="平台" style="width: 110px" @change="loadLogs">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
          <el-button @click="loadLogs">刷新</el-button>
        </div>
        <el-table :data="logs" size="small" stripe>
          <el-table-column prop="platform" label="平台" width="80" />
          <el-table-column prop="event_type" label="事件" width="140" show-overflow-tooltip />
          <el-table-column prop="platform_order_id" label="平台单号" min-width="140" show-overflow-tooltip />
          <el-table-column prop="order_id" label="订单ID" width="90" />
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="外卖订单详情" size="480px" destroy-on-close>
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <div class="detail-head">
            <el-tag :type="detail.platform === 'MEITUAN' ? 'warning' : 'success'">{{ platformName(detail.platform) }}</el-tag>
            <span class="seq">#{{ detail.platform_day_seq || '-' }}</span>
            <el-tag :type="deliveryStatusTagType(detail.delivery_status)" size="small">
              {{ label(DELIVERY_STATUS, detail.delivery_status) }}
            </el-tag>
            <el-tag v-if="detail.urge_count > 0" type="danger" size="small">催×{{ detail.urge_count }}</el-tag>
          </div>
          <el-descriptions :column="1" border size="small" class="mt">
            <el-descriptions-item label="平台单号">{{ detail.platform_order_id }}</el-descriptions-item>
            <el-descriptions-item label="本系统单号">{{ detail.order_no }}</el-descriptions-item>
            <el-descriptions-item label="订单金额"><span class="money">¥{{ detail.total_amount }}</span></el-descriptions-item>
            <el-descriptions-item label="配送费">¥{{ detail.delivery_fee }}</el-descriptions-item>
            <el-descriptions-item label="餐盒费">¥{{ detail.packing_fee }}</el-descriptions-item>
            <el-descriptions-item label="物流">
              {{ label(LOGISTICS_STATUS, detail.logistics_status, detail.logistics_status || '-') }}
              <span v-if="detail.logistics_code" class="muted"> ({{ detail.logistics_code }})</span>
            </el-descriptions-item>
            <el-descriptions-item label="顾客">{{ detail.recipient_name }} {{ detail.recipient_phone }}</el-descriptions-item>
            <el-descriptions-item label="地址">{{ detail.recipient_address || '-' }}</el-descriptions-item>
            <el-descriptions-item label="骑手">
              {{ detail.rider_name || '-' }}
              <span v-if="detail.rider_phone"> {{ detail.rider_phone }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="期望送达">{{ fmtTime(detail.expected_delivery_at) }}</el-descriptions-item>
            <el-descriptions-item label="备注">{{ detail.remark || '-' }}</el-descriptions-item>
            <el-descriptions-item label="下单时间">{{ fmtTime(detail.created_at) }}</el-descriptions-item>
          </el-descriptions>

          <h4 class="mt">菜品</h4>
          <div v-for="it in detail.items || []" :key="it.id" class="item-row">
            <div class="item-name">
              {{ it.product_name }} ×{{ it.quantity }}
              <span v-if="it.remark" class="item-remark">（{{ it.remark }}）</span>
            </div>
            <div class="item-opts" v-if="it.options?.length">
              <span v-for="(o, i) in it.options" :key="i">{{ o.group_name }}:{{ o.option_name }} </span>
            </div>
            <div class="item-price">¥{{ it.subtotal }}</div>
          </div>
          <div v-if="!(detail.items || []).length" class="muted">暂无明细</div>

          <div class="detail-actions mt">
            <el-button
              v-if="detail.delivery_status === 'PENDING'"
              type="success"
              :loading="actingId === detail.order_id"
              @click="confirmOrder(detail)"
            >
              接单
            </el-button>
            <el-button v-if="canCancel(detail)" type="danger" plain @click="askCancel(detail)">商家取消</el-button>
          </div>
        </template>
      </div>
    </el-drawer>

    <!-- 取消原因 -->
    <el-dialog v-model="cancelVisible" title="商家取消外卖单" width="420px">
      <el-form label-width="80px">
        <el-form-item label="取消原因" required>
          <el-select v-model="cancelReason" filterable allow-create default-first-option style="width: 100%">
            <el-option v-for="r in cancelReasons" :key="r" :label="r" :value="r" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelVisible = false">关闭</el-button>
        <el-button type="danger" :loading="cancelLoading" @click="doCancel">确认取消</el-button>
      </template>
    </el-dialog>

    <!-- 拒绝退款 -->
    <el-dialog v-model="rejectVisible" title="拒绝退款" width="420px">
      <el-input v-model="rejectReason" type="textarea" :rows="3" placeholder="请填写拒绝原因" />
      <template #footer>
        <el-button @click="rejectVisible = false">关闭</el-button>
        <el-button type="danger" @click="doRejectRefund">确认拒绝</el-button>
      </template>
    </el-dialog>

    <!-- 映射弹窗 -->
    <el-dialog v-model="showMapDialog" title="新增/更新商品映射" width="480px">
      <el-form label-width="110px">
        <el-form-item label="平台">
          <el-select v-model="mapForm.platform" style="width: 100%">
            <el-option label="美团" value="MEITUAN" />
            <el-option label="饿了么" value="ELEME" />
          </el-select>
        </el-form-item>
        <el-form-item label="平台 SKU" required>
          <el-input v-model="mapForm.platform_sku_id" />
        </el-form-item>
        <el-form-item label="平台品名">
          <el-input v-model="mapForm.platform_sku_name" />
        </el-form-item>
        <el-form-item label="本系统商品ID" required>
          <el-input-number v-model="mapForm.product_id" :min="1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="菜品码">
          <el-input v-model="mapForm.app_food_code" placeholder="默认取商品编号" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMapDialog = false">取消</el-button>
        <el-button type="primary" @click="saveMap">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'
import {
  DELIVERY_REFUND_STATUS,
  DELIVERY_STATUS,
  LOGISTICS_STATUS,
  REFUND_APPLY_STATUS,
  deliveryStatusTagType,
  label,
} from '@/utils/labels'

const tab = ref('orders')
const pageLoading = ref(false)
const orders = ref<any[]>([])
const refunds = ref<any[]>([])
const bindings = ref<any[]>([])
const maps = ref<any[]>([])
const logs = ref<any[]>([])
const stats = reactive({
  pending: 0,
  accepted: 0,
  delivering: 0,
  urging: 0,
  refund_pending: 0,
  today_orders: 0,
  today_meituan: 0,
  today_eleme: 0,
  today_revenue: '0',
})

const filters = reactive({
  platform: '' as string,
  delivery_status: '' as string,
  refund_status: '' as string,
  keyword: '',
  day_seq: '',
  only_urge: false,
  dateRange: null as string[] | null,
})
const quickFilter = ref('')
const pager = reactive({ page: 1, pageSize: 20, total: 0 })
const refundFilter = ref('PENDING')
const refundPager = reactive({ page: 1, pageSize: 20, total: 0 })

const statusOptions = {
  PENDING: '待接单',
  ACCEPTED: '已接单',
  READY: '待配送',
  DELIVERING: '配送中',
  DONE: '已完成',
  CANCELLED: '已取消',
}

const kpiCards = computed(() => [
  { key: 'pending', label: '待接单', value: stats.pending, filter: 'pending', alert: true, tone: 'tone-danger' },
  { key: 'urging', label: '催单中', value: stats.urging, filter: 'urge', alert: true, tone: 'tone-warn' },
  { key: 'refund', label: '待退款', value: stats.refund_pending, filter: 'refund', alert: true, tone: 'tone-warn' },
  { key: 'accepted', label: '制作/待送', value: stats.accepted, filter: 'accepted', alert: false, tone: '' },
  { key: 'today', label: '今日单量', value: stats.today_orders, filter: 'today', alert: false, tone: '' },
  {
    key: 'rev',
    label: '今日外卖营收',
    value: `¥${stats.today_revenue}`,
    filter: '',
    alert: false,
    tone: 'tone-ok',
  },
  {
    key: 'plat',
    label: '美团 / 饿了么',
    value: `${stats.today_meituan} / ${stats.today_eleme}`,
    filter: '',
    alert: false,
    tone: '',
  },
])

const connStatus = ref<'online' | 'polling' | 'offline' | 'connecting'>('connecting')
const connLabel = computed(() => {
  const m = { online: '实时在线', polling: '轮询中', offline: '已离线', connecting: '连接中…' }
  return m[connStatus.value]
})

const actingId = ref<number | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<any>(null)

const cancelVisible = ref(false)
const cancelLoading = ref(false)
const cancelReason = ref('商家忙碌，无法接单')
const cancelTarget = ref<any>(null)
const cancelReasons = ['商家忙碌，无法接单', '商品售罄', '超出配送范围', '顾客要求取消', '其他原因']

const rejectVisible = ref(false)
const rejectReason = ref('商品已制作，无法退款')
const rejectId = ref<number | null>(null)

const showMapDialog = ref(false)
const syncPlatform = ref('MEITUAN')
const logPlatform = ref('')
const bindingForm = reactive({
  platform: 'MEITUAN',
  platform_shop_id: '',
  platform_shop_name: '',
  access_token: '',
  refresh_token: '',
})
const mapForm = reactive({
  platform: 'MEITUAN',
  platform_sku_id: '',
  platform_sku_name: '',
  product_id: 0,
  app_food_code: '',
})

let ws: WebSocket | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0

function platformName(p: string) {
  return p === 'MEITUAN' ? '美团' : p === 'ELEME' ? '饿了么' : p || '-'
}

function fmtTime(v?: string | null) {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

function canCancel(row: any) {
  return row && !['DONE', 'COMPLETED', 'CANCELLED'].includes(row.delivery_status)
}

function orderRowClass({ row }: { row: any }) {
  if (row.delivery_status === 'PENDING') return 'row-pending'
  if (row.urge_count > 0) return 'row-urge'
  if (row.refund_status === 'PENDING') return 'row-refund'
  return ''
}

function applyQuick(filter: string) {
  if (!filter) return
  quickFilter.value = filter
  filters.only_urge = false
  filters.delivery_status = ''
  filters.refund_status = ''
  filters.dateRange = null
  if (filter === 'pending') filters.delivery_status = 'PENDING'
  else if (filter === 'accepted') filters.delivery_status = 'ACCEPTED'
  else if (filter === 'urge') filters.only_urge = true
  else if (filter === 'refund') {
    tab.value = 'refunds'
    refundFilter.value = 'PENDING'
    reloadRefunds()
    return
  } else if (filter === 'today') {
    const d = new Date()
    const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    filters.dateRange = [s, s]
  }
  tab.value = 'orders'
  reloadOrders()
}

async function loadStats() {
  try {
    const res = await api.get('/delivery/stats')
    Object.assign(stats, res.data || {})
  } catch {
    /* */
  }
}

async function loadOrders() {
  const params: Record<string, any> = {
    page: pager.page,
    page_size: pager.pageSize,
  }
  if (filters.platform) params.platform = filters.platform
  if (filters.delivery_status) params.delivery_status = filters.delivery_status
  if (filters.refund_status) params.refund_status = filters.refund_status
  if (filters.keyword?.trim()) params.keyword = filters.keyword.trim()
  if (filters.day_seq?.trim()) params.day_seq = filters.day_seq.trim()
  if (filters.only_urge) params.only_urge = true
  if (filters.dateRange?.length === 2) {
    params.date_from = filters.dateRange[0]
    params.date_to = filters.dateRange[1]
  }
  const res = await api.get('/delivery/orders', { params })
  orders.value = res.data?.items || []
  pager.total = res.data?.total || 0
}

function reloadOrders() {
  pager.page = 1
  return loadOrders()
}

async function loadRefunds() {
  const params: Record<string, any> = {
    page: refundPager.page,
    page_size: refundPager.pageSize,
  }
  if (refundFilter.value) params.status = refundFilter.value
  const res = await api.get('/delivery/refunds', { params })
  refunds.value = res.data?.items || []
  refundPager.total = res.data?.total || 0
}

function reloadRefunds() {
  refundPager.page = 1
  return loadRefunds()
}

async function loadBindings() {
  const res = await api.get('/delivery/store-bindings')
  const data = res.data
  bindings.value = Array.isArray(data) ? data : data?.items || []
}

async function loadMaps() {
  const res = await api.get('/delivery/product-maps')
  const data = res.data
  maps.value = Array.isArray(data) ? data : data?.items || []
}

async function loadLogs() {
  const params: Record<string, any> = { page_size: 50 }
  if (logPlatform.value) params.platform = logPlatform.value
  const res = await api.get('/delivery/callback-logs', { params })
  logs.value = res.data?.items || []
}

async function refreshAll() {
  pageLoading.value = true
  try {
    await Promise.all([loadStats(), loadOrders(), loadRefunds()])
  } finally {
    pageLoading.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'bindings') loadBindings()
  else if (name === 'maps') loadMaps()
  else if (name === 'logs') loadLogs()
  else if (name === 'refunds') loadRefunds()
}

async function openDetail(row: any) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await api.get(`/delivery/orders/${row.id}`)
    detail.value = res.data
  } catch (e: any) {
    ElMessage.error(e.message || '加载详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function confirmOrder(row: any) {
  actingId.value = row.order_id
  try {
    await api.post(`/delivery/orders/${row.order_id}/confirm`)
    ElMessage.success('已接单')
    await Promise.all([loadOrders(), loadStats()])
    if (detailVisible.value && detail.value?.order_id === row.order_id) {
      await openDetail({ id: detail.value.id })
    }
  } catch (e: any) {
    ElMessage.error(e.message || '接单失败')
  } finally {
    actingId.value = null
  }
}

function askCancel(row: any) {
  cancelTarget.value = row
  cancelReason.value = '商家忙碌，无法接单'
  cancelVisible.value = true
}

async function doCancel() {
  if (!cancelTarget.value) return
  if (!cancelReason.value?.trim()) {
    ElMessage.warning('请填写取消原因')
    return
  }
  cancelLoading.value = true
  try {
    await api.post(`/delivery/orders/${cancelTarget.value.id}/cancel`, { reason: cancelReason.value.trim() })
    ElMessage.success('已取消')
    cancelVisible.value = false
    detailVisible.value = false
    await Promise.all([loadOrders(), loadStats()])
  } catch (e: any) {
    ElMessage.error(e.message || '取消失败')
  } finally {
    cancelLoading.value = false
  }
}

async function handleRefund(id: number, approve: boolean, reason?: string) {
  try {
    await api.post(`/delivery/refunds/${id}/action`, {
      approve,
      reason: approve ? undefined : reason || '商家拒绝',
    })
    ElMessage.success(approve ? '已同意退款' : '已拒绝退款')
    await Promise.all([loadRefunds(), loadOrders(), loadStats()])
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

function askRejectRefund(row: any) {
  rejectId.value = row.id
  rejectReason.value = '商品已制作，无法退款'
  rejectVisible.value = true
}

async function doRejectRefund() {
  if (!rejectId.value) return
  if (!rejectReason.value?.trim()) {
    ElMessage.warning('请填写拒绝原因')
    return
  }
  await handleRefund(rejectId.value, false, rejectReason.value.trim())
  rejectVisible.value = false
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

async function saveMap() {
  if (!mapForm.platform_sku_id?.trim()) {
    ElMessage.warning('请填写平台 SKU')
    return
  }
  if (!mapForm.product_id || mapForm.product_id <= 0) {
    ElMessage.warning('请填写有效的本系统商品 ID')
    return
  }
  try {
    await api.post('/delivery/product-maps', mapForm)
    ElMessage.success('映射已保存')
    showMapDialog.value = false
    loadMaps()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  }
}

async function syncMenu() {
  try {
    const res = await api.post('/delivery/sync/menu', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`同步 ${res.data.synced}/${res.data.total} 个商品`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

async function syncPrices() {
  try {
    const res = await api.post('/delivery/sync/prices', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`更新 ${res.data.updated} 个价格`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

async function syncStock() {
  try {
    const res = await api.post('/delivery/sync/stock', null, { params: { platform: syncPlatform.value } })
    ElMessage.success(`更新 ${res.data.updated} 个库存`)
  } catch (e: any) {
    ElMessage.error(e.message || '同步失败')
  }
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    loadStats()
    if (tab.value === 'orders') loadOrders()
    if (tab.value === 'refunds') loadRefunds()
  }, 8000)
  connStatus.value = 'polling'
}

function scheduleReconnect() {
  if (reconnectTimer) return
  const delay = Math.min(15000, 1000 * Math.pow(2, reconnectAttempt++))
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWs()
  }, delay)
}

function connectWs() {
  try {
    ws?.close()
  } catch {
    /* */
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = localStorage.getItem('snh_staff_token') || localStorage.getItem('token') || ''
  connStatus.value = 'connecting'
  ws = new WebSocket(`${proto}://${location.host}/api/v1/ws/orders?token=${encodeURIComponent(token)}`)
  ws.onopen = () => {
    reconnectAttempt = 0
    connStatus.value = 'online'
    stopPoll()
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data || '{}')
      const event = msg.event || ''
      if (event === 'ORDER_URGE') ElMessage.warning('收到催单')
      else if (event === 'ORDER_REFUND') ElMessage.warning('收到退款申请')
      else if (event === 'ORDER_NEW') ElMessage.info('新外卖订单')
    } catch {
      /* */
    }
    loadStats()
    if (tab.value === 'orders') loadOrders()
    if (tab.value === 'refunds') loadRefunds()
  }
  ws.onerror = () => {
    connStatus.value = 'offline'
  }
  ws.onclose = () => {
    connStatus.value = 'offline'
    startPoll()
    scheduleReconnect()
  }
}

watch(
  () => filters.only_urge,
  (v) => {
    if (v) quickFilter.value = 'urge'
  },
)

onMounted(async () => {
  pageLoading.value = true
  try {
    await Promise.all([loadStats(), loadOrders(), loadRefunds()])
  } finally {
    pageLoading.value = false
  }
  connectWs()
})

onUnmounted(() => {
  stopPoll()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  try {
    ws?.close()
  } catch {
    /* */
  }
})
</script>

<style scoped>
.delivery-page {
  min-height: 400px;
}
.kpi-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  align-items: stretch;
}
.kpi-card {
  min-width: 108px;
  padding: 12px 14px;
  background: #f7f8fa;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: border-color 0.15s, background 0.15s;
}
.kpi-card:hover {
  border-color: #c0c4cc;
}
.kpi-card.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.kpi-card.alert {
  animation: pulse 1.6s ease-in-out infinite;
}
.kpi-card.tone-danger {
  background: #fef0f0;
}
.kpi-card.tone-warn {
  background: #fdf6ec;
}
.kpi-card.tone-ok {
  background: #f0f9eb;
}
.kpi-val {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
  line-height: 1.2;
}
.kpi-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.kpi-meta {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  justify-content: center;
}
.conn {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  background: #f4f4f5;
  color: #909399;
}
.conn.online {
  background: #f0f9eb;
  color: #67c23a;
}
.conn.polling {
  background: #fdf6ec;
  color: #e6a23c;
}
.conn.offline,
.conn.connecting {
  background: #f4f4f5;
  color: #909399;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.pager {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
.money {
  font-weight: 600;
  color: #e6a23c;
}
.muted {
  color: #c0c4cc;
}
.tab-badge {
  margin-left: 6px;
}
.bind-form {
  margin-bottom: 8px;
}
.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.seq {
  font-size: 20px;
  font-weight: 700;
}
.mt {
  margin-top: 16px;
}
.item-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}
.item-name {
  font-weight: 500;
}
.item-remark {
  color: #e6a23c;
  font-size: 12px;
}
.item-opts {
  grid-column: 1 / -1;
  font-size: 12px;
  color: #909399;
}
.item-price {
  color: #606266;
}
.detail-actions {
  display: flex;
  gap: 8px;
}
:deep(.row-pending) {
  background: #fef0f0 !important;
}
:deep(.row-urge) {
  background: #fdf6ec !important;
}
:deep(.row-refund) {
  background: #fdf6ec !important;
}
@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(245, 108, 108, 0.25);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(245, 108, 108, 0.08);
  }
}
</style>
