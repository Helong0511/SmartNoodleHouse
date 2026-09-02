<template>
  <div class="pos" :class="{ fullscreen, standalone }">
    <header class="toolbar">
      <div class="left">
        <img src="/logo.png" alt="logo" class="head-logo" />
        <div class="head-text">
          <div class="title">收银点餐</div>
          <div class="sub">{{ storeLabel }}</div>
        </div>
        <el-select
          v-if="isSuperAdmin"
          v-model="selectedStoreId"
          placeholder="选择门店"
          size="large"
          class="store-select"
          @change="onStoreChange"
        >
          <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </div>
      <div class="right">
        <el-radio-group v-model="diningType" size="large" class="dine-toggle" @change="onDiningTypeChange">
          <el-radio-button label="DINE_IN">堂食</el-radio-button>
          <el-radio-button label="TAKEAWAY">外带</el-radio-button>
        </el-radio-group>
        <el-button
          v-if="diningType === 'DINE_IN'"
          size="large"
          type="warning"
          plain
          class="table-btn"
          @click="tableDialogVisible = true"
        >
          {{ tableLabel }}
        </el-button>
        <el-button size="large" @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
        <el-button v-if="auth.isCashier" size="large" @click="goMyStats">我的今日</el-button>
        <el-button v-if="currentShift" size="large" plain @click="openShiftOrders">本班订单</el-button>
        <el-button v-if="currentShift" size="large" type="warning" plain @click="shiftCloseVisible = true">交班</el-button>
        <el-button size="large" @click="lockScreen">锁屏</el-button>
        <el-button v-if="standalone" size="large" type="danger" plain @click="staffLogout">退出</el-button>
      </div>
    </header>

    <div v-if="loading" class="loading-tip">加载菜单中…</div>

    <div v-else class="main">
      <aside class="cat-nav">
        <button
          v-for="c in categories"
          :key="c.id"
          type="button"
          class="cat-item"
          :class="{ on: activeCat === c.id }"
          @click="activeCat = c.id"
        >
          {{ c.name }}
        </button>
      </aside>

      <section class="products">
        <div class="product-grid">
          <button
            v-for="p in visibleProducts"
            :key="p.id"
            type="button"
            class="product-card"
            @click="openProduct(p)"
          >
            <div class="thumb-wrap">
              <img v-if="p._imageUrl" :src="p._imageUrl" class="thumb" alt="" />
              <div v-else class="thumb ph">{{ (p.name || '餐')[0] }}</div>
            </div>
            <div class="pinfo">
              <div class="pname">{{ p.name }}</div>
              <div class="pprice">¥{{ p.price }}</div>
            </div>
          </button>
        </div>
        <div v-if="!visibleProducts.length" class="empty-menu">该分类暂无商品</div>
      </section>

      <aside class="cart-panel">
        <div class="cart-head">
          <span>购物车</span>
          <el-button v-if="cart.length" link type="danger" @click="clearCart">清空</el-button>
        </div>
        <div class="cart-list">
          <div v-if="!cart.length" class="cart-empty">点选左侧菜品加入</div>
          <div v-for="(item, idx) in cart" :key="idx" class="cart-line">
            <div class="line-main">
              <div class="line-name">{{ item.name }}</div>
              <div v-if="item.option_text" class="line-opt">{{ item.option_text }}</div>
            </div>
            <div class="line-ctrl">
              <el-button class="qty-btn" circle @click="changeQty(idx, -1)">−</el-button>
              <span class="qty">{{ item.quantity }}</span>
              <el-button class="qty-btn" circle type="primary" @click="changeQty(idx, 1)">+</el-button>
            </div>
            <div class="line-amt">¥{{ item.subtotal }}</div>
          </div>
        </div>
        <div class="cart-foot">
          <div v-if="diningType === 'TAKEAWAY'" class="sum-row sub">
            <span>商品小计</span><span>¥{{ cartSubtotal }}</span>
          </div>
          <div v-if="diningType === 'TAKEAWAY'" class="sum-row sub pack-row">
            <span>打包费</span>
            <span v-if="waivePackingFee" class="waived">已免收</span>
            <span v-else>¥{{ packingFeeDisplay }}</span>
          </div>
          <div class="sum-row"><span>合计</span><span class="sum">¥{{ cartTotal }}</span></div>
          <el-input v-model="remark" placeholder="备注（可选）" size="large" class="remark" />
          <el-button
            type="primary"
            size="large"
            class="pay-btn"
            :loading="paying"
            :disabled="!cart.length"
            @click="openCheckout"
          >
            去收款 ¥{{ cartTotal }}
          </el-button>
        </div>
      </aside>
    </div>

    <el-dialog v-model="tableDialogVisible" title="选择桌号" width="560px" class="table-dialog">
      <div class="table-grid">
        <button
          v-for="t in tables"
          :key="t.id"
          type="button"
          class="table-chip"
          :class="{ on: selectedTableId === t.id }"
          @click="pickTable(t)"
        >
          {{ t.table_name || t.table_no }}
        </button>
      </div>
      <div v-if="!tables.length" class="empty-tables">暂无可用桌台</div>
    </el-dialog>

    <!-- 规格选择：大按钮触控 -->
    <el-drawer
      v-model="productVisible"
      title="选择规格"
      direction="btt"
      :size="drawerSize"
      class="spec-drawer"
      :show-close="true"
    >
      <div v-if="currentProduct" class="drawer-body">
        <div class="drawer-head">
          <h3 class="drawer-title">{{ currentProduct.name }}</h3>
          <div class="drawer-price-live">¥{{ drawerUnitPrice }}</div>
        </div>
        <div class="opt-scroll">
          <div v-for="g in currentProduct.option_groups || []" :key="g.id" class="opt-group">
            <h4 class="opt-group-title">
              {{ g.name }}
              <span v-if="g.is_required" class="req">必选</span>
            </h4>
            <div class="opt-chips">
              <template v-if="g.selection_type === 'SINGLE'">
                <button
                  v-for="o in g.options"
                  :key="o.id"
                  type="button"
                  class="opt-chip"
                  :class="{ on: selectedOptions[g.id] === o.id }"
                  @click="selectedOptions[g.id] = o.id"
                >
                  <span class="chip-name">{{ o.name }}</span>
                  <span v-if="+o.extra_price" class="chip-extra">+¥{{ o.extra_price }}</span>
                </button>
              </template>
              <template v-else>
                <button
                  v-for="o in g.options"
                  :key="o.id"
                  type="button"
                  class="opt-chip"
                  :class="{ on: isMultiSelected(g.id, o.id) }"
                  @click="toggleMultiOption(g.id, o.id)"
                >
                  <span class="chip-name">{{ o.name }}</span>
                  <span v-if="+o.extra_price" class="chip-extra">+¥{{ o.extra_price }}</span>
                </button>
              </template>
            </div>
          </div>
        </div>
        <div class="drawer-foot">
          <el-button type="primary" class="add-cart-btn" @click="addToCart">
            加入购物车 ¥{{ drawerUnitPrice }}
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 收款 -->
    <el-dialog
      v-model="payDialogVisible"
      title="收款"
      width="520px"
      class="pay-dialog"
      :close-on-click-modal="false"
      @closed="resetCheckout"
    >
      <div class="pay-total-row">
        <span>应收金额</span>
        <span class="pay-total">¥{{ payableAmount }}</span>
      </div>
      <div v-if="diningType === 'TAKEAWAY'" class="pay-pack-row">
        <div class="pay-pack-info">
          <span>商品 ¥{{ cartSubtotal }}</span>
          <span v-if="waivePackingFee" class="waived">打包费已免收</span>
          <span v-else>打包费 ¥{{ packingFeeDisplay }}</span>
        </div>
        <el-button
          v-if="!waivePackingFee"
          type="warning"
          plain
          size="large"
          @click="toggleWaivePacking(true)"
        >
          不收打包费
        </el-button>
        <el-button v-else size="large" @click="toggleWaivePacking(false)">恢复打包费</el-button>
      </div>

      <!-- 选择支付方式 -->
      <div v-if="payStep === 'pick'" class="pay-methods">
        <button type="button" class="pay-method scan" :disabled="paying" @click="startBarcodePay">
          <span class="pm-icon">扫</span>
          <span class="pm-label-wrap">
            <span class="pm-label">扫码收款</span>
            <span class="pm-sub">自动识别微信 / 支付宝</span>
          </span>
        </button>
        <button type="button" class="pay-method cash" :disabled="paying" @click="openCashPay">
          <span class="pm-icon">¥</span>
          <span class="pm-label">现金收款</span>
        </button>
      </div>

      <!-- 现金 -->
      <div v-else-if="payStep === 'cash'" class="cash-panel">
        <div class="cash-row">
          <span>应收</span>
          <span class="cash-amt">¥{{ checkoutAmount }}</span>
        </div>
        <div class="cash-input-wrap">
          <label>实收金额</label>
          <div class="cash-quick">
            <button type="button" class="cash-quick-btn" @click="setCashQuick(20)">¥20</button>
            <button type="button" class="cash-quick-btn" @click="setCashQuick(50)">¥50</button>
            <button type="button" class="cash-quick-btn" @click="setCashQuick(100)">¥100</button>
            <button type="button" class="cash-quick-btn exact" @click="setCashExact">正好</button>
          </div>
          <el-input
            v-model="cashReceived"
            size="large"
            inputmode="decimal"
            placeholder="输入顾客给的现金"
            class="cash-input"
            @input="onCashInput"
          >
            <template #prefix>¥</template>
          </el-input>
        </div>
        <div class="cash-row change-row">
          <span>找零</span>
          <span class="change-amt" :class="{ warn: cashChangeWarn }">¥{{ cashChangeDisplay }}</span>
        </div>
        <div class="cash-actions">
          <el-button size="large" @click="payStep = 'pick'">返回</el-button>
          <el-button
            type="primary"
            size="large"
            class="cash-confirm"
            :loading="paying"
            :disabled="cashConfirmDisabled"
            @click="confirmCashPay"
          >
            确认收款
          </el-button>
        </div>
      </div>

      <!-- 扫顾客付款码 -->
      <div v-else-if="payStep === 'barcode'" class="barcode-panel">
        <div class="scan-provider">请扫描顾客付款码</div>
        <p v-if="barcodeProviderHint" class="scan-detected" :class="{ warn: !detectBarcodeProvider(barcodeInput.trim()) }">
          {{ barcodeProviderHint }}
        </p>
        <el-input
          ref="barcodeInputRef"
          v-model="barcodeInput"
          size="large"
          placeholder="扫码枪对准此处，或手动粘贴付款码"
          class="barcode-input"
          @keyup.enter="submitBarcodePay"
        />
        <p class="scan-hint">支持微信、支付宝付款码，扫完自动提交</p>
        <div class="cash-actions">
          <el-button size="large" @click="cancelBarcode">返回</el-button>
          <el-button type="primary" size="large" class="cash-confirm" :loading="paying" @click="submitBarcodePay">
            确认收款
          </el-button>
        </div>
      </div>
    </el-dialog>

    <!-- 开班 -->
    <el-dialog v-model="shiftOpenVisible" title="开班" width="420px" :close-on-click-modal="false" :show-close="false">
      <p class="shift-hint">请输入本班备用金后开始收银</p>
      <el-input v-model="openingFloat" size="large" inputmode="decimal" placeholder="备用金金额">
        <template #prefix>¥</template>
      </el-input>
      <template #footer>
        <el-button type="primary" size="large" :loading="shiftLoading" @click="confirmOpenShift">开始营业</el-button>
      </template>
    </el-dialog>

    <!-- 交班 -->
    <el-dialog v-model="shiftCloseVisible" title="交班" width="480px">
      <div v-if="currentShift" class="shift-summary">
        <p>本班收款合计：<b>¥{{ currentShift.total_amount }}</b></p>
        <p>现金 ¥{{ currentShift.total_cash }} · 微信 ¥{{ currentShift.total_wechat }} · 支付宝 ¥{{ currentShift.total_alipay }}</p>
      </div>
      <el-input v-model="closingCash" size="large" inputmode="decimal" placeholder="交班实盘现金（含备用金）" style="margin-top:12px">
        <template #prefix>¥</template>
      </el-input>
      <el-input v-model="shiftRemark" type="textarea" placeholder="交班备注（可选）" style="margin-top:12px" />
      <template #footer>
        <el-button @click="shiftCloseVisible = false">取消</el-button>
        <el-button type="primary" :loading="shiftLoading" @click="confirmCloseShift">确认交班</el-button>
      </template>
    </el-dialog>

    <!-- 本班订单 -->
    <el-drawer v-model="shiftOrdersVisible" title="本班订单" size="420px" direction="rtl">
      <div v-if="currentShift" class="shift-orders-head">
        <p>开班 {{ formatShiftTime(currentShift.opened_at) }}</p>
        <p class="shift-orders-sum">本班收款 <b>¥{{ currentShift.total_amount }}</b> · 共 {{ shiftOrdersTotal }} 单</p>
        <el-button size="large" :loading="shiftOrdersLoading" @click="loadShiftOrders">刷新</el-button>
      </div>
      <div v-if="shiftOrdersLoading" class="shift-orders-tip">加载中…</div>
      <div v-else-if="!shiftOrders.length" class="shift-orders-tip">本班暂无收款订单</div>
      <div v-else class="shift-orders-list">
        <div v-for="o in shiftOrders" :key="o.id" class="shift-order-row">
          <div class="so-top">
            <span class="so-no">{{ o.order_no }}</span>
            <span class="so-amt">¥{{ o.total_amount }}</span>
          </div>
          <div class="so-meta">
            <span>{{ payMethodLabel(o.payment_method) }}</span>
            <span>{{ diningLabel(o.dining_type, o.table_no, o.table_name) }}</span>
            <span>{{ formatShiftTime(o.paid_at) }}</span>
          </div>
          <div class="so-actions">
            <span v-if="o.refund_request" class="so-refund-tag" :class="refundTagClass(o.refund_request.status)">
              {{ refundStatusLabel(o.refund_request.status) }}
              <template v-if="o.refund_request.amount"> · ¥{{ o.refund_request.amount }}</template>
            </span>
            <el-button
              v-if="canApplyRefund(o)"
              type="danger"
              plain
              size="small"
              @click="openRefundApply(o)"
            >
              申请退款
            </el-button>
            <el-button
              v-else-if="o.refund_request?.status === 'PENDING'"
              size="small"
              @click="cancelRefundRequest(o.refund_request.id)"
            >
              撤销申请
            </el-button>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 申请退款 -->
    <el-dialog v-model="refundApplyVisible" title="申请退款" width="420px" :close-on-click-modal="false">
      <p v-if="refundTarget" class="refund-hint">订单 {{ refundTarget.order_no }} · 实收 ¥{{ refundTarget.total_amount }}</p>
      <el-input
        v-model="refundAmount"
        size="large"
        inputmode="decimal"
        placeholder="退款金额（默认全额）"
        style="margin-bottom:12px"
      >
        <template #prefix>¥</template>
      </el-input>
      <el-input
        v-model="refundReason"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        placeholder="请填写退款原因（必填）"
      />
      <template #footer>
        <el-button @click="refundApplyVisible = false">取消</el-button>
        <el-button type="danger" :loading="refundSubmitting" @click="submitRefundApply">提交申请</el-button>
      </template>
    </el-dialog>

    <!-- 锁屏 -->
    <div v-if="locked" class="lock-overlay">
      <div class="lock-card">
        <img src="/logo.png" alt="" class="lock-logo" />
        <div class="lock-user">{{ auth.user?.display_name || auth.user?.username }}</div>
        <el-input
          v-model="unlockPassword"
          type="password"
          size="large"
          placeholder="输入密码解锁"
          show-password
          class="lock-input"
          @keyup.enter="tryUnlock"
        />
        <el-button type="primary" size="large" class="lock-btn" :loading="unlocking" @click="tryUnlock">解锁</el-button>
        <el-button link type="danger" @click="staffLogout">切换账号</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { isPosLocked, setPosLocked } from '@/utils/staffAuth'
import { mediaUrl } from '@/utils/media'
import {
  buildOptionLabels,
  calcUnitPrice,
  collectOptionIds,
  mergeCartItem,
} from '@/utils/cart'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const standalone = computed(() => route.path === '/pos-terminal' || !!route.meta?.standalone)
const isSuperAdmin = computed(() => auth.isSuperAdmin)
const storeOptions = ref<{ id: number; name: string }[]>([])
const selectedStoreId = ref<number | null>(null)
const storeLabel = computed(() => {
  if (isSuperAdmin.value) {
    const s = storeOptions.value.find((x) => x.id === selectedStoreId.value)
    return s?.name || '请选择门店'
  }
  return auth.user?.store_name || auth.user?.display_name || '本店'
})

const loading = ref(true)
const paying = ref(false)
const fullscreen = ref(false)
const categories = ref<any[]>([])
const products = ref<any[]>([])
const activeCat = ref<number | null>(null)
const tables = ref<any[]>([])
const selectedTableId = ref<number | null>(null)
const diningType = ref<'DINE_IN' | 'TAKEAWAY'>('DINE_IN')
const waivePackingFee = ref(false)
const remark = ref('')
const cart = ref<any[]>([])
const tableDialogVisible = ref(false)
const productVisible = ref(false)
const currentProduct = ref<any>(null)
const selectedOptions = reactive<Record<number, number>>({})
const multiOptions = reactive<Record<number, number[]>>({})

const payDialogVisible = ref(false)
const payStep = ref<'pick' | 'cash' | 'barcode'>('pick')
const checkoutOrderId = ref<number | null>(null)
const checkoutOrderNo = ref('')
const checkoutAmount = ref('0.00')
const cashReceived = ref('')
const barcodeInput = ref('')
const barcodeInputRef = ref<any>(null)

function detectBarcodeProvider(code: string): 'WECHAT' | 'ALIPAY' | null {
  const s = code.trim()
  if (/^1[0-5]\d{16}$/.test(s)) return 'WECHAT'
  if (/^(?:2[5-9]|30)\d{14,22}$/.test(s)) return 'ALIPAY'
  return null
}

const barcodeProviderHint = computed(() => {
  const code = barcodeInput.value.trim()
  if (!code) return ''
  const p = detectBarcodeProvider(code)
  if (p === 'WECHAT') return '已识别：微信支付'
  if (p === 'ALIPAY') return '已识别：支付宝'
  if (code.length >= 6) return '无法识别，请确认是微信或支付宝付款码'
  return ''
})

const locked = ref(isPosLocked())
const unlockPassword = ref('')
const unlocking = ref(false)
const currentShift = ref<any>(null)
const shiftOpenVisible = ref(false)
const shiftCloseVisible = ref(false)
const shiftLoading = ref(false)
const openingFloat = ref('0')
const closingCash = ref('')
const shiftRemark = ref('')
const shiftOrdersVisible = ref(false)
const shiftOrdersLoading = ref(false)
const shiftOrders = ref<any[]>([])
const shiftOrdersTotal = ref(0)
const refundApplyVisible = ref(false)
const refundSubmitting = ref(false)
const refundTarget = ref<any>(null)
const refundAmount = ref('')
const refundReason = ref('')

const drawerSize = computed(() => (window.innerWidth >= 768 ? '58%' : '78%'))

const tableLabel = computed(() => {
  if (diningType.value !== 'DINE_IN') return ''
  const t = tables.value.find((x) => x.id === selectedTableId.value)
  return t ? (t.table_name || t.table_no) : '选择桌号'
})

const visibleProducts = computed(() =>
  products.value.filter((p) => !activeCat.value || p.category_id === activeCat.value),
)

const cartSubtotal = computed(() => {
  const n = cart.value.reduce((s, i) => s + parseFloat(i.subtotal || 0), 0)
  return (Math.round(n * 100) / 100).toFixed(2)
})

const packingFeeNum = computed(() => {
  if (diningType.value !== 'TAKEAWAY' || waivePackingFee.value) return 0
  let fee = 0
  for (const item of cart.value) {
    const box = parseFloat(item.box_price || 0) || 0
    fee += box * (item.quantity || 1)
  }
  return Math.round(fee * 100) / 100
})

const packingFeeDisplay = computed(() => packingFeeNum.value.toFixed(2))

const cartTotal = computed(() => {
  const total = parseFloat(cartSubtotal.value) + packingFeeNum.value
  return total.toFixed(2)
})

const payableAmount = computed(() =>
  checkoutOrderId.value ? checkoutAmount.value : cartTotal.value,
)

const drawerUnitPrice = computed(() => {
  if (!currentProduct.value) return '0.00'
  return calcUnitPrice(currentProduct.value, selectedOptions, multiOptions).toFixed(2)
})

const cashChangeNum = computed(() => {
  const received = parseFloat(cashReceived.value)
  const due = parseFloat(checkoutAmount.value)
  if (!Number.isFinite(received) || !Number.isFinite(due)) return null
  return Math.round((received - due) * 100) / 100
})

const cashChangeDisplay = computed(() => {
  if (cashChangeNum.value === null) return '—'
  return cashChangeNum.value.toFixed(2)
})

const cashChangeWarn = computed(() => cashChangeNum.value !== null && cashChangeNum.value < 0)

const cashConfirmDisabled = computed(() => {
  if (!cashReceived.value.trim()) return false
  return cashChangeWarn.value
})

function effectiveStoreId(): number | null {
  if (isSuperAdmin.value) return selectedStoreId.value
  return auth.user?.store_id ?? null
}

function decorate(p: any) {
  return { ...p, _imageUrl: mediaUrl(p.main_image), box_price: p.box_price ?? '0' }
}

function isMultiSelected(gId: number, optId: number) {
  return (multiOptions[gId] || []).includes(optId)
}

function toggleMultiOption(gId: number, optId: number) {
  const arr = [...(multiOptions[gId] || [])]
  const idx = arr.indexOf(optId)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(optId)
  multiOptions[gId] = arr
}

function onCashInput() {
  cashReceived.value = cashReceived.value.replace(/[^\d.]/g, '')
}

async function loadStores() {
  if (!isSuperAdmin.value) {
    if (auth.user?.store_id) selectedStoreId.value = auth.user.store_id
    return
  }
  const res = await api.get('/orders/kitchen/stores')
  storeOptions.value = res.data?.items || []
  if (!selectedStoreId.value && storeOptions.value.length) {
    selectedStoreId.value = storeOptions.value[0].id
  }
}

async function loadTables() {
  const sid = effectiveStoreId()
  const params = sid ? { store_id: sid } : {}
  const res = await api.get('/tables/pos', { params })
  tables.value = res.data?.items || []
  if (selectedTableId.value && !tables.value.some((t) => t.id === selectedTableId.value)) {
    selectedTableId.value = null
  }
}

async function loadMenu() {
  const sid = effectiveStoreId()
  if (!sid) {
    categories.value = []
    products.value = []
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [cats, prods] = await Promise.all([
      api.get('/categories', { params: { store_id: sid } }),
      api.get('/products', { params: { store_id: sid, page_size: 200 } }),
    ])
    categories.value = cats.data || []
    products.value = ((prods.data?.items) || []).map(decorate)
    activeCat.value = categories.value[0]?.id ?? null
    await loadTables()
  } catch (e: any) {
    ElMessage.error(e.message || '加载菜单失败')
  } finally {
    loading.value = false
  }
}

function onStoreChange() {
  selectedTableId.value = null
  cart.value = []
  loadMenu()
}

function onDiningTypeChange() {
  if (diningType.value === 'TAKEAWAY') {
    selectedTableId.value = null
  } else {
    waivePackingFee.value = false
  }
  invalidateCheckoutOrder()
}

function invalidateCheckoutOrder() {
  checkoutOrderId.value = null
  checkoutOrderNo.value = ''
}

function toggleWaivePacking(waive: boolean) {
  waivePackingFee.value = waive
  invalidateCheckoutOrder()
  if (payDialogVisible.value) {
    checkoutAmount.value = cartTotal.value
  }
}

function pickTable(t: any) {
  selectedTableId.value = t.id
  tableDialogVisible.value = false
}

function clearCart() {
  cart.value = []
}

function changeQty(idx: number, delta: number) {
  const list = [...cart.value]
  const item = { ...list[idx] }
  const q = (item.quantity || 1) + delta
  if (q <= 0) {
    list.splice(idx, 1)
  } else {
    item.quantity = q
    const unit = parseFloat(item.unit_price || item.price || 0)
    item.subtotal = (unit * q).toFixed(2)
    list[idx] = item
  }
  cart.value = list
  invalidateCheckoutOrder()
}

async function openProduct(p: any) {
  const res = await api.get(`/products/${p.id}`)
  currentProduct.value = decorate(res.data)
  Object.keys(selectedOptions).forEach((k) => delete selectedOptions[Number(k)])
  Object.keys(multiOptions).forEach((k) => delete multiOptions[Number(k)])
  for (const g of currentProduct.value.option_groups || []) {
    if (g.selection_type === 'SINGLE' && g.options?.length) {
      const normal = g.options.find((o: any) => o.code === 'NORMAL') || g.options[0]
      selectedOptions[g.id] = normal.id
    }
    if (g.selection_type === 'MULTIPLE') multiOptions[g.id] = []
  }
  productVisible.value = true
}

function addToCart() {
  const p = currentProduct.value
  if (!p) return
  for (const g of p.option_groups || []) {
    if (!g.is_required) continue
    const has =
      (g.selection_type === 'SINGLE' && selectedOptions[g.id]) ||
      (g.selection_type === 'MULTIPLE' && (multiOptions[g.id] || []).length)
    if (!has) {
      ElMessage.warning(`请选择：${g.name}`)
      return
    }
  }
  const optionIds = collectOptionIds(p, selectedOptions, multiOptions)
  const optionLabels = buildOptionLabels(p, selectedOptions, multiOptions)
  const unitPrice = calcUnitPrice(p, selectedOptions, multiOptions)
  cart.value = mergeCartItem(cart.value, {
    product_id: p.id,
    name: p.name,
    quantity: 1,
    option_ids: optionIds,
    option_labels: optionLabels,
    option_text: optionLabels.join('、'),
    unit_price: unitPrice,
    price: p.price,
    box_price: p.box_price ?? '0',
    subtotal: unitPrice.toFixed(2),
    imageUrl: p._imageUrl || '',
  })
  productVisible.value = false
}

function idempotencyKey() {
  const raw = JSON.stringify({
    store: effectiveStoreId(),
    table: selectedTableId.value,
    dining: diningType.value,
    waive_pack: waivePackingFee.value,
    items: cart.value.map((c) => ({ p: c.product_id, q: c.quantity, o: c.option_ids })),
  })
  let h = 0
  for (let i = 0; i < raw.length; i++) h = (Math.imul(31, h) + raw.charCodeAt(i)) | 0
  return `pos_${Math.abs(h).toString(16)}`
}

function validateBeforeCheckout(): boolean {
  if (!cart.value.length) return false
  if (diningType.value === 'DINE_IN' && !selectedTableId.value) {
    ElMessage.warning('没有选桌号')
    tableDialogVisible.value = true
    return false
  }
  const sid = effectiveStoreId()
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return false
  }
  return true
}

async function createPosOrder() {
  const sid = effectiveStoreId()
  const orderRes = await api.post('/orders/pos', {
    store_id: sid,
    table_id: diningType.value === 'DINE_IN' ? selectedTableId.value : null,
    dining_type: diningType.value,
    need_pack: diningType.value === 'TAKEAWAY',
    waive_packing_fee: diningType.value === 'TAKEAWAY' && waivePackingFee.value,
    remark: remark.value.trim() || null,
    items: cart.value.map((c) => ({
      product_id: c.product_id,
      quantity: c.quantity || 1,
      option_ids: c.option_ids || [],
    })),
    idempotency_key: idempotencyKey(),
  })
  checkoutOrderId.value = orderRes.data?.id
  checkoutOrderNo.value = orderRes.data?.order_no || ''
  checkoutAmount.value = orderRes.data?.total_amount || cartTotal.value
  return orderRes.data
}

function resetCheckout() {
  payStep.value = 'pick'
  invalidateCheckoutOrder()
  cashReceived.value = ''
  barcodeInput.value = ''
}

function setCashQuick(amt: number) {
  cashReceived.value = String(amt)
}

function setCashExact() {
  cashReceived.value = checkoutAmount.value || cartTotal.value
}

function finishCheckout(msg: string) {
  ElMessage.success(msg)
  cart.value = []
  remark.value = ''
  waivePackingFee.value = false
  payDialogVisible.value = false
  resetCheckout()
  loadShift()
  if (shiftOrdersVisible.value) loadShiftOrders()
}

function staffLogout() {
  auth.logout()
  router.push('/login')
}

function lockScreen() {
  locked.value = true
  setPosLocked(true)
  unlockPassword.value = ''
}

async function tryUnlock() {
  if (!unlockPassword.value) {
    ElMessage.warning('请输入密码')
    return
  }
  unlocking.value = true
  try {
    await api.post('/auth/verify-password', { password: unlockPassword.value })
    locked.value = false
    setPosLocked(false)
    unlockPassword.value = ''
    ElMessage.success('已解锁')
  } catch (e: any) {
    ElMessage.error(e.message || '密码错误')
  } finally {
    unlocking.value = false
  }
}

function goMyStats() {
  router.push('/pos/my-stats')
}

function formatShiftTime(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function payMethodLabel(method: string | null | undefined) {
  const m = (method || '').toUpperCase()
  if (m === 'CASH') return '现金'
  if (m === 'WECHAT') return '微信'
  if (m === 'ALIPAY') return '支付宝'
  return method || '—'
}

function diningLabel(dtype: string | null | undefined, tableNo?: string | null, tableName?: string | null) {
  if ((dtype || '').toUpperCase() === 'TAKEAWAY') return '外带'
  const label = (tableName || tableNo || '').trim()
  return label ? `${label}桌` : '堂食'
}

async function loadShiftOrders() {
  if (!currentShift.value) return
  shiftOrdersLoading.value = true
  try {
    const res = await api.get('/pos/shifts/current/orders', { params: { page: 1, page_size: 100 } })
    shiftOrders.value = res.data?.items || []
    shiftOrdersTotal.value = res.data?.total ?? shiftOrders.value.length
  } catch (e: any) {
    ElMessage.error(e.message || '加载本班订单失败')
  } finally {
    shiftOrdersLoading.value = false
  }
}

function openShiftOrders() {
  shiftOrdersVisible.value = true
  loadShiftOrders()
}

function refundStatusLabel(status: string) {
  const map: Record<string, string> = {
    PENDING: '退款审批中',
    REJECTED: '已拒绝',
    CANCELLED: '已撤销',
    SUCCESS: '已退款',
    FAILED: '退款失败',
  }
  return map[status] || status
}

function refundTagClass(status: string) {
  if (status === 'PENDING') return 'pending'
  if (status === 'SUCCESS') return 'ok'
  if (status === 'FAILED' || status === 'REJECTED') return 'bad'
  return ''
}

function canApplyRefund(o: any) {
  if (!o) return false
  const ps = (o.payment_status || '').toUpperCase()
  if (ps === 'REFUNDED') return false
  const st = o.refund_request?.status
  if (st === 'PENDING' || st === 'SUCCESS') return false
  return true
}

function openRefundApply(o: any) {
  refundTarget.value = o
  refundAmount.value = o.total_amount || ''
  refundReason.value = ''
  refundApplyVisible.value = true
}

async function submitRefundApply() {
  if (!refundTarget.value) return
  const reason = refundReason.value.trim()
  if (!reason) {
    ElMessage.warning('请填写退款原因')
    return
  }
  refundSubmitting.value = true
  try {
    const body: { order_id: number; reason: string; amount?: string } = {
      order_id: refundTarget.value.id,
      reason,
    }
    if (refundAmount.value.trim()) body.amount = refundAmount.value.trim()
    await api.post('/pos/refund-requests', body)
    ElMessage.success('已提交退款申请，等待店长审批')
    refundApplyVisible.value = false
    await loadShiftOrders()
  } catch (e: any) {
    ElMessage.error(e.message || '申请失败')
  } finally {
    refundSubmitting.value = false
  }
}

async function cancelRefundRequest(id: number) {
  try {
    await api.post(`/pos/refund-requests/${id}/cancel`)
    ElMessage.success('已撤销申请')
    await loadShiftOrders()
  } catch (e: any) {
    ElMessage.error(e.message || '撤销失败')
  }
}

async function loadShift() {
  try {
    const res = await api.get('/pos/shifts/current')
    currentShift.value = res.data?.shift || null
    if (!currentShift.value && auth.isCashier) {
      shiftOpenVisible.value = true
    }
  } catch {
    /* ignore */
  }
}

async function confirmOpenShift() {
  shiftLoading.value = true
  try {
    const sid = effectiveStoreId()
    const res = await api.post('/pos/shifts/open', {
      opening_float: openingFloat.value || '0',
      store_id: sid,
    })
    currentShift.value = res.data
    shiftOpenVisible.value = false
    ElMessage.success('已开班')
  } catch (e: any) {
    ElMessage.error(e.message || '开班失败')
  } finally {
    shiftLoading.value = false
  }
}

async function confirmCloseShift() {
  shiftLoading.value = true
  try {
    const body: Record<string, string> = {}
    if (closingCash.value.trim()) body.closing_cash = closingCash.value.trim()
    if (shiftRemark.value.trim()) body.remark = shiftRemark.value.trim()
    await api.post('/pos/shifts/close', body)
    currentShift.value = null
    shiftCloseVisible.value = false
    closingCash.value = ''
    shiftRemark.value = ''
    ElMessage.success('交班完成')
    if (auth.isCashier) {
      shiftOpenVisible.value = true
      openingFloat.value = '0'
    }
  } catch (e: any) {
    ElMessage.error(e.message || '交班失败')
  } finally {
    shiftLoading.value = false
  }
}

function openCheckout() {
  if (!validateBeforeCheckout()) return
  invalidateCheckoutOrder()
  payStep.value = 'pick'
  checkoutAmount.value = cartTotal.value
  payDialogVisible.value = true
}

async function ensureOrder() {
  if (checkoutOrderId.value) return checkoutOrderId.value
  paying.value = true
  try {
    const data = await createPosOrder()
    return data.id as number
  } finally {
    paying.value = false
  }
}

function openCashPay() {
  payStep.value = 'cash'
  cashReceived.value = checkoutAmount.value || cartTotal.value
}

async function confirmCashPay() {
  if (!validateBeforeCheckout()) return
  paying.value = true
  try {
    const orderId = await ensureOrder()
    const body: { received_amount?: string } = {}
    if (cashReceived.value.trim()) {
      body.received_amount = cashReceived.value.trim()
    }
    const payRes = await api.post(`/payments/orders/${orderId}/cash`, body || {})
    const change = payRes.data?.change_amount
    const msg = change != null
      ? `收款成功 · ${payRes.data?.order_no} · 找零 ¥${change}`
      : `收款成功 · ${payRes.data?.order_no || checkoutOrderNo.value}`
    finishCheckout(msg)
  } catch (e: any) {
    ElMessage.error(e.message || '收款失败')
  } finally {
    paying.value = false
  }
}

async function startBarcodePay() {
  if (!validateBeforeCheckout()) return
  payStep.value = 'barcode'
  barcodeInput.value = ''
  await nextTick()
  barcodeInputRef.value?.focus?.()
}

async function submitBarcodePay() {
  const code = barcodeInput.value.trim()
  if (!code) {
    ElMessage.warning('请扫描顾客付款码')
    return
  }
  const detected = detectBarcodeProvider(code)
  if (!detected) {
    ElMessage.warning('无法识别付款码，请扫描微信或支付宝付款码')
    return
  }
  if (!validateBeforeCheckout()) return
  paying.value = true
  const payingMsg = ElMessage({
    message: '正在收款，请顾客确认支付…',
    type: 'info',
    duration: 0,
  })
  try {
    const orderId = await ensureOrder()
    const payRes = await api.post(
      `/payments/orders/${orderId}/pos-scan`,
      { auth_code: code },
      { timeout: 90000 },
    )
    if (payRes.data?.mock_settled || payRes.data?.status === 'PAID') {
      finishCheckout(`支付成功 · ${payRes.data?.order_no || checkoutOrderNo.value}`)
      return
    }
    ElMessage.error('支付未完成，请重试')
  } catch (e: any) {
    const msg = e.message || '收款失败'
    if (msg.includes('已支付')) {
      finishCheckout('订单已支付')
      return
    }
    ElMessage.error(msg)
  } finally {
    payingMsg.close()
    paying.value = false
  }
}

function cancelBarcode() {
  payStep.value = 'pick'
  barcodeInput.value = ''
}

function toggleFullscreen() {
  const el = document.documentElement
  if (!document.fullscreenElement) {
    el.requestFullscreen?.().then(() => { fullscreen.value = true }).catch(() => { fullscreen.value = true })
  } else {
    document.exitFullscreen?.().then(() => { fullscreen.value = false }).catch(() => { fullscreen.value = false })
  }
}

function onFsChange() {
  fullscreen.value = !!document.fullscreenElement
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  auth.assertSessionNotStale()
  locked.value = isPosLocked()
  await loadStores()
  await loadMenu()
  await loadShift()
  if (standalone.value) document.title = '收银点餐'
  document.addEventListener('fullscreenchange', onFsChange)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', onFsChange)
})

watch(() => auth.user?.store_id, () => {
  if (!isSuperAdmin.value && auth.user?.store_id) {
    selectedStoreId.value = auth.user.store_id
    loadMenu()
  }
})
</script>

<style scoped>
.pos {
  min-height: 100vh;
  background: #f4f6f8;
  display: flex;
  flex-direction: column;
}
.pos.standalone { min-height: 100dvh; }
.pos.fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  min-height: 100dvh;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  position: sticky;
  top: 0;
  z-index: 10;
}
.left, .right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.head-logo { width: 48px; height: auto; border-radius: 8px; }
.head-text .title { font-size: 22px; font-weight: 700; color: #222; }
.head-text .sub { font-size: 14px; color: #888; }
.store-select { width: 180px; }
.dine-toggle :deep(.el-radio-button__inner) { padding: 12px 24px; font-size: 17px; }
.table-btn { min-width: 130px; font-size: 17px; font-weight: 600; }
.loading-tip { padding: 48px; text-align: center; color: #888; font-size: 20px; }
.main {
  flex: 1;
  display: grid;
  grid-template-columns: 168px 1fr minmax(300px, 380px);
  gap: 0;
  min-height: 0;
}
.cat-nav {
  background: #f0f2f5;
  border-right: 1px solid #e0e0e0;
  padding: 10px 8px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  -webkit-overflow-scrolling: touch;
}
.cat-item {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 60px;
  border: 2px solid #d9dde3;
  border-radius: 12px;
  background: #fff;
  padding: 14px 10px;
  font-size: 18px;
  font-weight: 600;
  color: #444;
  cursor: pointer;
  text-align: center;
  line-height: 1.3;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  transition: border-color 0.15s, background 0.15s, transform 0.1s;
  flex-shrink: 0;
}
.cat-item:active {
  transform: scale(0.98);
}
.cat-item.on {
  color: #ff6b35;
  font-weight: 700;
  background: #fff8f5;
  border-color: #ff6b35;
  box-shadow: 0 2px 10px rgba(255, 107, 53, 0.18);
}
.products {
  padding: 14px;
  overflow-y: auto;
}
.product-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(172px, 1fr));
  gap: 14px;
}
@media (min-width: 900px) {
  .main {
    grid-template-columns: 188px 1fr minmax(300px, 380px);
  }
  .cat-nav {
    padding: 12px 10px;
    gap: 12px;
  }
  .cat-item {
    min-height: 68px;
    font-size: 19px;
    padding: 16px 12px;
    border-radius: 14px;
  }
  .product-grid {
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 16px;
  }
}
.product-card {
  border: none;
  background: #fff;
  border-radius: 14px;
  padding: 12px;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(0,0,0,.07);
  transition: transform .12s;
}
.product-card:active { transform: scale(0.98); }
.thumb-wrap { aspect-ratio: 1; border-radius: 12px; overflow: hidden; background: #f3f3f3; margin-bottom: 10px; }
.thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb.ph {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 40px; font-weight: 700; color: #ff6b35;
  background: linear-gradient(145deg, #ffe8df, #f5f5f5);
}
.pname { font-size: 17px; font-weight: 600; color: #333; line-height: 1.35; min-height: 2.5em; }
.pprice { margin-top: 8px; color: #ff6b35; font-size: 20px; font-weight: 700; }
.empty-menu { padding: 40px; text-align: center; color: #aaa; font-size: 17px; }
.cart-panel {
  background: #fff;
  border-left: 1px solid #eee;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.cart-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  font-size: 19px;
  font-weight: 700;
  border-bottom: 1px solid #f0f0f0;
}
.cart-list { flex: 1; overflow-y: auto; padding: 8px 14px; }
.cart-empty { color: #bbb; text-align: center; padding: 32px 8px; font-size: 16px; }
.cart-line {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 10px;
  padding: 12px 0;
  border-bottom: 1px dashed #f0f0f0;
}
.line-name { font-weight: 600; font-size: 17px; }
.line-opt { font-size: 13px; color: #999; margin-top: 3px; }
.line-ctrl { display: flex; align-items: center; gap: 8px; }
.qty-btn { width: 40px !important; height: 40px !important; font-size: 18px !important; }
.qty { min-width: 28px; text-align: center; font-weight: 700; font-size: 18px; }
.line-amt { grid-column: 2; text-align: right; color: #ff6b35; font-weight: 700; font-size: 17px; }
.cart-foot {
  padding: 14px 16px 18px;
  border-top: 1px solid #eee;
  background: #fafafa;
}
.sum-row { display: flex; justify-content: space-between; font-size: 18px; margin-bottom: 12px; align-items: baseline; }
.sum-row.sub { font-size: 15px; color: #666; margin-bottom: 6px; }
.pack-row .waived { color: #52c41a; font-weight: 600; }
.sum { font-size: 28px; font-weight: 800; color: #ff6b35; }
.remark { margin-bottom: 12px; }
.remark :deep(.el-input__inner) { font-size: 16px; }
.pay-btn { width: 100%; height: 60px; font-size: 20px; font-weight: 700; border-radius: 12px; }
.table-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 12px;
}
.table-chip {
  border: 2px solid #e8e8e8;
  background: #fff;
  border-radius: 12px;
  padding: 20px 10px;
  font-size: 18px;
  font-weight: 600;
  cursor: pointer;
}
.table-chip.on { border-color: #ff6b35; background: #fff8f5; color: #ff6b35; }
.empty-tables { text-align: center; color: #999; padding: 24px; font-size: 16px; }

/* 规格抽屉 */
.spec-drawer :deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: 16px 20px;
  font-size: 20px;
  font-weight: 700;
}
.spec-drawer :deep(.el-drawer__body) {
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.drawer-body {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px 12px;
  border-bottom: 1px solid #f0f0f0;
}
.drawer-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #222;
}
.drawer-price-live {
  font-size: 28px;
  font-weight: 800;
  color: #ff6b35;
}
.opt-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px 20px 16px;
}
.opt-group { margin-bottom: 20px; }
.opt-group-title {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 700;
  color: #333;
}
.opt-group-title .req {
  font-size: 13px;
  color: #ff6b35;
  font-weight: 600;
  margin-left: 6px;
}
.opt-chips {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(128px, 1fr));
  gap: 10px;
}
@media (min-width: 768px) {
  .opt-chips {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
  }
}
@media (min-width: 1024px) {
  .opt-chips {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
.opt-chip {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-width: 0;
  min-height: 56px;
  padding: 12px 18px;
  border: 2px solid #e0e0e0;
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.opt-chip.on {
  border-color: #ff6b35;
  background: #fff8f5;
  color: #ff6b35;
}
.chip-name { font-size: 17px; font-weight: 600; }
.chip-extra { font-size: 14px; margin-top: 2px; opacity: .85; }
.drawer-foot {
  padding: 14px 20px 20px;
  border-top: 1px solid #eee;
  background: #fafafa;
}
.add-cart-btn {
  width: 100%;
  height: 64px;
  font-size: 20px;
  font-weight: 700;
  border-radius: 12px;
}

/* 收款弹窗 */
.pay-dialog :deep(.el-dialog__body) { padding-top: 8px; }
.pay-total-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 18px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f0f0f0;
}
.pay-total { font-size: 32px; font-weight: 800; color: #ff6b35; }
.pay-pack-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 16px;
  padding: 12px;
  background: #fff8f5;
  border-radius: 10px;
  font-size: 15px;
}
.pay-pack-info { display: flex; flex-wrap: wrap; gap: 12px; color: #666; }
.pay-pack-info .waived { color: #52c41a; font-weight: 600; }
.pay-methods {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.pay-method {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  border: none;
  border-radius: 14px;
  padding: 20px 22px;
  cursor: pointer;
  font-size: 20px;
  font-weight: 700;
  color: #fff;
  transition: opacity .15s, transform .1s;
}
.pay-method:active { transform: scale(0.99); }
.pay-method:disabled { opacity: .6; cursor: not-allowed; }
.pay-method.scan { background: linear-gradient(135deg, #07c160 0%, #1677ff 100%); }
.pay-method.cash { background: linear-gradient(135deg, #ff8c42, #ff6b35); }
.pm-label-wrap { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left; }
.pm-sub { font-size: 14px; font-weight: 500; opacity: 0.9; }
.pm-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: rgba(255,255,255,.25);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 800;
}
.pm-label { flex: 1; text-align: left; }

.cash-panel { padding: 4px 0; }
.cash-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 18px;
  margin-bottom: 14px;
}
.cash-amt { font-size: 26px; font-weight: 800; color: #ff6b35; }
.cash-input-wrap label {
  display: block;
  font-size: 16px;
  color: #666;
  margin-bottom: 8px;
}
.cash-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.cash-quick-btn {
  flex: 1;
  min-width: 72px;
  border: 2px solid #e0e0e0;
  background: #fff;
  border-radius: 10px;
  padding: 14px 8px;
  font-size: 18px;
  font-weight: 700;
  color: #333;
  cursor: pointer;
}
.cash-quick-btn.exact {
  border-color: #ff6b35;
  color: #ff6b35;
}
.cash-quick-btn:active {
  background: #fff8f5;
}
.cash-input :deep(.el-input__inner) {
  font-size: 24px;
  font-weight: 700;
  height: 56px;
}
.change-row { margin-top: 16px; padding-top: 14px; border-top: 1px dashed #eee; }
.change-amt { font-size: 30px; font-weight: 800; color: #52c41a; }
.change-amt.warn { color: #f5222d; }
.cash-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}
.cash-actions .el-button { flex: 1; height: 52px; font-size: 17px; }
.cash-confirm { flex: 2 !important; }

.barcode-panel { text-align: center; }
.barcode-panel .scan-provider { font-size: 20px; font-weight: 700; margin-bottom: 8px; }
.scan-detected { font-size: 16px; color: #1677ff; font-weight: 600; margin: 0 0 12px; }
.scan-detected.warn { color: #e6a23c; }
.barcode-input { margin: 16px 0; }
.barcode-input :deep(.el-input__inner) {
  font-size: 20px;
  height: 56px;
  text-align: center;
  letter-spacing: 1px;
}
.scan-hint { color: #888; font-size: 15px; margin: 8px 0 16px; }

.shift-hint { color: #666; margin: 0 0 12px; font-size: 15px; }
.shift-summary { font-size: 15px; line-height: 1.6; color: #444; }
.shift-summary b { color: #ff6b35; font-size: 20px; }
.shift-orders-head { margin-bottom: 16px; font-size: 15px; color: #666; line-height: 1.6; }
.shift-orders-sum b { color: #ff6b35; font-size: 18px; }
.shift-orders-tip { text-align: center; color: #aaa; padding: 32px 8px; font-size: 16px; }
.shift-orders-list { display: flex; flex-direction: column; gap: 10px; }
.shift-order-row {
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  padding: 14px;
  background: #fafafa;
}
.so-top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.so-no { font-size: 15px; font-weight: 600; color: #333; word-break: break-all; }
.so-amt { font-size: 20px; font-weight: 700; color: #ff6b35; flex-shrink: 0; }
.so-meta { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 14px; color: #888; }
.so-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; }
.so-refund-tag { font-size: 13px; font-weight: 600; color: #909399; }
.so-refund-tag.pending { color: #e6a23c; }
.so-refund-tag.ok { color: #67c23a; }
.so-refund-tag.bad { color: #f56c6c; }
.refund-hint { margin: 0 0 12px; color: #666; font-size: 15px; }

.lock-overlay {
  position: fixed;
  inset: 0;
  z-index: 5000;
  background: linear-gradient(160deg, #1d1e1f 0%, #2d3436 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}
.lock-card {
  width: min(400px, 92vw);
  background: #fff;
  border-radius: 16px;
  padding: 32px 24px;
  text-align: center;
}
.lock-logo { width: 80px; border-radius: 10px; margin-bottom: 12px; }
.lock-user { font-size: 20px; font-weight: 700; margin-bottom: 20px; color: #333; }
.lock-input { margin-bottom: 16px; }
.lock-input :deep(.el-input__inner) { font-size: 18px; height: 52px; }
.lock-btn { width: 100%; height: 52px; font-size: 18px; margin-bottom: 8px; }

@media (max-width: 900px) {
  .main { grid-template-columns: 148px 1fr; grid-template-rows: 1fr auto; }
  .cat-nav { padding: 8px 6px; gap: 8px; }
  .cat-item { min-height: 56px; font-size: 17px; padding: 12px 8px; }
  .cart-panel { grid-column: 1 / -1; max-height: 44vh; border-left: none; border-top: 1px solid #eee; }
  .opt-chip { min-width: 88px; min-height: 52px; padding: 10px 14px; }
  .chip-name { font-size: 16px; }
}
</style>
