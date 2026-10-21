<template>
  <div class="pos" :class="{ fullscreen, standalone }">
    <header class="toolbar">
      <div class="left">
        <img src="/logo.png?v=202609080938" alt="logo" class="head-logo" />
        <div class="head-text">
          <div class="title">收银点餐</div>
          <div class="sub">{{ storeLabel }}</div>
        </div>
        <el-select v-if="isSuperAdmin" v-model="selectedStoreId" placeholder="选择门店" size="large" class="store-select"
          @change="onStoreChange">
          <el-option v-for="s in storeOptions" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </div>
      <div class="right">
        <el-radio-group v-model="diningType" size="large" class="dine-toggle" @change="onDiningTypeChange">
          <el-radio-button value="DINE_IN">堂食</el-radio-button>
          <el-radio-button value="TAKEAWAY">外带</el-radio-button>
        </el-radio-group>
        <el-button v-if="diningType === 'DINE_IN'" size="large" type="warning" plain class="table-btn"
          @click="tableDialogVisible = true">
          {{ tableLabel }}
        </el-button>
        <el-button v-if="checkoutOrderId && addingItems" size="large" type="warning" plain
          @click="transferTableDialogVisible = true">
          转台
        </el-button>
        <el-button size="large" @click="toggleFullscreen">
          {{ fullscreen ? '退出全屏' : '全屏' }}
        </el-button>
        <el-button v-if="auth.isCashier" size="large" @click="goMyStats">我的今日</el-button>
        <el-button v-if="!currentShift" size="large" type="success" @click="shiftOpenVisible = true">开班</el-button>
        <el-button v-if="currentShift" size="large" plain @click="openShiftOrders">本班订单</el-button>
        <el-button v-if="currentShift" size="large" type="warning" plain
          @click="shiftCloseVisible = true">交班</el-button>
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
        <div v-for="o in pendingOrders" :key="o.id" class="pending-card" :class="{ active: checkoutOrderId === o.id }">
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
        <button v-for="c in categories" :key="c.id" type="button" class="cat-item" :class="{ on: activeCat === c.id }"
          @click="activeCat = c.id">
          {{ c.name }}
        </button>
      </aside>

      <section class="products">
        <div class="product-grid">
          <button v-for="p in pagedProducts" :key="p.id" type="button" class="product-card" @click="openProduct(p)">
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
        <div v-if="!visibleProducts.length" class="empty-menu">该分类暂无餐品</div>
        <div v-if="totalPages > 1" class="pos-pagination">
          <el-pagination v-model:current-page="productPage" :page-size="pageSize" :total="visibleProducts.length"
            layout="prev, pager, next" size="small" background />
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
            <span>餐品小计</span><span>¥{{ cartSubtotal }}</span>
          </div>
          <div v-if="diningType === 'TAKEAWAY'" class="sum-row sub pack-row">
            <span>打包费</span>
            <span v-if="waivePackingFee" class="waived">已免收</span>
            <span v-else>¥{{ packingFeeDisplay }}</span>
          </div>
          <div class="sum-row"><span>合计</span><span class="sum">¥{{ cartTotal }}</span></div>
          <div class="member-bind">
            <label>会员（可选）</label>
            <el-select v-model="selectedCustomerId" filterable remote clearable reserve-keyword
              placeholder="手机号 / 会员号 / 展示名 / 昵称" :remote-method="searchPosCustomers" :loading="customerSearching"
              style="width: 100%" @change="onPosCustomerChange">
              <el-option v-for="c in customerOptions" :key="c.id" :label="posCustomerLabel(c)" :value="c.id" />
            </el-select>
            <div v-if="canRememberPhone" class="remember-phone-row">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="rememberPhoneLoading"
                @click="rememberPosPhone"
              >记住该手机号</el-button>
              <span class="member-tip muted">未找到会员也可先记下来；顾客扫码官方绑号后，积分与公益身份会并入</span>
            </div>
            <p v-if="selectedCustomerLabel" class="member-tip">
              已选：{{ selectedCustomerLabel }} · 计入折扣/积分/成单里程碑
              <template v-if="selectedIsPhoneShell">（收银预留，待扫码合并）</template>
            </p>
            <p v-else class="member-tip muted">不选则按散客收款，不计会员权益</p>
            <div v-if="selectedCustomerId" class="social-block">
              <div class="social-row">
                <el-checkbox
                  v-model="useSocialBenefit"
                  :disabled="serviceCardLoading || socialVerifying"
                  @change="onSocialBenefitToggle"
                >公益折扣</el-checkbox>
                <span v-if="activeSocialIdentityLabel" class="member-tip social-id-chip">
                  已认定：{{ activeSocialIdentityLabel }}
                </span>
              </div>
              <div v-if="needPosSocialVerify" class="social-row">
                <el-select
                  v-model="verifyIdentityCode"
                  placeholder="核验身份"
                  size="small"
                  style="width: 120px"
                  :disabled="!socialIdentityTypes.length || socialVerifying"
                >
                  <el-option
                    v-for="t in socialIdentityTypes"
                    :key="t.code"
                    :label="t.name"
                    :value="t.code"
                  />
                </el-select>
                <el-button size="small" type="primary" plain :loading="socialVerifying" :disabled="!verifyIdentityCode" @click="posSocialVerify">
                  当场核验
                </el-button>
              </div>
              <p v-if="!socialIdentityTypes.length" class="member-tip warn-tip">
                未加载到公益身份类型（库缺 social_identity_types）。请导入 sql/data_sys.sql 后再核验。
              </p>
              <p v-else-if="needPosSocialVerify" class="member-tip warn-tip">
                该顾客暂无已生效公益身份，勾选公益折扣前请先「当场核验」或等小程序审核通过。
              </p>
              <p v-else class="member-tip muted">
                已读取顾客公益身份；勾选后按「已认定身份 + 规则时段」重算，无需重复核验。
              </p>
            </div>
            <div v-if="selectedCustomerId && !useSocialBenefit" class="coupon-pick">
              <label>优惠券</label>
              <el-select
                v-model="selectedCouponId"
                clearable
                placeholder="不使用优惠券"
                style="width: 100%"
                :disabled="!availableCoupons.length"
                @change="onCouponSelectionChange"
              >
                <el-option
                  v-for="c in availableCoupons"
                  :key="c.id"
                  :label="couponOptionLabel(c)"
                  :value="c.id"
                />
              </el-select>
              <label class="coupon-pick-gap">代金券</label>
              <el-select
                v-model="selectedVoucherId"
                clearable
                placeholder="不使用代金券"
                style="width: 100%"
                :disabled="!availableVouchers.length"
                @change="onCouponSelectionChange"
              >
                <el-option
                  v-for="c in availableVouchers"
                  :key="c.id"
                  :label="couponOptionLabel(c)"
                  :value="c.id"
                />
              </el-select>
              <p v-if="couponAutoHint" class="member-tip">{{ couponAutoHint }}</p>
              <p v-else-if="(serviceCard?.coupon_count || 0) > 0 && !selectedCouponId && !selectedVoucherId" class="member-tip warn-tip">
                该会员有可用券但当前购物车未达门槛，可改选或继续点餐
              </p>
            </div>
            <div v-if="selectedCustomerId && serviceCard" class="service-card" v-loading="serviceCardLoading">
              <div class="sc-title">服务卡 · {{ serviceCard.greeting }}</div>
              <div class="sc-line">
                <span>{{ serviceCard.level_name || '会员' }}</span>
                <span>{{ serviceCard.lifecycle }}</span>
                <span v-if="serviceCard.phone_masked">{{ serviceCard.phone_masked }}</span>
              </div>
              <div class="sc-line muted">储值 ¥{{ serviceCard.balance_total }} · 积分 {{ serviceCard.points }} · 可用券 {{ serviceCard.coupon_count }}</div>
              <div v-if="serviceCard.frequent_products?.length" class="sc-block">
                <b>常点</b>
                {{ serviceCard.frequent_products.map((p: any) => p.name).join('、') }}
              </div>
              <div v-if="serviceCard.taste_likes?.length" class="sc-block">
                <b>口味</b>
                {{ serviceCard.taste_likes.map((p: any) => p.label).join('、') }}
              </div>
              <div v-if="serviceCard.taste_aversions?.length" class="sc-block warn">
                <b>忌口</b>
                {{ serviceCard.taste_aversions.map((p: any) => p.label).join('、') }}
              </div>
              <div v-if="serviceCard.available_coupons?.length" class="sc-block">
                <b>券</b>
                {{ serviceCard.available_coupons.slice(0, 3).map((c: any) => c.summary || c.name).join('、') }}
              </div>
              <div v-if="serviceCard.social_identities?.length" class="sc-block">
                <b>公益</b>
                {{ serviceCard.social_identities.map((s: any) => `${s.identity_name}(${socialStatusZh(s.status)})`).join('、') }}
                <span v-if="serviceCard.social_today_used"> · 今日已享</span>
                <span v-else> · 今日未用</span>
              </div>
              <p v-else-if="selectedCustomerId && !serviceCardLoading" class="member-tip warn-tip">
                暂无公益身份记录：勾选公益折扣时将提示「当场核验」
              </p>
            </div>
          </div>
          <el-input v-model="remark" placeholder="备注（可选）" size="large" class="remark" />
          <div v-if="addingItems && cart.length" class="add-items-actions">
            <el-button size="large" plain class="add-items-btn" :loading="paying" @click="addItemsToOrder">
              加菜入单 ¥{{ cartTotal }}
            </el-button>
            <el-button size="large" type="primary" @click="goCheckoutFromAddItems">
              去收款 ¥{{ payableAmount }}
            </el-button>
          </div>
          <div v-else-if="cart.length" class="checkout-actions">
            <el-button size="large" plain class="park-btn" :loading="parking" @click="parkOrder">
              挂单
            </el-button>
            <el-button size="large" type="primary" class="pay-btn" :loading="paying" @click="openCheckout">
              去收款 ¥{{ cartTotal }}
            </el-button>
          </div>
          <el-button v-else type="primary" size="large" class="pay-btn" :loading="paying" :disabled="!cart.length"
            @click="openCheckout">
            去收款 ¥{{ cartTotal }}
          </el-button>
        </div>
      </aside>
    </div>

    <el-dialog v-model="tableDialogVisible" title="选择桌号" width="560px" class="table-dialog">
      <div class="table-grid">
        <button type="button" class="table-chip walk-in" :class="{ on: !selectedTableId }" @click="pickWalkIn">
          散客
        </button>
        <button v-for="t in tables" :key="t.id" type="button" class="table-chip"
          :class="{ on: selectedTableId === t.id }" @click="pickTable(t)">
          {{ t.table_name || t.table_no }}
        </button>
      </div>
      <div v-if="!tables.length" class="empty-tables">暂无可用桌台</div>
    </el-dialog>

    <!-- 转台 -->
    <el-dialog v-model="transferTableDialogVisible" title="转台" width="560px" class="table-dialog">
      <div class="table-grid">
        <button v-for="t in tables" :key="t.id" type="button" class="table-chip"
          :class="{ on: selectedTableId === t.id }" :disabled="t.id === selectedTableId" @click="doTransferTable(t.id)">
          {{ t.table_name || t.table_no }}
        </button>
      </div>
      <div v-if="!tables.length" class="empty-tables">暂无可用桌台</div>
    </el-dialog>

    <!-- 规格选择：平板分栏触控，少滑动 -->
    <el-drawer v-model="productVisible" title="选择规格" direction="btt" :size="drawerSize" class="spec-drawer"
      :show-close="true" destroy-on-close>
      <div v-if="currentProduct" class="drawer-body">
        <div class="drawer-head">
          <div class="drawer-head-main">
            <h3 class="drawer-title">{{ currentProduct.name }}</h3>
            <div v-if="drawerSelectedSummary" class="drawer-summary">{{ drawerSelectedSummary }}</div>
          </div>
          <div class="drawer-price-live">¥{{ drawerUnitPrice }}</div>
        </div>

        <div class="opt-split">
          <div v-if="(currentProduct.option_groups || []).length > 1" class="opt-nav">
            <button v-for="g in currentProduct.option_groups" :key="g.id" type="button" class="opt-nav-item" :class="{
              on: activeOptGroupId === g.id,
              done: isGroupFilled(g),
              missing: !!groupNavById[g.id]?.missing,
            }" @click="activeOptGroupId = g.id">
              <span class="nav-name">{{ g.name }}</span>
              <span v-if="groupNavById[g.id]?.text" class="nav-value"
                :class="{ missing: !!groupNavById[g.id]?.missing }">{{ groupNavById[g.id].text }}</span>
            </button>
          </div>

          <div class="opt-pane">
            <template v-if="activeOptGroup">
              <h4 class="opt-pane-title">
                {{ activeOptGroup.name }}
                <span v-if="activeOptGroup.is_required" class="req">必选</span>
                <span v-else class="opt-hint">可多选或不选</span>
              </h4>
              <div class="opt-tiles" :class="{
                'few': activePaneOptions.length <= 4,
                'many': activePaneOptions.length > 6,
              }">
                <button v-for="(o, oi) in activePaneOptions" :key="o.id" type="button" class="opt-tile" :class="{
                  on:
                    activeOptGroup.selection_type === 'SINGLE'
                      ? selectedOptions[activeOptGroup.id] === o.id
                      : isMultiSelected(activeOptGroup.id, o.id),
                }" @click="onPickOption(activeOptGroup, o)">
                  <span v-if="oi < 9" class="tile-num">{{ oi + 1 }}</span>
                  <span class="tile-name">{{ o.name }}</span>
                  <span v-if="+o.extra_price" class="tile-extra">+¥{{ o.extra_price }}</span>
                  <span v-else class="tile-extra muted">不加价</span>
                </button>
              </div>
            </template>
            <div v-else class="opt-empty">该餐品无需选择规格</div>
          </div>
        </div>

        <div class="drawer-foot">
          <el-button v-if="activeOptGroupIndex > 0" size="large" class="nav-step-btn" @click="goPrevOptGroup">
            上一组
          </el-button>
          <el-button v-if="activeOptGroupIndex >= 0 && activeOptGroupIndex < optGroupCount - 1" size="large"
            class="nav-step-btn" @click="goNextOptGroup">
            下一组
          </el-button>
          <el-button type="primary" class="add-cart-btn" @click="addToCart">
            加入购物车 ¥{{ drawerUnitPrice }}
          </el-button>
          <span class="enter-hint">小键盘 1-9 选规格 · 回车确认默认 · 末组选完即加购</span>
        </div>
      </div>
    </el-drawer>

    <!-- 收款：支持组合分笔（微信/支付宝/现金） -->
    <el-dialog v-model="payDialogVisible" title="收款" width="560px" class="pay-dialog" :close-on-click-modal="false"
      @closed="resetCheckout">
      <div class="pay-summary">
        <div v-if="checkoutSubtotal" class="pay-summary-row muted">
          <span>餐品小计</span>
          <span>¥{{ checkoutSubtotal }}</span>
        </div>
        <div v-if="checkoutSocialApplied && Number(checkoutMemberDiscount || 0) > 0" class="pay-summary-row muted">
          <span>公益折扣{{ checkoutSocialLabel ? `（${checkoutSocialLabel}）` : '' }}</span>
          <span class="social-off">-¥{{ checkoutMemberDiscount || '0.00' }}</span>
        </div>
        <div v-else-if="Number(checkoutMemberDiscount || 0) > 0" class="pay-summary-row muted">
          <span>会员折</span>
          <span>-¥{{ checkoutMemberDiscount || '0.00' }}</span>
        </div>
        <div v-if="Number(checkoutCouponDiscount || 0) > 0" class="pay-summary-row muted">
          <span>优惠券</span>
          <span>-¥{{ checkoutCouponDiscount }}</span>
        </div>
        <div v-if="Number(checkoutVoucherDiscount || 0) > 0" class="pay-summary-row muted">
          <span>代金券</span>
          <span>-¥{{ checkoutVoucherDiscount }}</span>
        </div>
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
          <span>餐品 ¥{{ cartSubtotal }}</span>
          <span v-if="waivePackingFee" class="waived">打包费已免收</span>
          <span v-else>打包费 ¥{{ packingFeeDisplay }}</span>
        </div>
        <el-button v-if="!waivePackingFee" type="warning" plain size="large" @click="toggleWaivePacking(true)">
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
          <el-select v-model="selectedCustomerId" filterable remote clearable reserve-keyword
            placeholder="手机号 / 会员号 / 展示名 / 昵称" :remote-method="searchPosCustomers" :loading="customerSearching"
            style="width: 100%" @change="onCheckoutMemberChange">
            <el-option v-for="c in customerOptions" :key="c.id" :label="posCustomerLabel(c)" :value="c.id" />
          </el-select>
          <div v-if="canRememberPhone" class="remember-phone-row">
            <el-button
              size="small"
              type="primary"
              plain
              :loading="rememberPhoneLoading"
              @click="rememberPosPhone"
            >记住该手机号</el-button>
          </div>
          <p class="member-tip">
            {{ selectedCustomerLabel || '散客（不计入成单里程碑）' }}
            <template v-if="selectedIsPhoneShell"> · 收银预留待扫码合并</template>
          </p>
          <div v-if="selectedCustomerId" class="social-row">
            <el-checkbox
              v-model="useSocialBenefit"
              :disabled="serviceCardLoading || socialVerifying"
              @change="onPaySocialToggle"
            >公益折扣</el-checkbox>
            <span v-if="activeSocialIdentityLabel" class="member-tip social-id-chip">
              已认定：{{ activeSocialIdentityLabel }}
            </span>
            <span v-else class="member-tip warn-tip">无已生效身份，请返回点餐区核验</span>
          </div>
          <div v-if="selectedCustomerId && !useSocialBenefit" class="coupon-pick">
            <label>优惠券</label>
            <el-select
              v-model="selectedCouponId"
              clearable
              placeholder="不使用优惠券"
              style="width: 100%"
              :disabled="!availableCoupons.length"
              @change="onCouponSelectionChange"
            >
              <el-option
                v-for="c in availableCoupons"
                :key="c.id"
                :label="couponOptionLabel(c)"
                :value="c.id"
              />
            </el-select>
            <label class="coupon-pick-gap">代金券</label>
            <el-select
              v-model="selectedVoucherId"
              clearable
              placeholder="不使用代金券"
              style="width: 100%"
              :disabled="!availableVouchers.length"
              @change="onCouponSelectionChange"
            >
              <el-option
                v-for="c in availableVouchers"
                :key="c.id"
                :label="couponOptionLabel(c)"
                :value="c.id"
              />
            </el-select>
            <p v-if="couponAutoHint" class="member-tip">{{ couponAutoHint }}</p>
            <p v-else-if="(serviceCard?.coupon_count || 0) > 0 && !selectedCouponId && !selectedVoucherId" class="member-tip warn-tip">
              有可用券但当前未达门槛，切换券后会立即重算应收
            </p>
          </div>
        </div>
        <div class="tender-amt-wrap">
          <label>本笔收款</label>
          <div class="tender-quick">
            <button type="button" class="cash-quick-btn exact" @click="setTenderExact">收清待收</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(10)">¥10</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(20)">¥20</button>
            <button type="button" class="cash-quick-btn" @click="setTenderQuick(50)">¥50</button>
          </div>
          <el-input v-model="tenderAmount" size="large" inputmode="decimal" class="cash-input" @input="onTenderInput">
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
          <el-input v-model="cashReceived" size="large" inputmode="decimal" placeholder="输入顾客给的现金" class="cash-input"
            @input="onCashInput">
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
          <el-button type="primary" size="large" class="cash-confirm" :loading="paying" :disabled="cashConfirmDisabled"
            @click="confirmCashPay">
            确认本笔现金
          </el-button>
        </div>
      </div>

      <!-- 扫顾客付款码 -->
      <div v-else-if="payStep === 'barcode'" class="barcode-panel">
        <div class="scan-provider">本笔扫码 ¥{{ activeTenderAmount }}</div>
        <p v-if="barcodeProviderHint" class="scan-detected"
          :class="{ warn: !detectBarcodeProvider(barcodeInput.trim()) }">
          {{ barcodeProviderHint }}
        </p>
        <el-input ref="barcodeInputRef" v-model="barcodeInput" size="large" placeholder="扫码枪对准此处，或手动粘贴付款码"
          class="barcode-input" @keyup.enter="submitBarcodePay" />
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
        <p>现金 ¥{{ currentShift.total_cash }} · 微信 ¥{{ currentShift.total_wechat }} · 支付宝 ¥{{ currentShift.total_alipay
        }}
        </p>
      </div>
      <el-input v-model="closingCash" size="large" inputmode="decimal" placeholder="交班实盘现金（含备用金）"
        style="margin-top:12px">
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
            <span>{{ formatShiftPayMethod(o) }}</span>
            <span>{{ diningLabel(o.dining_type, o.table_no, o.table_name) }}</span>
            <span>{{ formatShiftTime(o.paid_at) }}</span>
          </div>
          <div class="so-actions">
            <span v-if="o.refund_request" class="so-refund-tag" :class="refundTagClass(o.refund_request.status)">
              {{ refundStatusLabel(o.refund_request.status) }}
              <template v-if="o.refund_request.amount"> · ¥{{ o.refund_request.amount }}</template>
            </span>
            <el-button v-if="canApplyRefund(o)" type="danger" plain size="small" @click="openRefundApply(o)">
              申请退款
            </el-button>
            <el-button v-else-if="o.refund_request?.status === 'PENDING'" size="small"
              @click="cancelRefundRequest(o.refund_request.id)">
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
      <el-input v-model="refundAmount" size="large" inputmode="decimal" placeholder="退款金额（默认全额可退）"
        style="margin-bottom:12px">
        <template #prefix>¥</template>
      </el-input>
      <el-input v-model="refundReason" type="textarea" :rows="3" maxlength="200" show-word-limit
        placeholder="请填写退款原因（必填）" />
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
        <el-input v-model="unlockPassword" type="password" size="large" placeholder="输入密码解锁" show-password
          class="lock-input" @keyup.enter="tryUnlock" />
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
import { useStoreOptions } from '@/composables/useStoreOptions'
import { useAuthStore } from '@/stores/auth'
import { isPosLocked, setPosLocked } from '@/utils/staffAuth'
import { mediaUrl } from '@/utils/media'
import { SOCIAL_IDENTITY } from '@/utils/labels'
import {
  buildOptionLabels,
  calcUnitPrice,
  checkCategoryRequire,
  collectOptionIds,
  mergeCartItem,
  validateCategoryRequires,
} from '@/utils/cart'
import {
  calcDiscountableSubtotal,
  pickBestPromoPair,
  type CartPromoLine,
} from '@/utils/bestPromo'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const standalone = computed(() => route.path === '/pos-terminal' || !!route.meta?.standalone)
const isSuperAdmin = computed(() => auth.isSuperAdmin)
const { storeOptions, loadStores: fetchStores } = useStoreOptions({ source: 'kitchen', rethrow: true, clearOnError: false })
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
const customerSearchKeyword = ref('')
const rememberPhoneLoading = ref(false)
const useSocialBenefit = ref(false)
const socialVerifying = ref(false)
const socialIdentityTypes = ref<any[]>([])
const verifyIdentityCode = ref<string>('SANITATION')
const customerSearching = ref(false)
const serviceCard = ref<any | null>(null)
const serviceCardLoading = ref(false)
/** 满减/折扣券（customer_coupons.id） */
const selectedCouponId = ref<number | undefined>()
/** 代金券 */
const selectedVoucherId = ref<number | undefined>()
/** 收银员手动改过券/代金券后，在换会员或改购物车前不再自动覆盖 */
const couponManualPick = ref(false)
const couponAutoHint = ref('')
let cartPromoAutoTimer: ReturnType<typeof setTimeout> | null = null
const checkoutSubtotal = ref('')
const checkoutMemberDiscount = ref('')
const checkoutCouponDiscount = ref('')
const checkoutVoucherDiscount = ref('')
const selectedCustomerLabel = computed(() => {
  if (!selectedCustomerId.value) return ''
  const c = customerOptions.value.find((x) => x.id === selectedCustomerId.value)
  return c ? posCustomerLabel(c) : `会员#${selectedCustomerId.value}`
})
const availableCoupons = computed(() =>
  (serviceCard.value?.available_coupons || []).filter((c: any) => (c.kind || '').toUpperCase() !== 'VOUCHER'),
)
const availableVouchers = computed(() =>
  (serviceCard.value?.available_coupons || []).filter((c: any) => (c.kind || '').toUpperCase() === 'VOUCHER'),
)
const hasPromoDiscount = computed(() => {
  const m = Number(checkoutMemberDiscount.value || 0)
  const c = Number(checkoutCouponDiscount.value || 0)
  const v = Number(checkoutVoucherDiscount.value || 0)
  return m > 0 || c > 0 || v > 0
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
const checkoutSocialApplied = ref(false)
const checkoutSocialCode = ref('')
const paidAmount = ref('0.00')
const remainingAmount = ref('0.00')

const checkoutSocialLabel = computed(() => {
  const code = String(checkoutSocialCode.value || '').toUpperCase()
  const fromApi = socialIdentityTypes.value.find((t: any) => String(t.code || '').toUpperCase() === code)
  return (fromApi && fromApi.name) || SOCIAL_IDENTITY[code] || '公益优惠'
})

function socialStatusZh(status: string) {
  const key = String(status || '').toUpperCase()
  if (key === 'ACTIVE') return '已生效'
  if (key === 'PENDING') return '待审核'
  if (key === 'REVOKED') return '已取消'
  if (key === 'REJECTED') return '已拒绝'
  if (key === 'EXPIRED') return '已过期'
  return '—'
}

function activeSocialIdentities() {
  return (serviceCard.value?.social_identities || []).filter(
    (s: any) => String(s.status || '').toUpperCase() === 'ACTIVE',
  )
}

function hasActiveSocialIdentity() {
  return activeSocialIdentities().length > 0
}

const activeSocialIdentityLabel = computed(() => {
  const rows = activeSocialIdentities()
  if (!rows.length) return ''
  return rows
    .map((s: any) => s.identity_name || SOCIAL_IDENTITY[String(s.identity_code || '').toUpperCase()] || s.identity_code)
    .filter(Boolean)
    .join('、')
})

/** 无已生效身份时才展示「当场核验」 */
const needPosSocialVerify = computed(() => {
  if (!selectedCustomerId.value) return false
  if (serviceCardLoading.value) return false
  return !hasActiveSocialIdentity()
})

/** 勾选公益前强制刷新服务卡，读取顾客当前公益身份 */
async function ensureCustomerSocialIdentities(): Promise<boolean> {
  if (!selectedCustomerId.value) return false
  await loadServiceCard(selectedCustomerId.value, { keepSelection: true, autoPromo: false })
  return hasActiveSocialIdentity()
}
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

/** 当前规格组可见选项（与触控瓦片、小键盘序号一致） */
const activePaneOptions = computed(() => activeOptionsOf(activeOptGroup.value))

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

watch(
  [cart, diningType, waivePackingFee],
  () => {
    if (!selectedCustomerId.value || useSocialBenefit.value) return
    scheduleAutoSelectBestPromo()
  },
  { deep: true },
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

/** 当前组未选时补默认（单选补一项，多选补默认项） */
function ensureActiveGroupSelection() {
  const g = activeOptGroup.value
  if (!g) return
  const defs = defaultOptionsOf(g)
  if (g.selection_type === 'SINGLE') {
    if (!findOptionById(g, selectedOptions[g.id]) && defs[0]) {
      selectedOptions[g.id] = defs[0].id
    }
  } else if (!(multiOptions[g.id] || []).length && defs.length) {
    multiOptions[g.id] = defs.map((o: any) => o.id)
  }
}

/** 单选/确认后：有下一组则跳转，末组直接加入购物车 */
function advanceAfterSinglePick() {
  const idx = activeOptGroupIndex.value
  const count = optGroupCount.value
  if (count === 0 || idx < 0 || idx >= count - 1) {
    addToCart()
    return
  }
  goNextOptGroup()
}

/** 回车：确认当前（默认）规格；有下一组则跳转，否则加入购物车 */
function confirmCurrentOptGroupAndAdvance() {
  if (!productVisible.value || !currentProduct.value) return
  ensureActiveGroupSelection()
  advanceAfterSinglePick()
}

/** 小键盘 1-9：按当前组选项顺序选中（1=第一项） */
function pickOptionByNumpadIndex(oneBased: number) {
  const g = activeOptGroup.value
  if (!g || oneBased < 1 || oneBased > 9) return
  const opts = activePaneOptions.value
  const o = opts[oneBased - 1]
  if (!o) return
  onPickOption(g, o)
}

function numpadDigitFromEvent(e: KeyboardEvent): number | null {
  // 仅数字小键盘，避免与主键盘冲突
  if (e.code >= 'Numpad1' && e.code <= 'Numpad9') {
    return Number(e.code.slice('Numpad'.length))
  }
  if (e.location === 3 && e.key >= '1' && e.key <= '9') {
    return Number(e.key)
  }
  return null
}

function onSpecDrawerKeydown(e: KeyboardEvent) {
  if (!productVisible.value) return
  if (e.isComposing) return
  if (payDialogVisible.value || locked.value) return
  const el = e.target as HTMLElement | null
  const tag = (el?.tagName || '').toUpperCase()
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return

  const digit = numpadDigitFromEvent(e)
  if (digit != null) {
    e.preventDefault()
    e.stopPropagation()
    pickOptionByNumpadIndex(digit)
    return
  }

  if (e.key !== 'Enter') return
  e.preventDefault()
  e.stopPropagation()
  confirmCurrentOptGroupAndAdvance()
}

/** 单选后自动跳下一组；末组选完直接加购 */
function onPickOption(g: any, o: any) {
  if (g.selection_type === 'SINGLE') {
    selectedOptions[g.id] = o.id
    advanceAfterSinglePick()
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
  await fetchStores()
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
            params: { store_id: sid, page, page_size: 200, sale_visible: true },
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
    no_member_discount: !!p.no_member_discount,
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
  const sid = effectiveStoreId()
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return false
  }
  // 挂单恢复 / 分笔收款：订单已创建，购物车可能已空，仍应允许结算
  if (checkoutOrderId.value) return true
  if (!cart.value.length) {
    ElMessage.warning('请先点餐')
    return false
  }
  const catIssues = validateCategoryRequires(cart.value, products.value, categories.value)
  if (catIssues.length) {
    ElMessage.warning(catIssues[0].message)
    return false
  }
  return true
}

async function loadSocialIdentityTypes() {
  try {
    const res = await api.get('/social-benefits/identity-types')
    socialIdentityTypes.value = res.data?.items || []
    if (
      socialIdentityTypes.value.length
      && !socialIdentityTypes.value.some((t: any) => t.code === verifyIdentityCode.value)
    ) {
      verifyIdentityCode.value = socialIdentityTypes.value[0].code
    }
  } catch {
    socialIdentityTypes.value = []
  }
}

async function posSocialVerify() {
  if (!selectedCustomerId.value) {
    ElMessage.warning('请先选择会员')
    return
  }
  const sid = effectiveStoreId()
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return
  }
  const code = (verifyIdentityCode.value || '').trim().toUpperCase()
  if (!code) {
    ElMessage.warning('请选择要核验的公益身份')
    return
  }
  if (!socialIdentityTypes.value.length) {
    ElMessage.error('库中无公益身份类型，请先导入 sql/data_sys.sql（social_identity_types）')
    return
  }
  socialVerifying.value = true
  try {
    const typeName = socialIdentityTypes.value.find((t: any) => t.code === code)?.name
      || SOCIAL_IDENTITY[String(code || '').toUpperCase()]
      || '公益身份'
    await api.post('/social-benefits/identities/pos-verify', {
      customer_id: selectedCustomerId.value,
      identity_code: code,
      store_id: sid,
      activate: true,
      credential_note: 'POS当场核验',
    })
    clearCouponSelection()
    useSocialBenefit.value = true
    await loadServiceCard(selectedCustomerId.value, { keepSelection: true })
    ElMessage.success(`已认定「${typeName}」并勾选公益折扣，去收款后按规则重算应收`)
    if (checkoutOrderId.value) await bindCustomerOnCheckoutOrder()
  } catch (e: any) {
    ElMessage.error(e?.message || '公益核验失败')
  } finally {
    socialVerifying.value = false
  }
}

function couponOptionLabel(c: any) {
  const summary = c.summary || c.discount_label || c.name || `券#${c.id}`
  return summary
}

function applyOrderPromoSnapshot(data: any) {
  if (!data) return
  if (data.total_amount != null) checkoutAmount.value = String(data.total_amount)
  if (data.subtotal != null) checkoutSubtotal.value = String(data.subtotal)
  if (data.discount_amount != null) checkoutMemberDiscount.value = String(data.discount_amount)
  if (data.coupon_discount != null) checkoutCouponDiscount.value = String(data.coupon_discount)
  if (data.voucher_discount != null) checkoutVoucherDiscount.value = String(data.voucher_discount)
  if (Object.prototype.hasOwnProperty.call(data, 'social_applied')) {
    checkoutSocialApplied.value = !!data.social_applied
  }
  if (Object.prototype.hasOwnProperty.call(data, 'social_identity_code')) {
    checkoutSocialCode.value = data.social_identity_code ? String(data.social_identity_code) : ''
  }
  if (data.social_applied && data.social_discount != null) {
    // 公益补贴写入 discount_amount；若响应带 social_discount 则对齐展示
    checkoutMemberDiscount.value = String(data.social_discount)
  }
  if (Object.prototype.hasOwnProperty.call(data, 'customer_coupon_id')) {
    selectedCouponId.value = data.customer_coupon_id != null ? Number(data.customer_coupon_id) : undefined
  }
  if (Object.prototype.hasOwnProperty.call(data, 'customer_voucher_id')) {
    selectedVoucherId.value = data.customer_voucher_id != null ? Number(data.customer_voucher_id) : undefined
  }
}

function clearCouponSelection() {
  selectedCouponId.value = undefined
  selectedVoucherId.value = undefined
  couponAutoHint.value = ''
}

/** 购物车行：缺省时从 products 补 no_member_discount */
function cartLinesForPromo(): CartPromoLine[] {
  const byId = new Map(products.value.map((p: any) => [Number(p.id), p]))
  return cart.value.map((item: any) => {
    const p = byId.get(Number(item.product_id))
    const flagged =
      item.no_member_discount != null
        ? !!item.no_member_discount
        : !!(p && p.no_member_discount)
    return {
      subtotal: item.subtotal,
      quantity: item.quantity,
      no_member_discount: flagged,
    }
  })
}

async function autoSelectBestPromo(opts?: { force?: boolean; bindIfOrder?: boolean }) {
  if (!selectedCustomerId.value || useSocialBenefit.value) {
    if (useSocialBenefit.value) clearCouponSelection()
    couponAutoHint.value = ''
    return
  }
  if (couponManualPick.value && !opts?.force) return
  if (!serviceCard.value) return

  const discountable = calcDiscountableSubtotal(cartLinesForPromo())
  if (discountable <= 0 && !cart.value.length) {
    clearCouponSelection()
    return
  }
  const memberRate = serviceCard.value.discount_rate ?? 1
  const { coupon, voucher } = pickBestPromoPair({
    coupons: availableCoupons.value,
    vouchers: availableVouchers.value,
    discountable,
    memberRate,
    packingFee: packingFeeNum.value,
    needPack: diningType.value === 'TAKEAWAY' && !waivePackingFee.value,
  })
  const nextCoupon = coupon?.id != null ? Number(coupon.id) : undefined
  const nextVoucher = voucher?.id != null ? Number(voucher.id) : undefined
  const changed =
    selectedCouponId.value !== nextCoupon || selectedVoucherId.value !== nextVoucher
  selectedCouponId.value = nextCoupon
  selectedVoucherId.value = nextVoucher
  couponManualPick.value = false
  if (nextCoupon || nextVoucher) {
    couponAutoHint.value = '已自动选最优优惠，可改选'
  } else if ((serviceCard.value.coupon_count || 0) > 0) {
    couponAutoHint.value = ''
  } else {
    couponAutoHint.value = ''
  }
  if ((opts?.bindIfOrder !== false) && checkoutOrderId.value && changed) {
    await bindCustomerOnCheckoutOrder()
  }
}

function scheduleAutoSelectBestPromo() {
  if (cartPromoAutoTimer) clearTimeout(cartPromoAutoTimer)
  cartPromoAutoTimer = setTimeout(() => {
    cartPromoAutoTimer = null
    if (!selectedCustomerId.value || useSocialBenefit.value) return
    couponManualPick.value = false
    void autoSelectBestPromo({ force: true })
  }, 280)
}

/** 本单已预占的券不会出现在 AVAILABLE 列表，补一条选项避免下拉空白 */
function ensureSelectedCouponOptions(extra?: { couponId?: number | null; voucherId?: number | null; couponLabel?: string; voucherLabel?: string }) {
  if (!serviceCard.value) {
    serviceCard.value = { available_coupons: [], coupon_count: 0 }
  }
  const list = [...(serviceCard.value.available_coupons || [])]
  const cid = extra?.couponId ?? selectedCouponId.value
  const vid = extra?.voucherId ?? selectedVoucherId.value
  if (cid && !list.some((c: any) => Number(c.id) === Number(cid))) {
    list.push({
      id: Number(cid),
      name: extra?.couponLabel || '本单已用优惠券',
      kind: 'COUPON',
      summary: extra?.couponLabel || '本单已用优惠券',
    })
  }
  if (vid && !list.some((c: any) => Number(c.id) === Number(vid))) {
    list.push({
      id: Number(vid),
      name: extra?.voucherLabel || '本单已用代金券',
      kind: 'VOUCHER',
      summary: extra?.voucherLabel || '本单已用代金券',
    })
  }
  serviceCard.value = {
    ...serviceCard.value,
    available_coupons: list,
    coupon_count: Math.max(Number(serviceCard.value.coupon_count || 0), list.length),
  }
}

async function onSocialBenefitToggle() {
  if (useSocialBenefit.value) {
    clearCouponSelection()
    couponManualPick.value = false
    const ok = await ensureCustomerSocialIdentities()
    if (!ok) {
      ElMessage.warning('该顾客暂无已生效公益身份，请先「当场核验」后再勾选公益折扣')
      useSocialBenefit.value = false
      return
    }
    const label = activeSocialIdentityLabel.value
    ElMessage.success(label ? `已读取公益身份「${label}」，去收款后按规则重算` : '已读取公益身份，去收款后按规则重算')
  } else {
    checkoutSocialApplied.value = false
    checkoutSocialCode.value = ''
    couponManualPick.value = false
    void autoSelectBestPromo({ force: true })
  }
  if (checkoutOrderId.value) await bindCustomerOnCheckoutOrder()
}

async function onCouponSelectionChange() {
  if (useSocialBenefit.value) {
    clearCouponSelection()
    ElMessage.warning('已勾选公益折扣，不可同时用券')
    return
  }
  couponManualPick.value = true
  couponAutoHint.value = selectedCouponId.value || selectedVoucherId.value
    ? '已按手动选择用券'
    : ''
  // 已建单则立即重算；未建单则等「去收款」建单时带上所选券
  if (checkoutOrderId.value) {
    await bindCustomerOnCheckoutOrder()
  }
}

async function createPosOrder() {
  const sid = effectiveStoreId()
  const useSocial = !!(useSocialBenefit.value && selectedCustomerId.value)
  if (!useSocial && selectedCustomerId.value && !couponManualPick.value) {
    await autoSelectBestPromo({ force: true, bindIfOrder: false })
  }
  try {
    const orderRes = await api.post('/orders/pos', {
      store_id: sid,
      table_id: diningType.value === 'DINE_IN' ? selectedTableId.value : null,
      customer_id: selectedCustomerId.value || null,
      dining_type: diningType.value,
      need_pack: diningType.value === 'TAKEAWAY',
      waive_packing_fee: diningType.value === 'TAKEAWAY' && waivePackingFee.value,
      remark: remark.value.trim() || null,
      use_social_benefit: useSocial,
      customer_coupon_id: useSocial ? null : (selectedCouponId.value || null),
      customer_voucher_id: useSocial ? null : (selectedVoucherId.value || null),
      items: cart.value.map((c) => ({
        product_id: c.product_id,
        quantity: c.quantity || 1,
        option_ids: c.option_ids || [],
      })),
      idempotency_key: idempotencyKey(),
    })
    checkoutOrderId.value = orderRes.data?.id
    checkoutOrderNo.value = orderRes.data?.order_no || ''
    applyOrderPromoSnapshot(orderRes.data)
    ensureSelectedCouponOptions({
      couponId: orderRes.data?.customer_coupon_id,
      voucherId: orderRes.data?.customer_voucher_id,
    })
    if (orderRes.data?.total_amount == null) {
      checkoutAmount.value = cartTotal.value
    }
    remainingAmount.value = checkoutAmount.value
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
  // 展示名优先；无展示名才用昵称。禁止把合成名写回 display_name。
  const display = String(c?.display_name || '').trim()
  const nick = String(c?.nickname || '').trim()
  const name = display || nick || '会员'
  const nickHint = display && nick && display !== nick ? `（昵称 ${nick}）` : ''
  const phone = c.phone ? ` ${c.phone}` : ''
  const no = c.customer_no ? ` · ${c.customer_no}` : ''
  const shell = c.is_phone_shell ? ' · 预留' : ''
  return `${name}${nickHint}${phone}${no}${shell}`
}

function isMobilePhone(kw: string) {
  return /^1\d{10}$/.test((kw || '').trim())
}

const canRememberPhone = computed(() => {
  const kw = customerSearchKeyword.value.trim()
  return isMobilePhone(kw) && !customerSearching.value && customerOptions.value.length === 0 && !selectedCustomerId.value
})

const selectedIsPhoneShell = computed(() => {
  if (!selectedCustomerId.value) return false
  const c = customerOptions.value.find((x) => x.id === selectedCustomerId.value)
  return !!(c && c.is_phone_shell)
})

async function searchPosCustomers(keyword: string) {
  const kw = (keyword || '').trim()
  customerSearchKeyword.value = kw
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

async function rememberPosPhone() {
  const phone = customerSearchKeyword.value.trim()
  if (!isMobilePhone(phone)) {
    ElMessage.warning('请输入 11 位手机号后再记住')
    return
  }
  const sid = effectiveStoreId()
  if (!sid) {
    ElMessage.warning('请先选择门店')
    return
  }
  rememberPhoneLoading.value = true
  try {
    const res = (await api.post('/pos/customers/remember-phone', {
      phone,
      store_id: sid,
    })) as { message?: string; data?: any }
    const row = res.data || {}
    const option = {
      id: row.id,
      customer_no: row.customer_no,
      display_name: row.display_name,
      nickname: row.nickname,
      phone: row.phone,
      is_member: !!row.is_member,
      phone_verified: !!row.phone_verified,
      is_phone_shell: true,
    }
    customerOptions.value = [option]
    selectedCustomerId.value = option.id
    ElMessage.success(res.message || '已记住手机号')
    await onPosCustomerChange()
    if (checkoutOrderId.value) {
      await bindCustomerOnCheckoutOrder()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '记住手机号失败')
  } finally {
    rememberPhoneLoading.value = false
  }
}

async function loadServiceCard(customerId?: number, opts?: { keepSelection?: boolean; autoPromo?: boolean }) {
  if (!customerId) {
    serviceCard.value = null
    if (!opts?.keepSelection) clearCouponSelection()
    return
  }
  serviceCardLoading.value = true
  try {
    const res = await api.get(`/customers/${customerId}/service-card`)
    serviceCard.value = res.data || null
    if (!opts?.keepSelection) {
      // 换会员时清空旧券；若仅刷新服务卡则保留当前选择
      const couponIds = new Set(availableCoupons.value.map((c: any) => c.id))
      const voucherIds = new Set(availableVouchers.value.map((c: any) => c.id))
      if (selectedCouponId.value && !couponIds.has(selectedCouponId.value)) {
        selectedCouponId.value = undefined
      }
      if (selectedVoucherId.value && !voucherIds.has(selectedVoucherId.value)) {
        selectedVoucherId.value = undefined
      }
    }
    if (opts?.autoPromo !== false && !opts?.keepSelection && !useSocialBenefit.value) {
      couponManualPick.value = false
      await autoSelectBestPromo({ force: true })
    }
  } catch {
    serviceCard.value = null
  } finally {
    serviceCardLoading.value = false
  }
}

async function onPosCustomerChange() {
  clearCouponSelection()
  couponManualPick.value = false
  useSocialBenefit.value = false
  await loadServiceCard(selectedCustomerId.value, { autoPromo: true })
}

async function onCheckoutMemberChange() {
  clearCouponSelection()
  couponManualPick.value = false
  useSocialBenefit.value = false
  await loadServiceCard(selectedCustomerId.value, { autoPromo: true })
  await bindCustomerOnCheckoutOrder()
}

async function onPaySocialToggle() {
  if (useSocialBenefit.value) {
    clearCouponSelection()
    couponManualPick.value = false
    const ok = await ensureCustomerSocialIdentities()
    if (!ok) {
      ElMessage.warning('该顾客暂无已生效公益身份，请先返回点餐区「当场核验」')
      useSocialBenefit.value = false
      return
    }
  } else {
    checkoutSocialApplied.value = false
    checkoutSocialCode.value = ''
    couponManualPick.value = false
    await autoSelectBestPromo({ force: true, bindIfOrder: false })
  }
  await bindCustomerOnCheckoutOrder()
}

async function bindCustomerOnCheckoutOrder() {
  if (!checkoutOrderId.value) return
  const useSocial = !!(useSocialBenefit.value && selectedCustomerId.value)
  try {
    const res = await api.post(`/orders/${checkoutOrderId.value}/bind-customer`, {
      customer_id: selectedCustomerId.value || null,
      customer_coupon_id: useSocial ? null : (selectedCouponId.value || null),
      customer_voucher_id: useSocial ? null : (selectedVoucherId.value || null),
      use_social_benefit: useSocial,
    })
    applyOrderPromoSnapshot(res.data)
    ensureSelectedCouponOptions({
      couponId: res.data?.customer_coupon_id,
      voucherId: res.data?.customer_voucher_id,
    })
    if (res.data?.total_amount != null) {
      const paid = parseFloat(paidAmount.value || '0') || 0
      const total = parseFloat(res.data.total_amount) || 0
      remainingAmount.value = Math.max(0, total - paid).toFixed(2)
      if (!tenders.value.length) {
        tenderAmount.value = remainingAmount.value
      }
    }
    if (res.data?.customer_id) {
      const exists = customerOptions.value.some((x) => x.id === res.data.customer_id)
      if (!exists) {
        customerOptions.value = [
          {
            id: res.data.customer_id,
            display_name: res.data.customer_display_name || null,
            nickname: res.data.customer_nickname || null,
            phone: res.data.customer_phone,
          },
          ...customerOptions.value,
        ]
      }
    }
    if (useSocial && res.data?.social_applied) {
      ElMessage.success(
        `已按公益折扣重算 · 减免 ¥${res.data.social_discount || checkoutMemberDiscount.value || '0.00'} · 应收 ¥${res.data.total_amount}`,
      )
    } else if (useSocial && selectedCustomerId.value) {
      ElMessage.info('会员折更优，已按会员优惠结算（与券互斥的公益未占用日限次）')
    } else {
      ElMessage.success(selectedCustomerId.value ? '已绑定会员并重算优惠' : '已清除会员绑定')
    }
  } catch (e: any) {
    if (useSocial) {
      useSocialBenefit.value = false
      checkoutSocialApplied.value = false
      checkoutSocialCode.value = ''
    }
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
  serviceCard.value = null
  useSocialBenefit.value = false
  clearCouponSelection()
  checkoutSubtotal.value = ''
  checkoutMemberDiscount.value = ''
  checkoutCouponDiscount.value = ''
  checkoutVoucherDiscount.value = ''
  checkoutSocialApplied.value = false
  checkoutSocialCode.value = ''
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

/** 本班订单：组合支付拆开各渠道金额 */
function formatShiftPayMethod(o: any): string {
  const tenders = Array.isArray(o?.payment_tenders) ? o.payment_tenders : []
  if (tenders.length > 1) {
    return tenders
      .map((t: any) => `${tenderLabel(t.provider)}¥${t.amount}`)
      .join(' + ')
  }
  if (tenders.length === 1) return `${tenderLabel(tenders[0].provider)}¥${tenders[0].amount}`
  return payMethodLabel(o?.payment_method)
}

/** 收款完成提示：汇总已收支付方式 */
function formatPaidMethodHint(data: any): string {
  const tenders = Array.isArray(data?.tenders) ? data.tenders : []
  if (tenders.length) {
    return tenders.map((t: any) => `${tenderLabel(t.provider)}¥${t.amount}`).join(' + ')
  }
  const method = data?.payment_method || data?.provider
  return method ? payMethodLabel(method) : ''
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
async function resumePendingOrder(o: any, mode: 'add' | 'pay') {
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
  selectedCouponId.value = o.customer_coupon_id || undefined
  selectedVoucherId.value = o.customer_voucher_id || undefined
  applyOrderPromoSnapshot(o)
  if (o.customer_id) {
    customerOptions.value = [
      {
        id: o.customer_id,
        display_name: o.customer_display_name || null,
        nickname: o.customer_nickname || null,
        phone: o.customer_phone,
      },
    ]
    await loadServiceCard(o.customer_id, { keepSelection: true })
    ensureSelectedCouponOptions({
      couponId: o.customer_coupon_id,
      voucherId: o.customer_voucher_id,
    })
  } else {
    customerOptions.value = []
    serviceCard.value = null
    clearCouponSelection()
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
    PROCESSING: '退款处理中',
    REJECTED: '已拒绝',
    CANCELLED: '已撤销',
    SUCCESS: '已退款',
    FAILED: '退款失败',
  }
  return map[status] || '—'
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

async function openCheckout() {
  if (!validateBeforeCheckout()) return
  // 先建单再弹收款：把会员折/券/公益一次性算进应收，避免弹窗仍显示购物车原价
  invalidateCheckoutOrder()
  paying.value = true
  try {
    await createPosOrder()
    payStep.value = 'pick'
    paidAmount.value = '0.00'
    remainingAmount.value = checkoutAmount.value
    tenders.value = []
    tenderAmount.value = remainingAmount.value
    payDialogVisible.value = true
    const couponOff = Number(checkoutCouponDiscount.value || 0)
    const voucherOff = Number(checkoutVoucherDiscount.value || 0)
    const memberOff = Number(checkoutMemberDiscount.value || 0)
    if (useSocialBenefit.value && checkoutSocialApplied.value) {
      ElMessage.success(
        `公益折扣已生效${checkoutSocialLabel.value ? `（${checkoutSocialLabel.value}）` : ''} · 减免 ¥${checkoutMemberDiscount.value || '0.00'} · 应收 ¥${checkoutAmount.value}`,
      )
    } else if (useSocialBenefit.value && selectedCustomerId.value && !checkoutSocialApplied.value) {
      ElMessage.info('会员折更优，已按会员优惠结算（公益日限次未占用）')
    } else if (selectedCouponId.value || selectedVoucherId.value) {
      if (couponOff + voucherOff + memberOff <= 0) {
        ElMessage.warning('已选券但未产生减免，请核对满减门槛 / 门店限制 / 是否与会员折互斥')
      }
    }
  } catch (e: any) {
    if (useSocialBenefit.value) {
      // 建单因公益未命中失败时保持勾选状态，便于收银员看清错误后去核验
    }
    ElMessage.error(e?.message || '建单失败，无法进入收款')
  } finally {
    paying.value = false
  }
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

/** 加菜入单：将购物车中的餐品追加到已有订单 */
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
  // 不在此改 paying：由 confirmCashPay / startBarcodePay 等调用方统一控制 loading
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
      const payHint = formatPaidMethodHint(payRes.data)
      const base = `收款完成 · ${payRes.data?.order_no || checkoutOrderNo.value}`
      const withPay = payHint ? `${base} · ${payHint}` : base
      const msg = change != null && Number(change) > 0
        ? `${withPay} · 找零 ¥${change}`
        : withPay
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
      const payHint = formatPaidMethodHint(payRes.data)
      const base = `收款完成 · ${payRes.data?.order_no || checkoutOrderNo.value}`
      finishCheckout(payHint ? `${base} · ${payHint}` : base)
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

watch(productVisible, (open) => {
  if (open) {
    document.addEventListener('keydown', onSpecDrawerKeydown, true)
  } else {
    document.removeEventListener('keydown', onSpecDrawerKeydown, true)
  }
})

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  auth.assertSessionNotStale()
  locked.value = isPosLocked()
  await loadStores()
  await loadMenu()
  await loadShift()
  await loadPendingOrders()
  await loadSocialIdentityTypes()
  if (standalone.value) document.title = '收银点餐'
  document.addEventListener('fullscreenchange', onFsChange)
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', onFsChange)
  document.removeEventListener('keydown', onSpecDrawerKeydown, true)
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
  background:
    radial-gradient(circle at 20% -10%, rgba(36, 74, 134, 0.55) 0%, transparent 50%),
    radial-gradient(circle at 90% 110%, rgba(26, 58, 110, 0.45) 0%, transparent 55%),
    linear-gradient(180deg, #0e1e3c 0%, #0a1730 100%);
  display: flex;
  flex-direction: column;
  color: #dbeaff;
}

.pos.standalone {
  min-height: 100dvh;
}

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
  background: rgba(16, 34, 66, 0.72);
  border-bottom: 1px solid rgba(120, 200, 255, 0.28);
  backdrop-filter: blur(12px) saturate(140%);
  -webkit-backdrop-filter: blur(12px) saturate(140%);
  box-shadow: 0 4px 20px rgba(4, 14, 32, 0.3);
  position: sticky;
  top: 0;
  z-index: 10;
}

.left,
.right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.head-logo {
  width: 48px;
  height: 48px;
  object-fit: contain;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 0 16px rgba(120, 200, 255, 0.45);
}

.head-text .title {
  font-size: 22px;
  font-weight: 700;
  color: #e8f4ff;
}

.head-text .sub {
  font-size: 14px;
  color: #7a9cc4;
}

.store-select {
  width: 180px;
}

.dine-toggle :deep(.el-radio-button__inner) {
  padding: 12px 24px;
  font-size: 17px;
}

.table-btn {
  min-width: 130px;
  font-size: 17px;
  font-weight: 600;
}

.loading-tip {
  padding: 48px;
  text-align: center;
  color: #7a9cc4;
  font-size: 20px;
}

.pending-bar {
  background: rgba(80, 165, 245, 0.12);
  border-bottom: 2px solid #5ba8f5;
  padding: 10px 16px;
}

.pending-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #5ba8f5;
  margin-bottom: 8px;
}

.pending-summary b {
  font-size: 18px;
}

.bell-icon {
  font-size: 18px;
}

.pending-cards {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.pending-card {
  flex: 0 0 auto;
  width: 160px;
  background: rgba(26, 50, 92, 0.72);
  border: 1.5px solid #ffd4c2;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: all .15s;
}

.pending-card.active {
  border-color: #5ba8f5;
  box-shadow: 0 0 0 2px rgba(255, 107, 53, .2);
  background: rgba(80, 165, 245, 0.12);
}

.pc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pc-head .pc-table {
  font-weight: 700;
  font-size: 15px;
  color: #dbeaff;
}

.pc-head .pc-time {
  font-size: 11px;
  color: #5f7c9c;
}

.pc-body {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.pc-body .pc-amount {
  color: #5ba8f5;
  font-weight: 700;
  font-size: 20px;
}

.pc-body .pc-items {
  color: #6a8fb0;
  font-size: 12px;
}

.pc-actions {
  display: flex;
  gap: 6px;
  margin-top: 2px;
}

.pc-btn {
  flex: 1;
  height: 34px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: transform .1s;
}

.pc-btn:active {
  transform: scale(.96);
}

.pc-btn.add {
  background: rgba(80, 165, 245, 0.2);
  color: #5ba8f5;
}

.pc-btn.add:hover {
  background: rgba(80, 165, 245, 0.35);
}

.pc-btn.pay {
  background: linear-gradient(100deg, #7fd4ff 0%, #4f9bf5 50%, #7fd4ff 100%);
  background-size: 220% 100%;
  color: #08203c;
  font-weight: 700;
  box-shadow: 0 0 16px rgba(100, 185, 255, 0.45);
}

.pc-btn.pay:hover {
  background: linear-gradient(100deg, #7fd4ff 0%, #4f9bf5 50%, #7fd4ff 100%);
  background-size: 220% 100%;
  background-position: 100% 0;
  box-shadow: 0 0 24px rgba(100, 185, 255, 0.7);
}

.main {
  flex: 1;
  display: grid;
  grid-template-columns: 168px 1fr minmax(300px, 380px);
  gap: 0;
  min-height: 0;
}

.cat-nav {
  background: rgba(16, 34, 66, 0.75);
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
  background: rgba(26, 50, 92, 0.72);
  padding: 14px 10px;
  font-size: 18px;
  font-weight: 600;
  color: #cfe6ff;
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
  color: #5ba8f5;
  font-weight: 700;
  background: rgba(80, 165, 245, 0.12);
  border-color: #5ba8f5;
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
  background: rgba(26, 50, 92, 0.72);
  border-radius: 14px;
  padding: 12px;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(0, 0, 0, .07);
  transition: transform .12s;
}

.product-card:active {
  transform: scale(0.98);
}

.thumb-wrap {
  aspect-ratio: 1;
  border-radius: 12px;
  overflow: hidden;
  background: rgba(16, 34, 66, 0.7);
  margin-bottom: 10px;
}

.thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.thumb.ph {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
  font-weight: 700;
  color: #5ba8f5;
  background: linear-gradient(145deg, #ffe8df, #f5f5f5);
}

.pname {
  font-size: 17px;
  font-weight: 600;
  color: #dbeaff;
  line-height: 1.35;
  min-height: 2.5em;
}

.pprice {
  margin-top: 8px;
  color: #5ba8f5;
  font-size: 20px;
  font-weight: 700;
}

.empty-menu {
  padding: 40px;
  text-align: center;
  color: #5f7c9c;
  font-size: 17px;
}

.pos-pagination {
  display: flex;
  justify-content: center;
  padding: 8px 0 4px;
}

.cart-panel {
  background: rgba(26, 50, 92, 0.72);
  border-left: 1px solid rgba(120, 200, 255, 0.16);
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

.cart-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 14px;
}

.cart-empty {
  color: #5a7696;
  text-align: center;
  padding: 32px 8px;
  font-size: 16px;
}

.cart-line {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 10px;
  padding: 12px 0;
  border-bottom: 1px dashed #f0f0f0;
}

.line-name {
  font-weight: 600;
  font-size: 17px;
}

.line-opt {
  font-size: 13px;
  color: #6a8fb0;
  margin-top: 3px;
}

.line-ctrl {
  display: flex;
  align-items: center;
  gap: 8px;
}

.qty-btn {
  width: 40px !important;
  height: 40px !important;
  font-size: 18px !important;
}

.qty {
  min-width: 28px;
  text-align: center;
  font-weight: 700;
  font-size: 18px;
}

.line-amt {
  grid-column: 2;
  text-align: right;
  color: #5ba8f5;
  font-weight: 700;
  font-size: 17px;
}

.cart-foot {
  padding: 14px 16px 18px;
  border-top: 1px solid rgba(120, 200, 255, 0.16);
  background: rgba(20, 42, 78, 0.55);
}

.sum-row {
  display: flex;
  justify-content: space-between;
  font-size: 18px;
  margin-bottom: 12px;
  align-items: baseline;
}

.sum-row.sub {
  font-size: 15px;
  color: #9bb8d8;
  margin-bottom: 6px;
}

.pack-row .waived {
  color: #6ee7a7;
  font-weight: 600;
}

.sum {
  font-size: 28px;
  font-weight: 800;
  color: #5ba8f5;
}

.remark {
  margin-bottom: 12px;
}

.remark :deep(.el-input__inner) {
  font-size: 16px;
}

.member-bind {
  margin: 8px 0 10px;
}

.member-bind label {
  display: block;
  font-size: 12px;
  color: #9bb8d8;
  margin-bottom: 4px;
}

.remember-phone-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.member-tip {
  margin: 4px 0 0;
  font-size: 12px;
  color: #9bb8d8;
}

.member-tip.muted {
  color: #6a8fb0;
}

.member-tip.warn-tip {
  color: #f5c87c;
}

.coupon-pick {
  margin-top: 10px;
}

.coupon-pick label {
  display: block;
  font-size: 13px;
  color: #9bb8d8;
  margin-bottom: 4px;
}

.coupon-pick-gap {
  margin-top: 8px !important;
}

.pay-summary-row.muted {
  color: #7a9cc4;
  font-size: 13px;
}

.pay-summary-row.muted span:last-child {
  color: #6ee7a7;
}

.social-block {
  margin-top: 8px;
}

.social-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.social-id-chip {
  margin: 0 !important;
  color: #6ee7a7 !important;
}

.service-card {
  margin-top: 10px;
  padding: 10px;
  background: rgba(16, 34, 66, 0.7);
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: #dbeaff;
}

.service-card .sc-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.service-card .sc-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 2px;
}

.service-card .sc-line.muted,
.service-card .muted {
  color: #7a9cc4;
}

.service-card .sc-block {
  margin-top: 4px;
}

.service-card .sc-block.warn {
  color: #f5c87c;
}

.service-card .sc-block b {
  margin-right: 4px;
  color: #b7d3f0;
}

.pay-member {
  margin-bottom: 12px;
}

.pay-btn {
  width: 100%;
  height: 60px;
  font-size: 20px;
  font-weight: 700;
  border-radius: 12px;
}

.table-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 12px;
}

.table-chip {
  border: 2px solid rgba(120, 200, 255, 0.2);
  background: rgba(26, 50, 92, 0.72);
  border-radius: 12px;
  padding: 20px 10px;
  font-size: 18px;
  font-weight: 600;
  cursor: pointer;
}

.table-chip.on {
  border-color: #5ba8f5;
  background: rgba(80, 165, 245, 0.12);
  color: #5ba8f5;
}

.table-chip.walk-in {
  color: #5ba8f5;
  border-color: rgba(127, 200, 255, 0.55);
  background: rgba(80, 165, 245, 0.16);
}

.table-chip.walk-in.on {
  border-color: #5ba8f5;
  background: rgba(80, 165, 245, 0.2);
  color: #5ba8f5;
}

.empty-tables {
  text-align: center;
  color: #6a8fb0;
  padding: 24px;
  font-size: 16px;
}

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

.drawer-head-main {
  min-width: 0;
  flex: 1;
}

.drawer-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #e8f4ff;
}

.drawer-summary {
  margin-top: 6px;
  font-size: 15px;
  color: #9bb8d8;
  line-height: 1.4;
  word-break: break-all;
}

.drawer-price-live {
  font-size: 30px;
  font-weight: 800;
  color: #5ba8f5;
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
  background: rgba(16, 34, 66, 0.7);
  border-right: 1px solid rgba(120, 200, 255, 0.16);
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
  background: rgba(26, 50, 92, 0.72);
  text-align: left;
  cursor: pointer;
  color: #dbeaff;
}

.opt-nav-item.on {
  border-color: #5ba8f5;
  background: rgba(80, 165, 245, 0.12);
  color: #5ba8f5;
}

.opt-nav-item.done:not(.on) {
  border-color: #d9f7be;
  background: rgba(110, 231, 167, 0.14);
}

.opt-nav-item.missing:not(.on) {
  border-color: #ffccc7;
  background: rgba(255, 100, 100, 0.16);
}

.nav-name {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.2;
}

.nav-value {
  font-size: 12px;
  color: #9bb8d8;
  font-weight: 600;
  line-height: 1.3;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.opt-nav-item.on .nav-value {
  color: #5ba8f5;
}

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
  color: #dbeaff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.opt-pane-title .req {
  font-size: 13px;
  color: #5ba8f5;
  font-weight: 600;
}

.opt-hint {
  font-size: 13px;
  color: #6a8fb0;
  font-weight: 500;
}

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
  .opt-nav {
    width: 200px;
  }

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
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 88px;
  padding: 16px 12px;
  border: 2px solid #e0e0e0;
  border-radius: 14px;
  background: rgba(26, 50, 92, 0.72);
  cursor: pointer;
  transition: border-color .12s, background .12s, transform .08s;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}

.tile-num {
  position: absolute;
  top: 8px;
  left: 10px;
  min-width: 22px;
  height: 22px;
  padding: 0 5px;
  border-radius: 6px;
  background: rgba(91, 168, 245, 0.22);
  color: #9fd0ff;
  font-size: 13px;
  font-weight: 700;
  line-height: 22px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.opt-tile.on .tile-num {
  background: rgba(255, 255, 255, 0.28);
  color: #fff;
}

.opt-tile:active {
  transform: scale(0.98);
}

.opt-tile.on {
  border-color: #5ba8f5;
  background: rgba(80, 165, 245, 0.12);
  color: #5ba8f5;
  box-shadow: inset 0 0 0 1px #5ba8f5;
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

.tile-extra.muted {
  color: #5f7c9c;
  font-weight: 500;
}

.opt-empty {
  padding: 48px 16px;
  text-align: center;
  color: #6a8fb0;
  font-size: 16px;
}

.drawer-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(120, 200, 255, 0.16);
  background: rgba(20, 42, 78, 0.55);
  flex-shrink: 0;
}

.enter-hint {
  flex: 0 0 100%;
  order: -1;
  margin: 0;
  font-size: 12px;
  line-height: 1.3;
  color: rgba(180, 210, 240, 0.7);
  text-align: center;
  letter-spacing: 0.02em;
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
  .opt-nav {
    width: 120px;
  }

  .opt-nav-item {
    min-height: 56px;
    padding: 10px;
  }

  .nav-name {
    font-size: 15px;
  }

  .opt-tiles,
  .opt-tiles.few,
  .opt-tiles.many {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .opt-tile {
    min-height: 76px;
  }

  .tile-name {
    font-size: 17px;
  }

  .nav-step-btn {
    display: none;
  }

  .enter-hint {
    display: none;
  }
}


/* 收款弹窗：组合分笔 */
.pay-dialog :deep(.el-dialog__body) {
  padding-top: 8px;
}

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
  color: #9bb8d8;
}

.pay-summary-row.remain {
  margin-top: 2px;
}

.pay-total {
  font-size: 22px;
  font-weight: 800;
  color: #dbeaff;
}

.pay-paid {
  font-size: 22px;
  font-weight: 700;
  color: #6ee7a7;
}

.pay-remain {
  font-size: 30px;
  font-weight: 800;
  color: #5ba8f5;
}

.tender-list {
  margin-bottom: 14px;
  padding: 10px 12px;
  background: rgba(16, 34, 66, 0.7);
  border-radius: 10px;
}

.tender-item {
  display: flex;
  justify-content: space-between;
  font-size: 15px;
  color: #cfe6ff;
  padding: 4px 0;
}

.tender-amt-wrap {
  margin-bottom: 16px;
}

.tender-amt-wrap label {
  display: block;
  font-size: 16px;
  color: #9bb8d8;
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
  color: #6a8fb0;
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
  background: rgba(80, 165, 245, 0.12);
  border-radius: 10px;
  font-size: 15px;
}

.pay-pack-info {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: #9bb8d8;
}

.pay-pack-info .waived {
  color: #6ee7a7;
  font-weight: 600;
}

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
  color: #e8f4ff;
  transition: opacity .15s, transform .1s;
}

.pay-method:active {
  transform: scale(0.99);
}

.pay-method:disabled {
  opacity: .6;
  cursor: not-allowed;
}

.pay-method.scan {
  background: linear-gradient(135deg, #07c160 0%, #5ba8f5 100%);
}

.pay-method.cash {
  background: linear-gradient(135deg, #ff8c42, #5ba8f5);
}

.pm-label-wrap {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  text-align: left;
}

.pm-sub {
  font-size: 14px;
  font-weight: 500;
  opacity: 0.9;
}

.pm-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: rgba(255, 255, 255, .25);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 800;
}

.pm-label {
  flex: 1;
  text-align: left;
}

.cash-panel {
  padding: 4px 0;
}

.cash-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 18px;
  margin-bottom: 14px;
}

.cash-amt {
  font-size: 26px;
  font-weight: 800;
  color: #5ba8f5;
}

.cash-input-wrap label {
  display: block;
  font-size: 16px;
  color: #9bb8d8;
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
  background: rgba(26, 50, 92, 0.72);
  border-radius: 10px;
  padding: 14px 8px;
  font-size: 18px;
  font-weight: 700;
  color: #dbeaff;
  cursor: pointer;
}

.cash-quick-btn.exact {
  border-color: #5ba8f5;
  color: #5ba8f5;
}

.cash-quick-btn:active {
  background: rgba(80, 165, 245, 0.12);
}

.cash-input :deep(.el-input__inner) {
  font-size: 24px;
  font-weight: 700;
  height: 56px;
}

.change-row {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed rgba(120, 200, 255, 0.16);
}

.change-amt {
  font-size: 30px;
  font-weight: 800;
  color: #6ee7a7;
}

.change-amt.warn {
  color: #f5222d;
}

.cash-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.cash-actions .el-button {
  flex: 1;
  height: 52px;
  font-size: 17px;
}

.cash-confirm {
  flex: 2 !important;
}

.round-down-row {
  margin-top: 12px;
}

.add-items-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: rgba(80, 165, 245, 0.16);
  border-radius: 8px;
  font-size: 14px;
  color: #5ba8f5;
}

.add-items-actions {
  display: flex;
  gap: 8px;
}

.add-items-actions .el-button {
  flex: 1;
  height: 60px;
  font-size: 18px;
  font-weight: 700;
  border-radius: 12px;
}

.checkout-actions {
  display: flex;
  gap: 8px;
}

.checkout-actions .el-button {
  height: 60px;
  font-size: 18px;
  font-weight: 700;
  border-radius: 12px;
}

.checkout-actions .pay-btn {
  flex: 2;
}

.checkout-actions .park-btn {
  flex: 1;
  color: #5ba8f5;
  border-color: rgba(127, 200, 255, 0.55);
  background: rgba(80, 165, 245, 0.16);
}

.barcode-panel {
  text-align: center;
}

.barcode-panel .scan-provider {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 8px;
}

.scan-detected {
  font-size: 16px;
  color: #5ba8f5;
  font-weight: 600;
  margin: 0 0 12px;
}

.scan-detected.warn {
  color: #e6a23c;
}

.barcode-input {
  margin: 16px 0;
}

.barcode-input :deep(.el-input__inner) {
  font-size: 20px;
  height: 56px;
  text-align: center;
  letter-spacing: 1px;
}

.scan-hint {
  color: #7a9cc4;
  font-size: 15px;
  margin: 8px 0 16px;
}

.shift-hint {
  color: #9bb8d8;
  margin: 0 0 12px;
  font-size: 15px;
}

.shift-summary {
  font-size: 15px;
  line-height: 1.6;
  color: #cfe6ff;
}

.shift-summary b {
  color: #5ba8f5;
  font-size: 20px;
}

.shift-orders-head {
  margin-bottom: 16px;
  font-size: 15px;
  color: #9bb8d8;
  line-height: 1.6;
}

.shift-orders-sum b {
  color: #5ba8f5;
  font-size: 18px;
}

.shift-orders-tip {
  text-align: center;
  color: #5f7c9c;
  padding: 32px 8px;
  font-size: 16px;
}

.shift-orders-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.shift-order-row {
  border: 1px solid rgba(120, 200, 255, 0.2);
  border-radius: 12px;
  padding: 14px;
  background: rgba(20, 42, 78, 0.55);
}

.so-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.so-no {
  font-size: 15px;
  font-weight: 600;
  color: #dbeaff;
  word-break: break-all;
}

.so-amt {
  font-size: 20px;
  font-weight: 700;
  color: #5ba8f5;
  flex-shrink: 0;
}

.so-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  font-size: 14px;
  color: #7a9cc4;
}

.so-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
}

.so-refund-tag {
  font-size: 13px;
  font-weight: 600;
  color: #909399;
}

.so-refund-tag.pending {
  color: #e6a23c;
}

.so-refund-tag.ok {
  color: #6ee7a7;
}

.so-refund-tag.bad {
  color: #f56c6c;
}

.refund-hint {
  margin: 0 0 12px;
  color: #9bb8d8;
  font-size: 15px;
}

.refund-hint b {
  color: #5ba8f5;
}

.refund-tenders {
  margin: 0 0 14px;
  padding: 10px 12px;
  background: rgba(16, 34, 66, 0.7);
  border-radius: 10px;
}

.refund-tender-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 14px;
  color: #cfe6ff;
  padding: 4px 0;
}

.refund-alloc-tip {
  margin: 8px 0 0;
  font-size: 12px;
  color: #6a8fb0;
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
  background: rgba(26, 50, 92, 0.72);
  border-radius: 16px;
  padding: 32px 24px;
  text-align: center;
}

.lock-logo {
  width: 80px;
  height: 80px;
  object-fit: contain;
  border-radius: 12px;
  margin-bottom: 12px;
  background: rgba(26, 50, 92, 0.72);
}

.lock-user {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 20px;
  color: #dbeaff;
}

.lock-input {
  margin-bottom: 16px;
}

.lock-input :deep(.el-input__inner) {
  font-size: 18px;
  height: 52px;
}

.lock-btn {
  width: 100%;
  height: 52px;
  font-size: 18px;
  margin-bottom: 8px;
}

@media (max-width: 900px) {
  .main {
    grid-template-columns: 148px 1fr;
    grid-template-rows: 1fr auto;
  }

  .cat-nav {
    padding: 8px 6px;
    gap: 8px;
  }

  .cat-item {
    min-height: 56px;
    font-size: 17px;
    padding: 12px 8px;
  }

  .cart-panel {
    grid-column: 1 / -1;
    max-height: 44vh;
    border-left: none;
    border-top: 1px solid rgba(120, 200, 255, 0.16);
  }

  .opt-chip {
    min-width: 88px;
    min-height: 52px;
    padding: 10px 14px;
  }

  .chip-name {
    font-size: 16px;
  }
}
</style>
