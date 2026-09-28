<template>
  <div class="pos" :class="{ fullscreen, standalone }">
    <header class="toolbar">
      <div class="left">
        <img src="/logo.png?v=202609080938" alt="logo" class="head-logo" />
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
          <el-radio-button value="DINE_IN">堂食</el-radio-button>
          <el-radio-button value="TAKEAWAY">外带</el-radio-button>
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
        <el-button
          v-if="checkoutOrderId && addingItems"
          size="large"
          type="warning"
          plain
          @click="transferTableDialogVisible = true"
        >
          转台
        </el-button>
        <el-button size="large" @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
        <el-button v-if="auth.isCashier" size="large" @click="goMyStats">我的今日</el-button>
        <el-button v-if="!currentShift" size="large" type="success" @click="shiftOpenVisible = true">开班</el-button>
        <el-button v-if="currentShift" size="large" plain @click="openShiftOrders">本班订单</el-button>
        <el-button v-if="currentShift" size="large" type="warning" plain @click="shiftCloseVisible = true">交班</el-button>
        <el-button size="large" @click="lockScreen">锁屏</el-button>
        <el-button v-if="standalone" size="large" type="danger" plain @click="staffLogout">退出</el-button>
      </div>
    </header>

    <div v-if="loading" class="loading-tip">加载菜单中…</div>

    <!-- 未结算桌号卡片（大按钮触摸友好） -->
    <div v-if="!loading && pendingOrders.length" class="pending-bar">
      <div class="pending-summary">
        <span class="bell-icon">🔔</span>
        <span><b>{{ pendingOrders.length }}</b> 桌待结算</span>
        <el-button size="small" text @click="loadPendingOrders" :loading="pendingLoading">刷新</el-button>
      </div>
      <div class="pending-cards">
        <div
          v-for="o in pendingOrders"
          :key="o.id"
          class="pending-card"
          :class="{ active: checkoutOrderId === o.id }"
        >
          <div class="pc-head">
            <span class="pc-table">{{ o.table_no ? o.table_no + '号桌' : '散客' }}</span>
            <span class="pc-time">{{ formatPendingTime(o.ordered_at) }}</span>
          </div>
          <div class="pc-body">
            <span class="pc-amount">¥{{ o.total_amount }}</span>
            <span class="pc-items">{{ o.item_count }}件</span>
          </div>
          <div class="pc-actions">
            <button class="pc-btn add" @click="resumePendingOrder(o, 'add')">加餐</button>
            <button class="pc-btn pay" @click="resumePendingOrder(o, 'pay')">结算</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!loading" class="main">
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
            v-for="p in pagedProducts"
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
        <div v-if="totalPages > 1" class="pos-pagination">
          <el-pagination
            v-model:current-page="productPage"
            :page-size="pageSize"
            :total="visibleProducts.length"
            layout="prev, pager, next"
            size="small"
            background
          />
        </div>
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
          <div class="member-bind">
            <label>会员（可选）</label>
            <el-select
              v-model="selectedCustomerId"
              filterable
              remote
              clearable
              reserve-keyword
              placeholder="手机号 / 会员号 / 昵称"
              :remote-method="searchPosCustomers"
              :loading="customerSearching"
              style="width: 100%"
              @change="onPosCustomerChange"
            >
              <el-option
                v-for="c in customerOptions"
                :key="c.id"
                :label="posCustomerLabel(c)"
                :value="c.id"
              />
            </el-select>
            <p v-if="selectedCustomerLabel" class="member-tip">已选：{{ selectedCustomerLabel }} · 计入折扣/积分/成单里程碑</p>
            <p v-else class="member-tip muted">不选则按散客收款，不计会员权益</p>
          </div>
          <el-input v-model="remark" placeholder="备注（可选）" size="large" class="remark" />
          <div v-if="addingItems && cart.length" class="add-items-actions">
            <el-button
              size="large"
              plain
              class="add-items-btn"
              :loading="paying"
              @click="addItemsToOrder"
            >
              加菜入单 ¥{{ cartTotal }}
            </el-button>
            <el-button
              size="large"
              type="primary"
              @click="goCheckoutFromAddItems"
            >
              去收款 ¥{{ payableAmount }}
            </el-button>
          </div>
          <div v-else-if="cart.length" class="checkout-actions">
            <el-button
              size="large"
              plain
              class="park-btn"
              :loading="parking"
              @click="parkOrder"
            >
              挂单
            </el-button>
            <el-button
              size="large"
              type="primary"
              class="pay-btn"
              :loading="paying"
              @click="openCheckout"
            >
              去收款 ¥{{ cartTotal }}
            </el-button>
          </div>
          <el-button
            v-else
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
          type="button"
          class="table-chip walk-in"
          :class="{ on: !selectedTableId }"
          @click="pickWalkIn"
        >
          散客
        </button>
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

    <!-- 转台 -->
    <el-dialog v-model="transferTableDialogVisible" title="转台" width="560px" class="table-dialog">
      <div class="table-grid">
        <button
          v-for="t in tables"
          :key="t.id"
          type="button"
          class="table-chip"
          :class="{ on: selectedTableId === t.id }"
          :disabled="t.id === selectedTableId"
          @click="doTransferTable(t.id)"
        >
          {{ t.table_name || t.table_no }}
        </button>
      </div>
      <div v-if="!tables.length" class="empty-tables">暂无可用桌台</div>
    </el-dialog>

    <!-- 规格选择：平板分栏触控，少滑动 -->
    <el-drawer
      v-model="productVisible"
      title="选择规格"
      direction="btt"
      :size="drawerSize"
      class="spec-drawer"
      :show-close="true"
      destroy-on-close
    >
      <div v-if="currentProduct" class="drawer-body">
        <div class="drawer-head">
          <div class="drawer-head-main">
            <h3 class="drawer-title">{{ currentProduct.name }}</h3>
            <div v-if="drawerSelectedSummary" class="drawer-summary">{{ drawerSelectedSummary }}</div>
          </div>
          <div class="drawer-price-live">¥{{ drawerUnitPrice }}</div>
        </div>

        <div class="opt-split">
          <div
            v-if="(currentProduct.option_groups || []).length > 1"
            class="opt-nav"
          >
            <button
              v-for="g in currentProduct.option_groups"
              :key="g.id"
              type="button"
              class="opt-nav-item"
              :class="{
                on: activeOptGroupId === g.id,
                done: isGroupFilled(g),
                missing: !!groupNavById[g.id]?.missing,
              }"
              @click="activeOptGroupId = g.id"
            >
              <span class="nav-name">{{ g.name }}</span>
              <span
                v-if="groupNavById[g.id]?.text"
                class="nav-value"
                :class="{ missing: !!groupNavById[g.id]?.missing }"
              >{{ groupNavById[g.id].text }}</span>
            </button>
          </div>

          <div class="opt-pane">
            <template v-if="activeOptGroup">
              <h4 class="opt-pane-title">
                {{ activeOptGroup.name }}
                <span v-if="activeOptGroup.is_required" class="req">必选</span>
                <span v-else class="opt-hint">可多选或不选</span>
              </h4>
              <div
                class="opt-tiles"
                :class="{
                  'few': (activeOptGroup.options || []).length <= 4,
                  'many': (activeOptGroup.options || []).length > 6,
                }"
              >
                <button
                  v-for="o in activeOptGroup.options"
                  :key="o.id"
                  type="button"
                  class="opt-tile"
                  :class="{
                    on:
                      activeOptGroup.selection_type === 'SINGLE'
                        ? selectedOptions[activeOptGroup.id] === o.id
                        : isMultiSelected(activeOptGroup.id, o.id),
                  }"
                  @click="onPickOption(activeOptGroup, o)"
                >
                  <span class="tile-name">{{ o.name }}</span>
                  <span v-if="+o.extra_price" class="tile-extra">+¥{{ o.extra_price }}</span>
                  <span v-else class="tile-extra muted">不加价</span>
                </button>
              </div>
            </template>
            <div v-else class="opt-empty">该商品无需选择规格</div>
          </div>
        </div>

        <div class="drawer-foot">
          <el-button
            v-if="activeOptGroupIndex > 0"
            size="large"
            class="nav-step-btn"
            @click="goPrevOptGroup"
          >
            上一组
          </el-button>
          <el-button
            v-if="activeOptGroupIndex >= 0 && activeOptGroupIndex < optGroupCount - 1"
            size="large"
            class="nav-step-btn"
            @click="goNextOptGroup"
          >
            下一组
          </el-button>
          <el-button type="primary" class="add-cart-btn" @click="addToCart">
            加入购物车 ¥{{ drawerUnitPrice }}
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 收款：支持组合分笔（微信/支付宝/现金） -->
    <el-dialog
      v-model="payDialogVisible"
      title="收款"
      width="560px"
      class="pay-dialog"
      :close-on-click-modal="false"
      @closed="resetCheckout"
    >
      <div class="pay-summary">
        <div class="pay-summary-row">
          <span>应收</span>
          <b class="pay-total">¥{{ checkoutAmount }}</b>
        </div>
        <div class="pay-summary-row">
          <span>已收</span>
          <b class="pay-paid">¥{{ paidAmountDisplay }}</b>
        </div>
        <div class="pay-summary-row remain">
          <span>待收</span>
          <b class="pay-remain">¥{{ remainingAmountDisplay }}</b>
        </div>
      </div>

      <div v-if="tenders.length" class="tender-list">
        <div v-for="(t, i) in tenders" :key="t.payment_no || i" class="tender-item">
          <span>{{ tenderLabel(t.provider) }}</span>
          <span>¥{{ t.amount }}</span>
        </div>
      </div>

      <div v-if="diningType === 'TAKEAWAY' && !tenders.length" class="pay-pack-row">
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

      <!-- 本笔金额 + 选方式 -->
      <div v-if="payStep === 'pick'" class="pay-pick">
        <div v-if="checkoutOrderId" class="add-items-hint">
          <span>订单 {{ checkoutOrderNo }} · 待收 ¥{{ remainingAmountDisplay }}</span>
          <el-button size="small" plain @click="enterAddItemsMode">加菜</el-button>
        </div>
        <div v-if="checkoutOrderId" class="member-bind pay-member">
          <label>本单会员</label>
          <el-select
            v-model="selectedCustomerId"
            filterable
            remote
            clearable
            reserve-keyword
            placeholder="手机号 / 会员号 / 昵称"
            :remote-method="searchPosCustomers"
            :loading="customerSearching"
            style="width: 100%"
            @change="bindCustomerOnCheckoutOrder"
          >
            <el-option
              v-for="c in customerOptions"
              :key="c.id"
              :label="posCustomerLabel(c)"
              :value="c.id"
            />
          </el-select>
          <p class="member-tip">{{ selectedCustomerLabel || '散客（不计入成单里程碑）' }}</p>
        </div>
        <div class="tender-amt-wrap">
          <label>本笔收款</label>
          <div class="tender-quick">
            <button type="button" class="cash-quick-btn exact" @click="setTenderExact">收清待收</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(10)">¥10</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(20)">¥20</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(50)">¥50</button>
          </div>
          <el-input
            v-model="tenderAmount"
            size="large"
            inputmode="decimal"
            class="cash-input"
            @input="onTenderInput"
          >
            <template #prefix>¥</template>
          </el-input>
          <p class="tender-tip">可分多笔：如微信 10 + 支付宝 5 + 现金收清</p>
        </div>
        <div class="pay-methods">
          <button type="button" class="pay-method scan" :disabled="paying || !tenderAmountOk" @click="startBarcodePay">
            <span class="pm-icon">扫</span>
            <span class="pm-label-wrap">
              <span class="pm-label">扫码收 ¥{{ tenderAmountDisplay }}</span>
              <span class="pm-sub">自动识别微信 / 支付宝</span>
            </span>
          </button>
          <button type="button" class="pay-method cash" :disabled="paying || !tenderAmountOk" @click="openCashPay">
            <span class="pm-icon">¥</span>
            <span class="pm-label">现金收 ¥{{ tenderAmountDisplay }}</span>
          </button>
        </div>
      </div>

      <!-- 现金 -->
      <div v-else-if="payStep === 'cash'" class="cash-panel">
        <div class="cash-row">
          <span>本笔入账</span>
          <span class="cash-amt">¥{{ activeTenderAmount }}</span>
        </div>
        <div class="cash-input-wrap">
          <label>顾客实付现金</label>
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
        <div v-if="canRoundDown" class="cash-row round-down-row">
          <el-checkbox v-model="cashRoundDown">抹零（免除 ¥{{ roundDownAmount }} 零头）</el-checkbox>
        </div>
        <div class="cash-actions">
          <el-button size="large" @click="cancelCashPay">返回</el-button>
          <el-button
            type="primary"
            size="large"
            class="cash-confirm"
            :loading="paying"
            :disabled="cashConfirmDisabled"
            @click="confirmCashPay"
          >
            确认本笔现金
          </el-button>
        </div>
      </div>

      <!-- 扫顾客付款码 -->
      <div v-else-if="payStep === 'barcode'" class="barcode-panel">
        <div class="scan-provider">本笔扫码 ¥{{ activeTenderAmount }}</div>
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
            确认本笔扫码
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
    <el-dialog v-model="refundApplyVisible" title="申请退款" width="460px" :close-on-click-modal="false">
      <template v-if="refundTarget">
        <p class="refund-hint">
          订单 {{ refundTarget.order_no }} · 实收 ¥{{ refundTarget.total_amount }}
          · 可退 <b>¥{{ refundTargetRefundable }}</b>
        </p>
        <div v-if="refundTargetTenders.length" class="refund-tenders">
          <div v-for="(t, i) in refundTargetTenders" :key="t.payment_no || i" class="refund-tender-row">
            <span>{{ tenderLabel(t.provider) }}</span>
            <span>实收 ¥{{ t.amount }} · 可退 ¥{{ t.refundable }}</span>
          </div>
          <p class="refund-alloc-tip">组合支付退款：优先退现金，不足再退微信/支付宝原路返回</p>
        </div>
      </template>
      <el-input
        v-model="refundAmount"
        size="large"
        inputmode="decimal"
        placeholder="退款金额（默认全额可退）"
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
        <img src="/logo.png?v=202609080938" alt="" class="lock-logo" />
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
  checkCategoryRequire,
  collectOptionIds,
  mergeCartItem,
  validateCategoryRequires,
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
const selectedCustomerId = ref<number | undefined>()
const customerOptions = ref<any[]>([])
const customerSearching = ref(false)
const selectedCustomerLabel = computed(() => {
  if (!selectedCustomerId.value) return ''
  const c = customerOptions.value.find((x) => x.id === selectedCustomerId.value)
  return c ? posCustomerLabel(c) : `会员#${selectedCustomerId.value}`
})
const tableDialogVisible = ref(false)
const productVisible = ref(false)
const currentProduct = ref<any>(null)
const selectedOptions = reactive<Record<number, number>>({})
const multiOptions = reactive<Record<number, number[]>>({})

const payDialogVisible = ref(false)
const payStep = ref<'pick' | 'cash' | 'barcode'>('pick')
const checkoutOrderId = ref<number | null>(null)
const posCheckoutNonce = ref('')
const checkoutOrderNo = ref('')
const checkoutAmount = ref('0.00')
const paidAmount = ref('0.00')
const remainingAmount = ref('0.00')
const tenders = ref<any[]>([])
const tenderAmount = ref('')
/** 进入现金/扫码步骤后锁定的本笔金额，避免建单或进度回写改掉 */
const lockedTenderAmount = ref('')
const cashReceived = ref('')
const barcodeInput = ref('')
const barcodeInputRef = ref<any>(null)
/** 现金抹零 */
const cashRoundDown = ref(false)
/** 加菜模式：已建订单后返回菜单继续点餐 */
const addingItems = ref(false)
/** 转台对话框 */
const transferTableDialogVisible = ref(false)
/** 未结算订单列表 */
const pendingOrders = ref<any[]>([])
const pendingLoading = ref(false)
/** 挂单中 */
const parking = ref(false)
/** 当前展开操作按钮的未结算订单 ID */
const activePendingId = ref<number | null>(null)

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

const drawerSize = computed(() => {
  if (typeof window === 'undefined') return '88%'
  if (window.innerWidth >= 900) return '92%'
  if (window.innerWidth >= 768) return '90%'
  return '88%'
})

const activeOptGroupId = ref<number | null>(null)

const optGroups = computed(() => currentProduct.value?.option_groups || [])

const groupNavById = computed(() => {
  const map: Record<number, { text: string; missing: boolean }> = {}
  for (const g of optGroups.value) {
    map[g.id] = groupNavMeta(g)
  }
  return map
})

const optGroupCount = computed(() => optGroups.value.length)

const activeOptGroupIndex = computed(() =>
  optGroups.value.findIndex((g: any) => g.id === activeOptGroupId.value),
)

const activeOptGroup = computed(() => {
  const list = optGroups.value
  if (!list.length) return null
  return list.find((g: any) => g.id === activeOptGroupId.value) || list[0]
})

const drawerSelectedSummary = computed(() => {
  if (!currentProduct.value) return ''
  return buildOptionLabels(currentProduct.value, selectedOptions, multiOptions).join(' · ')
})

const drawerUnitPrice = computed(() => {
  if (!currentProduct.value) return '0.00'
  return calcUnitPrice(currentProduct.value, selectedOptions, multiOptions).toFixed(2)
})

const tableLabel = computed(() => {
  if (diningType.value !== 'DINE_IN') return ''
  if (!selectedTableId.value) return '散客 / 开台'
  const t = tables.value.find((x) => x.id === selectedTableId.value)
  return t ? (t.table_name || t.table_no) : '选择桌号'
})

const visibleProducts = computed(() =>
  products.value.filter((p) => !activeCat.value || p.category_id === activeCat.value),
)
const pageSize = 24
const productPage = ref(1)
const totalPages = computed(() => Math.ceil(visibleProducts.value.length / pageSize))
const pagedProducts = computed(() => {
  const start = (productPage.value - 1) * pageSize
  return visibleProducts.value.slice(start, start + pageSize)
})
watch([activeCat], () => { productPage.value = 1 })

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

const paidAmountDisplay = computed(() => paidAmount.value || '0.00')
const remainingAmountDisplay = computed(() => remainingAmount.value || checkoutAmount.value || '0.00')
const tenderAmountDisplay = computed(() => {
  const n = parseFloat(tenderAmount.value)
  if (!Number.isFinite(n) || n <= 0) return remainingAmountDisplay.value
  return n.toFixed(2)
})
/** 现金/扫码步骤展示与提交用的本笔金额 */
const activeTenderAmount = computed(() => {
  if ((payStep.value === 'cash' || payStep.value === 'barcode') && lockedTenderAmount.value) {
    return lockedTenderAmount.value
  }
  return tenderAmountDisplay.value
})
const tenderAmountOk = computed(() => {
  const n = parseFloat(tenderAmountDisplay.value)
  const rem = parseFloat(remainingAmountDisplay.value)
  return Number.isFinite(n) && n > 0 && Number.isFinite(rem) && n <= rem + 0.001
})

const cashChangeNum = computed(() => {
  const received = parseFloat(cashReceived.value)
  const due = parseFloat(activeTenderAmount.value)
  if (!Number.isFinite(received) || !Number.isFinite(due)) return null
  return Math.round((received - due) * 100) / 100
})

const cashChangeDisplay = computed(() => {
  if (cashChangeNum.value === null) return '—'
  return cashChangeNum.value.toFixed(2)
})

const cashChangeWarn = computed(() => cashChangeNum.value !== null && cashChangeNum.value < 0)

/** 抹零金额：待收金额小数部分（仅全额收款时有意义） */
const roundDownAmount = computed(() => {
  const due = parseFloat(activeTenderAmount.value)
  if (!Number.isFinite(due)) return '0.00'
  const cents = due * 100
  const remainder = cents % 100
  return (remainder / 100).toFixed(2)
})
const canRoundDown = computed(() => {
  const due = parseFloat(activeTenderAmount.value)
  const rem = parseFloat(remainingAmountDisplay.value)
  if (!Number.isFinite(due) || !Number.isFinite(rem)) return false
  return Math.abs(due - rem) < 0.001 && due > 0
})

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

function isFlagOn(v: unknown) {
  return v === true || v === 1 || v === '1'
}

function isDefaultOption(o: any) {
  return isFlagOn(o?.is_default)
}

function activeOptionsOf(g: any) {
  return (g?.options || []).filter((o: any) => o && o.status !== 0)
}

function findOptionById(g: any, optionId: number | string | null | undefined) {
  if (optionId == null || optionId === '') return null
  return (g?.options || []).find((o: any) => String(o.id) === String(optionId)) || null
}

/** 组内默认项：is_default，其次单选 NORMAL */
function defaultOptionsOf(g: any) {
  const opts = activeOptionsOf(g)
  if (!opts.length) return []
  if (g.selection_type === 'SINGLE') {
    const def = opts.find(isDefaultOption) || opts.find((o: any) => o.code === 'NORMAL')
    return def ? [def] : []
  }
  return opts.filter(isDefaultOption)
}

function selectedNamesOf(g: any): string[] {
  if (!g) return []
  if (g.selection_type === 'SINGLE') {
    const opt = findOptionById(g, selectedOptions[g.id])
    return opt?.name ? [opt.name] : []
  }
  return (multiOptions[g.id] || [])
    .map((id: number) => findOptionById(g, id)?.name)
    .filter(Boolean)
}

/** 左侧摘要：已选 > 默认 > 必选未选显示【未选】 */
function groupNavMeta(g: any): { text: string; missing: boolean } {
  const selected = selectedNamesOf(g)
  if (selected.length) return { text: selected.join('、'), missing: false }
  const defs = defaultOptionsOf(g).map((o: any) => o.name).filter(Boolean)
  if (defs.length) return { text: defs.join('、'), missing: false }
  if (isFlagOn(g?.is_required)) return { text: '【未选】', missing: true }
  return { text: '', missing: false }
}

function isGroupFilled(g: any) {
  if (!g) return false
  if (g.selection_type === 'SINGLE') return !!findOptionById(g, selectedOptions[g.id])
  return (multiOptions[g.id] || []).length > 0
}

function applyGroupDefaults(groups: any[]) {
  for (const g of groups || []) {
    const defs = defaultOptionsOf(g)
    if (g.selection_type === 'SINGLE') {
      if (defs[0]) selectedOptions[g.id] = defs[0].id
    } else {
      multiOptions[g.id] = defs.map((o: any) => o.id)
    }
  }
}

function goNextOptGroup() {
  const list = optGroups.value
  const idx = activeOptGroupIndex.value
  if (idx >= 0 && idx < list.length - 1) activeOptGroupId.value = list[idx + 1].id
}

function goPrevOptGroup() {
  const list = optGroups.value
  const idx = activeOptGroupIndex.value
  if (idx > 0) activeOptGroupId.value = list[idx - 1].id
}

/** 单选后自动跳下一组，加快触控点餐 */
function onPickOption(g: any, o: any) {
  if (g.selection_type === 'SINGLE') {
    selectedOptions[g.id] = o.id
    goNextOptGroup()
  } else {
    toggleMultiOption(g.id, o.id)
  }
}

function onCashInput() {
  cashReceived.value = cashReceived.value.replace(/[^\d.]/g, '')
}

function onTenderInput() {
  tenderAmount.value = tenderAmount.value.replace(/[^\d.]/g, '')
}

function tenderLabel(provider: string) {
  const u = (provider || '').toUpperCase()
  if (u === 'WECHAT') return '微信'
  if (u === 'ALIPAY') return '支付宝'
  if (u === 'CASH') return '现金'
  if (u === 'MIXED') return '组合支付'
  return provider || '支付'
}

function applyPayProgress(data: any) {
  if (!data) return
  if (data.total_amount != null) checkoutAmount.value = String(data.total_amount)
  if (data.paid_amount != null) paidAmount.value = String(data.paid_amount)
  if (data.remaining_amount != null) remainingAmount.value = String(data.remaining_amount)
  if (Array.isArray(data.tenders)) tenders.value = data.tenders
  const rem = parseFloat(remainingAmount.value)
  if (Number.isFinite(rem) && rem > 0) {
    tenderAmount.value = rem.toFixed(2)
  } else {
    tenderAmount.value = '0.00'
  }
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
    const [cats, ...prodPages] = await Promise.all([
      api.get('/categories', { params: { store_id: sid } }),
      (async () => {
        const all: any[] = []
        let page = 1
        let totalPages = 1
        do {
          const res: any = await api.get('/products', {
            params: { store_id: sid, page, page_size: 200 },
          })
          const data = res?.data || {}
          all.push(...(data.items || []))
          totalPages = Math.max(1, Number(data.total_pages || 1))
          page += 1
        } while (page <= totalPages && page <= 50)
        return all
      })(),
    ])
    categories.value = cats.data || []
    products.value = prodPages[0].map(decorate)
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
  posCheckoutNonce.value = ''
}

function toggleWaivePacking(waive: boolean) {
  waivePackingFee.value = waive
  invalidateCheckoutOrder()
  if (payDialogVisible.value) {
    checkoutAmount.value = cartTotal.value
    paidAmount.value = '0.00'
    remainingAmount.value = cartTotal.value
    tenders.value = []
    tenderAmount.value = cartTotal.value
  }
}

function pickTable(t: any) {
  selectedTableId.value = t.id
  tableDialogVisible.value = false
}

function pickWalkIn() {
  selectedTableId.value = null
  tableDialogVisible.value = false
}

function clearCart() {
  cart.value = []
  posCheckoutNonce.value = ''
  invalidateCheckoutOrder()
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
  const groups = currentProduct.value.option_groups || []
  applyGroupDefaults(groups)
  activeOptGroupId.value = groups[0]?.id ?? null
  productVisible.value = true
}

function addToCartFocusMissing() {
  const p = currentProduct.value
  if (!p) return
  for (const g of p.option_groups || []) {
    if (!isFlagOn(g.is_required)) continue
    const has =
      (g.selection_type === 'SINGLE' && selectedOptions[g.id]) ||
      (g.selection_type === 'MULTIPLE' && (multiOptions[g.id] || []).length)
    if (!has) {
      activeOptGroupId.value = g.id
      ElMessage.warning(`请选择：${g.name}`)
      return true
    }
  }
  return false
}

function addToCart() {
  const p = currentProduct.value
  if (!p) return
  if (addToCartFocusMissing()) return
  const requireCheck = checkCategoryRequire(p, cart.value, products.value, categories.value)
  if (!requireCheck.ok) {
    ElMessage.warning(requireCheck.message)
    return
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
    category_id: p.category_id,
    require_category_ids: p.require_category_ids || [],
    require_product_ids: p.require_product_ids || [],
  })
  productVisible.value = false
}

function newPosCheckoutNonce() {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
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
  if (!posCheckoutNonce.value) posCheckoutNonce.value = newPosCheckoutNonce()
  return `pos_${Math.abs(h).toString(16)}_${posCheckoutNonce.value}`
}

function validateBeforeCheckout(): boolean {
  if (!cart.value.length) return false
  const sid = effectiveStoreId()
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return false
  }
  const catIssues = validateCategoryRequires(cart.value, products.value, categories.value)
  if (catIssues.length) {
    ElMessage.warning(catIssues[0].message)
    return false
  }
  return true
}

async function createPosOrder() {
  const sid = effectiveStoreId()
  try {
    const orderRes = await api.post('/orders/pos', {
      store_id: sid,
      table_id: diningType.value === 'DINE_IN' ? selectedTableId.value : null,
      customer_id: selectedCustomerId.value || null,
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
    if (orderRes.data?.total_amount) {
      remainingAmount.value = orderRes.data.total_amount
    }
    return orderRes.data
  } catch (e: any) {
    // 已付单占用幂等键：清 nonce，下次可再下同款
    if (e?.code === 40903 || /幂等键.*已结束|重新下单/.test(e?.message || '')) {
      invalidateCheckoutOrder()
    }
    throw e
  }
}

function posCustomerLabel(c: any) {
  const name = c.display_name || c.nickname || '会员'
  const phone = c.phone ? ` ${c.phone}` : ''
  const no = c.customer_no ? ` · ${c.customer_no}` : ''
  return `${name}${phone}${no}`
}

async function searchPosCustomers(keyword: string) {
  const kw = (keyword || '').trim()
  if (!kw) {
    customerOptions.value = []
    return
  }
  customerSearching.value = true
  try {
    const res = await api.get('/pos/customers/search', {
      params: { keyword: kw, store_id: effectiveStoreId() || undefined },
    })
    customerOptions.value = res.data?.items || []
  } catch {
    customerOptions.value = []
  } finally {
    customerSearching.value = false
  }
}

function onPosCustomerChange() {
  // 仅本地选中；建单时带上 customer_id
}

async function bindCustomerOnCheckoutOrder() {
  if (!checkoutOrderId.value) return
  try {
    const res = await api.post(`/orders/${checkoutOrderId.value}/bind-customer`, {
      customer_id: selectedCustomerId.value || null,
    })
    if (res.data?.total_amount != null) {
      checkoutAmount.value = res.data.total_amount
      remainingAmount.value = res.data.total_amount
      const paid = parseFloat(paidAmount.value || '0') || 0
      const total = parseFloat(res.data.total_amount) || 0
      remainingAmount.value = Math.max(0, total - paid).toFixed(2)
      if (!tenders.value.length) {
        tenderAmount.value = remainingAmount.value
      }
    }
    if (res.data?.customer_id && res.data?.customer_name) {
      const exists = customerOptions.value.some((x) => x.id === res.data.customer_id)
      if (!exists) {
        customerOptions.value = [
          {
            id: res.data.customer_id,
            display_name: res.data.customer_name,
            phone: res.data.customer_phone,
          },
          ...customerOptions.value,
        ]
      }
    }
    ElMessage.success(selectedCustomerId.value ? '已绑定会员并重算优惠' : '已清除会员绑定')
  } catch (e: any) {
    ElMessage.error(e.message || '绑定会员失败')
  }
}

function resetCheckout() {
  payStep.value = 'pick'
  invalidateCheckoutOrder()
  cashReceived.value = ''
  barcodeInput.value = ''
  paidAmount.value = '0.00'
  remainingAmount.value = '0.00'
  tenders.value = []
  tenderAmount.value = ''
  lockedTenderAmount.value = ''
  cashRoundDown.value = false
  addingItems.value = false
}

function setCashQuick(amt: number) {
  cashReceived.value = String(amt)
}

function setCashExact() {
  cashReceived.value = activeTenderAmount.value
}

function setTenderExact() {
  tenderAmount.value = remainingAmountDisplay.value
}

function setTenderQuick(amt: number) {
  const rem = parseFloat(remainingAmountDisplay.value)
  if (!Number.isFinite(rem)) {
    tenderAmount.value = String(amt)
    return
  }
  tenderAmount.value = Math.min(amt, rem).toFixed(2)
}

function finishCheckout(msg: string) {
  ElMessage.success(msg)
  cart.value = []
  remark.value = ''
  selectedCustomerId.value = undefined
  customerOptions.value = []
  waivePackingFee.value = false
  payDialogVisible.value = false
  resetCheckout()
  loadShift()
  loadPendingOrders()
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
  if (m === 'MIXED') return '组合支付'
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

/** 加载未结算订单列表 */
async function loadPendingOrders() {
  const sid = effectiveStoreId()
  if (!sid) return
  pendingLoading.value = true
  try {
    const res = await api.get('/orders/pos/pending', { params: { store_id: sid } })
    pendingOrders.value = res.data || []
  } catch {
    pendingOrders.value = []
  } finally {
    pendingLoading.value = false
  }
}

/** 格式化等待时间：刚刚 / N分钟前 */
function formatPendingTime(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const mins = Math.floor((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  const hrs = Math.floor(mins / 60)
  return `${hrs}小时前`
}

/** 恢复挂单订单：mode=add 继续加餐，mode=pay 去结算 */
function resumePendingOrder(o: any, mode: 'add' | 'pay') {
  checkoutOrderId.value = o.id
  checkoutOrderNo.value = o.order_no
  checkoutAmount.value = o.total_amount
  remainingAmount.value = o.total_amount
  paidAmount.value = '0.00'
  cart.value = []
  remark.value = o.remark || ''
  selectedTableId.value = o.table_id || null
  activePendingId.value = null
  selectedCustomerId.value = o.customer_id || undefined
  if (o.customer_id) {
    customerOptions.value = [
      {
        id: o.customer_id,
        display_name: o.customer_name,
        phone: o.customer_phone,
      },
    ]
  } else {
    customerOptions.value = []
  }

  if (mode === 'add') {
    addingItems.value = true
    ElMessage.success(`已切换到 ${o.table_no ? o.table_no + '号桌' : '散客'}，继续加餐`)
  } else {
    addingItems.value = false
    payStep.value = 'pick'
    tenderAmount.value = o.total_amount
    payDialogVisible.value = true
  }
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
  const rem = parseFloat(o.refundable_amount ?? o.total_amount ?? '0')
  if (Number.isFinite(rem) && rem <= 0) return false
  return true
}

const refundTargetRefundable = computed(() => {
  const o = refundTarget.value
  if (!o) return '0.00'
  return String(o.refundable_amount ?? o.total_amount ?? '0.00')
})

const refundTargetTenders = computed(() => {
  const list = refundTarget.value?.payment_tenders
  return Array.isArray(list) ? list : []
})

function openRefundApply(o: any) {
  refundTarget.value = o
  refundAmount.value = o.refundable_amount || o.total_amount || ''
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
    // 无班次时自动弹开班对话框（所有有 POS 权限的角色）
    if (!currentShift.value) {
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
  paidAmount.value = '0.00'
  remainingAmount.value = cartTotal.value
  tenders.value = []
  tenderAmount.value = cartTotal.value
  payDialogVisible.value = true
}

/** 挂单：创建订单后不进入支付，回到菜单继续操作 */
async function parkOrder() {
  if (!validateBeforeCheckout()) return
  parking.value = true
  try {
    await createPosOrder()
    const tableName = selectedTableId.value
      ? (tables.value.find((t) => t.id === selectedTableId.value)?.table_name || tables.value.find((t) => t.id === selectedTableId.value)?.table_no || '')
      : '散客'
    ElMessage.success(`挂单成功：${tableName} ¥${checkoutAmount.value}`)
    // 清空购物车，回到菜单
    cart.value = []
    remark.value = ''
    waivePackingFee.value = false
    invalidateCheckoutOrder()
    addingItems.value = false
    // 刷新未结算列表
    await loadPendingOrders()
  } catch (e: any) {
    ElMessage.error(e.message || '挂单失败')
  } finally {
    parking.value = false
  }
}

/** 进入加菜模式：关闭收款弹窗，清空购物车，回到菜单继续点餐 */
function enterAddItemsMode() {
  payDialogVisible.value = false
  cart.value = []
  remark.value = ''
  addingItems.value = true
}

/** 加菜入单：将购物车中的商品追加到已有订单 */
async function addItemsToOrder() {
  if (!cart.value.length) {
    ElMessage.warning('请先选择要加的菜品')
    return
  }
  if (!checkoutOrderId.value) {
    ElMessage.error('订单不存在，请重新下单')
    return
  }
  const catIssues = validateCategoryRequires(cart.value, products.value, categories.value)
  if (catIssues.length) {
    ElMessage.warning(catIssues[0].message)
    return
  }
  paying.value = true
  try {
    const res = await api.post(`/orders/${checkoutOrderId.value}/add-items`, {
      items: cart.value.map((c) => ({
        product_id: c.product_id,
        quantity: c.quantity || 1,
        option_ids: c.option_ids || [],
      })),
      idempotency_key: `add_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    })
    checkoutAmount.value = res.data?.total_amount || checkoutAmount.value
    remainingAmount.value = res.data?.total_amount || remainingAmount.value
    paidAmount.value = '0.00'
    cart.value = []
    remark.value = ''
    addingItems.value = false
    ElMessage.success('加菜成功')
    loadPendingOrders()
  } catch (e: any) {
    ElMessage.error(e.message || '加菜失败')
  } finally {
    paying.value = false
  }
}

/** 加菜后直接去收款 */
function goCheckoutFromAddItems() {
  if (!checkoutOrderId.value) {
    ElMessage.error('订单不存在，请重新下单')
    return
  }
  payStep.value = 'pick'
  tenderAmount.value = remainingAmount.value
  payDialogVisible.value = true
}

/** 转台 */
async function doTransferTable(newTableId: number) {
  if (!checkoutOrderId.value) {
    ElMessage.error('订单不存在')
    return
  }
  if (!newTableId) {
    ElMessage.warning('请选择目标桌号')
    return
  }
  paying.value = true
  try {
    await api.post(`/orders/${checkoutOrderId.value}/transfer-table?new_table_id=${newTableId}`)
    selectedTableId.value = newTableId
    transferTableDialogVisible.value = false
    ElMessage.success('转台成功')
  } catch (e: any) {
    ElMessage.error(e.message || '转台失败')
  } finally {
    paying.value = false
  }
}

async function ensureOrder() {
  if (checkoutOrderId.value) return checkoutOrderId.value
  // 建单前先记住本笔金额，避免被重置成全额待收
  const keepTender = tenderAmount.value
  paying.value = true
  try {
    const data = await createPosOrder()
    checkoutAmount.value = data.total_amount || cartTotal.value
    paidAmount.value = '0.00'
    remainingAmount.value = checkoutAmount.value
    const keep = parseFloat(keepTender)
    const rem = parseFloat(String(remainingAmount.value))
    if (Number.isFinite(keep) && keep > 0 && Number.isFinite(rem) && keep <= rem + 0.001) {
      tenderAmount.value = keep.toFixed(2)
    } else {
      tenderAmount.value = remainingAmount.value
    }
    return data.id as number
  } finally {
    paying.value = false
  }
}

function openCashPay() {
  if (!tenderAmountOk.value) {
    ElMessage.warning('请输入正确的本笔金额（不超过待收）')
    return
  }
  lockedTenderAmount.value = tenderAmountDisplay.value
  payStep.value = 'cash'
  cashReceived.value = lockedTenderAmount.value
}

async function confirmCashPay() {
  if (!validateBeforeCheckout()) return
  const amount = lockedTenderAmount.value || tenderAmountDisplay.value
  const amt = parseFloat(amount)
  const rem = parseFloat(remainingAmountDisplay.value)
  if (!Number.isFinite(amt) || amt <= 0 || (Number.isFinite(rem) && amt > rem + 0.001)) {
    ElMessage.warning('本笔金额无效')
    return
  }
  paying.value = true
  try {
    const orderId = await ensureOrder()
    const body: { amount: string; received_amount?: string; round_down?: boolean } = { amount }
    if (cashReceived.value.trim()) {
      body.received_amount = cashReceived.value.trim()
    }
    if (cashRoundDown.value) {
      body.round_down = true
    }
    const payRes = await api.post(`/payments/orders/${orderId}/cash`, body)
    applyPayProgress(payRes.data)
    const change = payRes.data?.change_amount
    const remLeft = parseFloat(String(payRes.data?.remaining_amount ?? '0'))
    const done = payRes.data?.fully_paid === true || (Number.isFinite(remLeft) && remLeft <= 0.001)
    if (done) {
      const msg = change != null && Number(change) > 0
        ? `收款完成 · ${payRes.data?.order_no} · 找零 ¥${change}`
        : `收款完成 · ${payRes.data?.order_no || checkoutOrderNo.value}`
      finishCheckout(msg)
      return
    }
    ElMessage.success(
      change != null && Number(change) > 0
        ? `本笔现金已收 · 找零 ¥${change} · 待收 ¥${payRes.data?.remaining_amount}`
        : `本笔现金已收 · 待收 ¥${payRes.data?.remaining_amount}`,
    )
    lockedTenderAmount.value = ''
    payStep.value = 'pick'
    cashReceived.value = ''
  } catch (e: any) {
    ElMessage.error(e.message || '收款失败')
  } finally {
    paying.value = false
  }
}

async function startBarcodePay() {
  if (!validateBeforeCheckout()) return
  if (!tenderAmountOk.value) {
    ElMessage.warning('请输入正确的本笔金额（不超过待收）')
    return
  }
  // 先锁定本笔金额，再建单（建单不得改本笔）
  lockedTenderAmount.value = tenderAmountDisplay.value
  paying.value = true
  try {
    await ensureOrder()
  } catch (e: any) {
    lockedTenderAmount.value = ''
    ElMessage.error(e.message || '创建订单失败')
    return
  } finally {
    paying.value = false
  }
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
  const amount = lockedTenderAmount.value || tenderAmountDisplay.value
  const amt = parseFloat(amount)
  const rem = parseFloat(remainingAmountDisplay.value)
  if (!Number.isFinite(amt) || amt <= 0 || (Number.isFinite(rem) && amt > rem + 0.001)) {
    ElMessage.warning('本笔金额无效')
    return
  }
  paying.value = true
  const payingMsg = ElMessage({
    message: `正在收款 ¥${amount}，请顾客确认支付…`,
    type: 'info',
    duration: 0,
  })
  try {
    const orderId = await ensureOrder()
    const payRes = await api.post(
      `/payments/orders/${orderId}/pos-scan`,
      { auth_code: code, amount },
      { timeout: 90000 },
    )
    applyPayProgress(payRes.data)
    const remLeft = parseFloat(String(payRes.data?.remaining_amount ?? '0'))
    const charged = String(payRes.data?.amount ?? '')
    // 必须待收归零才关窗；禁止把「本笔 PAID」当成整单成功
    const done = payRes.data?.fully_paid === true || (Number.isFinite(remLeft) && remLeft <= 0.001)
    if (done) {
      finishCheckout(`收款完成 · ${payRes.data?.order_no || checkoutOrderNo.value}`)
      return
    }
    if (
      payRes.data?.mock_settled
      || payRes.data?.status === 'PAID'
      || payRes.data?.payment_status === 'PARTIAL_PAID'
    ) {
      ElMessage.success(
        `${tenderLabel(payRes.data?.provider)} ¥${charged || amount} 已收 · 还差 ¥${payRes.data?.remaining_amount}`,
      )
      lockedTenderAmount.value = ''
      payStep.value = 'pick'
      barcodeInput.value = ''
      return
    }
    ElMessage.error('支付未完成，请重试')
  } catch (e: any) {
    const msg = e.message || '收款失败'
    if (msg.includes('已支付') && !msg.includes('部分')) {
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
  lockedTenderAmount.value = ''
}

function cancelCashPay() {
  payStep.value = 'pick'
  cashReceived.value = ''
  lockedTenderAmount.value = ''
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
  await loadPendingOrders()
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
.head-logo { width: 48px; height: 48px; object-fit: contain; border-radius: 8px; background: #fff; }
.head-text .title { font-size: 22px; font-weight: 700; color: #222; }
.head-text .sub { font-size: 14px; color: #888; }
.store-select { width: 180px; }
.dine-toggle :deep(.el-radio-button__inner) { padding: 12px 24px; font-size: 17px; }
.table-btn { min-width: 130px; font-size: 17px; font-weight: 600; }
.loading-tip { padding: 48px; text-align: center; color: #888; font-size: 20px; }
.pending-bar { background: #fff8f5; border-bottom: 2px solid #ff6b35; padding: 10px 16px; }
.pending-summary { display: flex; align-items: center; gap: 8px; font-size: 14px; color: #ff6b35; margin-bottom: 8px; }
.pending-summary b { font-size: 18px; }
.bell-icon { font-size: 18px; }
.pending-cards { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
.pending-card { flex: 0 0 auto; width: 160px; background: #fff; border: 1.5px solid #ffd4c2; border-radius: 12px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; transition: all .15s; }
.pending-card.active { border-color: #ff6b35; box-shadow: 0 0 0 2px rgba(255,107,53,.2); background: #fff8f5; }
.pc-head { display: flex; justify-content: space-between; align-items: center; }
.pc-head .pc-table { font-weight: 700; font-size: 15px; color: #333; }
.pc-head .pc-time { font-size: 11px; color: #aaa; }
.pc-body { display: flex; justify-content: space-between; align-items: baseline; }
.pc-body .pc-amount { color: #ff6b35; font-weight: 700; font-size: 20px; }
.pc-body .pc-items { color: #999; font-size: 12px; }
.pc-actions { display: flex; gap: 6px; margin-top: 2px; }
.pc-btn { flex: 1; height: 34px; border-radius: 8px; font-size: 14px; font-weight: 600; border: none; cursor: pointer; transition: transform .1s; }
.pc-btn:active { transform: scale(.96); }
.pc-btn.add { background: #e6f4ff; color: #1677ff; }
.pc-btn.add:hover { background: #bae0ff; }
.pc-btn.pay { background: #ff6b35; color: #fff; }
.pc-btn.pay:hover { background: #ff551f; }
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
.pos-pagination { display: flex; justify-content: center; padding: 8px 0 4px; }
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
.member-bind { margin: 8px 0 10px; }
.member-bind label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
.member-tip { margin: 4px 0 0; font-size: 12px; color: #666; }
.member-tip.muted { color: #999; }
.pay-member { margin-bottom: 12px; }
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
.table-chip.walk-in { color: #1677ff; border-color: #91caff; background: #f0f9ff; }
.table-chip.walk-in.on { border-color: #1677ff; background: #e6f4ff; color: #1677ff; }
.empty-tables { text-align: center; color: #999; padding: 24px; font-size: 16px; }

/* 规格抽屉：分栏触控，少纵向滑动 */
.spec-drawer :deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: 12px 20px;
  font-size: 20px;
  font-weight: 700;
}
.spec-drawer :deep(.el-drawer__body) {
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: calc(100% - 56px);
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
  align-items: flex-start;
  gap: 16px;
  padding: 4px 20px 12px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}
.drawer-head-main { min-width: 0; flex: 1; }
.drawer-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #222;
}
.drawer-summary {
  margin-top: 6px;
  font-size: 15px;
  color: #666;
  line-height: 1.4;
  word-break: break-all;
}
.drawer-price-live {
  font-size: 30px;
  font-weight: 800;
  color: #ff6b35;
  flex-shrink: 0;
  line-height: 1.1;
}
.opt-split {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.opt-nav {
  width: 168px;
  flex-shrink: 0;
  overflow-y: auto;
  background: #f7f7f7;
  border-right: 1px solid #eee;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  -webkit-overflow-scrolling: touch;
}
.opt-nav-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  width: 100%;
  min-height: 64px;
  padding: 12px 14px;
  border: 2px solid transparent;
  border-radius: 12px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  color: #333;
}
.opt-nav-item.on {
  border-color: #ff6b35;
  background: #fff8f5;
  color: #ff6b35;
}
.opt-nav-item.done:not(.on) {
  border-color: #d9f7be;
  background: #f6ffed;
}
.opt-nav-item.missing:not(.on) {
  border-color: #ffccc7;
  background: #fff1f0;
}
.nav-name { font-size: 17px; font-weight: 700; line-height: 1.2; }
.nav-value {
  font-size: 12px;
  color: #666;
  font-weight: 600;
  line-height: 1.3;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.opt-nav-item.on .nav-value { color: #ff6b35; }
.nav-value.missing,
.opt-nav-item.on .nav-value.missing,
.opt-nav-item.missing .nav-value {
  color: #f5222d;
  font-weight: 700;
}
.opt-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 14px 18px 18px;
  -webkit-overflow-scrolling: touch;
}
.opt-pane-title {
  margin: 0 0 14px;
  font-size: 18px;
  font-weight: 700;
  color: #333;
  display: flex;
  align-items: center;
  gap: 8px;
}
.opt-pane-title .req {
  font-size: 13px;
  color: #ff6b35;
  font-weight: 600;
}
.opt-hint { font-size: 13px; color: #999; font-weight: 500; }
.opt-tiles {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.opt-tiles.few {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.opt-tiles.many {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
@media (min-width: 1024px) {
  .opt-nav { width: 200px; }
  .opt-tiles,
  .opt-tiles.few {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
  }
  .opt-tiles.many {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
.opt-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 88px;
  padding: 16px 12px;
  border: 2px solid #e0e0e0;
  border-radius: 14px;
  background: #fff;
  cursor: pointer;
  transition: border-color .12s, background .12s, transform .08s;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}
.opt-tile:active { transform: scale(0.98); }
.opt-tile.on {
  border-color: #ff6b35;
  background: #fff8f5;
  color: #ff6b35;
  box-shadow: inset 0 0 0 1px #ff6b35;
}
.tile-name {
  font-size: 20px;
  font-weight: 700;
  text-align: center;
  line-height: 1.25;
}
.tile-extra {
  margin-top: 8px;
  font-size: 15px;
  font-weight: 600;
  opacity: .9;
}
.tile-extra.muted { color: #aaa; font-weight: 500; }
.opt-empty {
  padding: 48px 16px;
  text-align: center;
  color: #999;
  font-size: 16px;
}
.drawer-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid #eee;
  background: #fafafa;
  flex-shrink: 0;
}
.nav-step-btn {
  height: 64px !important;
  min-width: 96px;
  font-size: 16px !important;
  font-weight: 600;
  border-radius: 12px !important;
  flex-shrink: 0;
}
.add-cart-btn {
  flex: 1;
  height: 64px !important;
  font-size: 20px !important;
  font-weight: 700;
  border-radius: 12px !important;
}
@media (max-width: 640px) {
  .opt-nav { width: 120px; }
  .opt-nav-item { min-height: 56px; padding: 10px; }
  .nav-name { font-size: 15px; }
  .opt-tiles,
  .opt-tiles.few,
  .opt-tiles.many {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .opt-tile { min-height: 76px; }
  .tile-name { font-size: 17px; }
  .nav-step-btn { display: none; }
}


/* 收款弹窗：组合分笔 */
.pay-dialog :deep(.el-dialog__body) { padding-top: 8px; }
.pay-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid #f0f0f0;
}
.pay-summary-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 16px;
  color: #666;
}
.pay-summary-row.remain { margin-top: 2px; }
.pay-total { font-size: 22px; font-weight: 800; color: #333; }
.pay-paid { font-size: 22px; font-weight: 700; color: #52c41a; }
.pay-remain { font-size: 30px; font-weight: 800; color: #ff6b35; }
.tender-list {
  margin-bottom: 14px;
  padding: 10px 12px;
  background: #f7f7f7;
  border-radius: 10px;
}
.tender-item {
  display: flex;
  justify-content: space-between;
  font-size: 15px;
  color: #444;
  padding: 4px 0;
}
.tender-amt-wrap { margin-bottom: 16px; }
.tender-amt-wrap label {
  display: block;
  font-size: 16px;
  color: #666;
  margin-bottom: 8px;
}
.tender-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.tender-tip {
  margin: 8px 0 0;
  font-size: 13px;
  color: #999;
  line-height: 1.4;
}
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
.round-down-row { margin-top: 12px; }
.add-items-hint { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 8px 12px; background: #f0f9ff; border-radius: 8px; font-size: 14px; color: #1677ff; }
.add-items-actions { display: flex; gap: 8px; }
.add-items-actions .el-button { flex: 1; height: 60px; font-size: 18px; font-weight: 700; border-radius: 12px; }
.checkout-actions { display: flex; gap: 8px; }
.checkout-actions .el-button { height: 60px; font-size: 18px; font-weight: 700; border-radius: 12px; }
.checkout-actions .pay-btn { flex: 2; }
.checkout-actions .park-btn { flex: 1; color: #1677ff; border-color: #91caff; background: #f0f9ff; }

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
.refund-hint b { color: #ff6b35; }
.refund-tenders {
  margin: 0 0 14px;
  padding: 10px 12px;
  background: #f7f7f7;
  border-radius: 10px;
}
.refund-tender-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 14px;
  color: #444;
  padding: 4px 0;
}
.refund-alloc-tip {
  margin: 8px 0 0;
  font-size: 12px;
  color: #999;
  line-height: 1.4;
}

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
.lock-logo { width: 80px; height: 80px; object-fit: contain; border-radius: 12px; margin-bottom: 12px; background: #fff; }
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
