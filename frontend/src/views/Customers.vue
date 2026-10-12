<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select v-model="storeFilter" placeholder="本店相关顾客" clearable style="width:160px" @change="reload">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-input v-model="keyword" placeholder="昵称 / 手机 / 编号" clearable style="width: 200px" @keyup.enter="reload"
          @clear="reload" />
      </el-form-item>
      <el-form-item>
        <el-select v-model="channel" placeholder="渠道" clearable style="width: 120px" @change="reload">
          <el-option label="微信" value="WECHAT" />
          <el-option label="支付宝" value="ALIPAY" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="memberStatusFilter" placeholder="会员身份" clearable style="width: 120px" @change="reload">
          <el-option label="会员" value="MEMBER" />
          <el-option label="游客" value="GUEST" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="levelFilter" placeholder="会员等级" clearable style="width: 130px" @change="reload">
          <el-option v-for="lv in levels" :key="lv.id" :label="lv.name" :value="lv.id" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="statusFilter" placeholder="账号状态" style="width: 120px" @change="reload">
          <el-option label="启用" :value="1" />
          <el-option label="禁用" :value="0" />
          <el-option label="全部" :value="-1" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-date-picker v-model="registeredRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="注册起"
          end-placeholder="注册止" style="width:240px" @change="reload" />
      </el-form-item>
      <el-form-item>
        <el-select v-model="lifecycleFilter" placeholder="生命周期" clearable style="width: 130px" @change="reload">
          <el-option label="新客 NEW" value="NEW" />
          <el-option label="活跃 ACTIVE" value="ACTIVE" />
          <el-option label="风险 AT_RISK" value="AT_RISK" />
          <el-option label="沉睡 DORMANT" value="DORMANT" />
          <el-option label="流失 LOST" value="LOST" />
          <el-option label="游客 GUEST" value="GUEST" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="rfmFilter" placeholder="RFM 分群" clearable style="width: 140px" @change="reload">
          <el-option label="冠军 CHAMPIONS" value="CHAMPIONS" />
          <el-option label="忠诚 LOYAL" value="LOYAL" />
          <el-option label="潜力 NEW_PROMISING" value="NEW_PROMISING" />
          <el-option label="风险 AT_RISK" value="AT_RISK" />
          <el-option label="休眠 HIBERNATING" value="HIBERNATING" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="birthdayTodayOnly" @change="reload">今日生日</el-checkbox>
      </el-form-item>
      <el-button type="primary" @click="reload">搜索</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <el-button @click="goMemberLevels">会员等级设置</el-button>
      <el-button link type="primary" @click="goBirthdayBenefitSettings">生日优惠怎么改</el-button>
    </el-form>
    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="birthday-alert"
      title="生命周期 / RFM"
      description="生命周期与 RFM 由夜间批处理刷新（新客/活跃/风险/沉睡/流失等）。筛选用作运营排查；圈人发券请到「客户分群 → 营销活动」。社会贡献身份审核在「社会贡献」。"
    />
    <el-alert
      v-if="listBirthdayBenefit?.text"
      type="info"
      show-icon
      :closable="false"
      class="birthday-alert"
      :title="birthdayTodayOnly ? '今日生日提醒' : '生日权益'"
      :description="listBirthdayBenefit.text + ' · 修改入口：门店 → 门店信息 → 业务参数 → 会员与优惠'"
    />

    <el-table :data="customers" border class="customer-table">
      <el-table-column label="头像" width="72" align="center">
        <template #default="{ row }">
          <el-image v-if="row.avatar_url" :src="mediaUrl(row.avatar_url)" fit="cover" class="avatar-thumb"
            :preview-src-list="[mediaUrl(row.avatar_url)]" preview-teleported />
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
      <el-table-column label="生命周期" width="100">
        <template #default="{ row }">{{ row.lifecycle_code || '—' }}</template>
      </el-table-column>
      <el-table-column label="RFM" width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ row.rfm_segment || '—' }}</template>
      </el-table-column>
      <el-table-column label="会员等级" width="110">
        <template #default="{ row }">{{ row.member_level_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="积分" width="80">
        <template #default="{ row }">{{ row.member_points ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="生日" width="120">
        <template #default="{ row }">
          <span v-if="row.birthday">
            {{ row.birthday_calendar === 'LUNAR' ? '农历' : '公历' }} {{ row.birthday }}
            <el-tag v-if="row.is_birthday_today" size="small" type="danger" style="margin-left:4px">今日</el-tag>
          </span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="储值余额" min-width="150">
        <template #default="{ row }">
          <div>¥{{ row.balance_total || '0.00' }}</div>
          <div class="muted bal-sub">
            本金 {{ row.balance_principal || '0.00' }} · 赠送 {{ row.balance_gift || '0.00' }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="折扣" width="80">
        <template #default="{ row }">
          {{
            row.member_discount_rate != null && Number(row.member_discount_rate) < 1 ?
              `${(Number(row.member_discount_rate) * 10).toFixed(1)}折` : (row.is_member ? '无折扣' : '—') }} </template>
      </el-table-column>
      <el-table-column label="渠道" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.channels?.includes('WECHAT')" size="small" type="success"
            style="margin-right:4px">微信</el-tag>
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
          <div v-if="row.preferences?.length" class="pref-tags"
            :title="row.preferences.map((p: any) => p.label).join('、')">
            <el-tag v-for="(p, i) in row.preferences" :key="`${p.option_code || i}-${i}`" size="small" type="danger"
              effect="plain" class="pref-tag">{{ p.label }}</el-tag>
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

    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next" style="margin-top:16px" @current-change="load" @size-change="reload" />

    <el-drawer v-model="drawer" title="顾客详情 · 会员与口味档案" size="640px">
      <template v-if="detail">
        <div class="detail-head">
          <el-image v-if="detail.avatar_url" :src="mediaUrl(detail.avatar_url)" fit="cover" class="avatar-lg"
            :preview-src-list="[mediaUrl(detail.avatar_url)]" preview-teleported />
          <div v-else class="avatar-lg placeholder">无头像</div>
          <div class="detail-head-meta">
            <div class="detail-name">{{ detail.display_name || detail.nickname || '—' }}</div>
            <div class="muted">{{ detail.customer_no }}</div>
          </div>
        </div>

        <div class="section-head">
          <h4>基本信息</h4>
          <el-button v-if="canEditCustomer" type="primary" size="small" :loading="savingProfile" @click="saveProfile">
            保存修改
          </el-button>
        </div>
        <el-form v-if="canEditCustomer" label-width="96px" class="edit-form" @submit.prevent>
          <el-form-item label="编号">
            <span>{{ detail.customer_no }}</span>
          </el-form-item>
          <el-form-item label="展示名">
            <el-input v-model="editForm.display_name" maxlength="100" clearable placeholder="顾客展示名" />
          </el-form-item>
          <el-form-item label="昵称">
            <el-input v-model="editForm.nickname" maxlength="100" clearable placeholder="昵称" />
          </el-form-item>
          <el-form-item label="手机">
            <el-input v-model="editForm.phone" maxlength="11" clearable placeholder="11 位手机号，可清空" />
          </el-form-item>
          <el-form-item label="生日历法">
            <el-radio-group
              v-model="editForm.birthday_calendar"
              :disabled="savingProfile"
              @change="onBirthdayCalendarChange"
            >
              <el-radio value="SOLAR">公历</el-radio>
              <el-radio value="LUNAR">农历</el-radio>
            </el-radio-group>
            <div class="muted tip">切换历法：同一天按公历/农历换展示与每年匹配规则；存库仍为对应公历锚点日</div>
          </el-form-item>
          <el-form-item label="生日">
            <el-date-picker
              v-model="editForm.birthday"
              type="date"
              value-format="YYYY-MM-DD"
              clearable
              :disabled="savingProfile"
              :placeholder="editForm.birthday_calendar === 'LUNAR' ? '选对应公历日（按农历月日记）' : '选填公历日期'"
              style="width:100%"
              @change="onBirthdayDateChange"
            />
            <div v-if="birthdayDisplayText" class="birthday-live">
              当前展示：<strong>{{ birthdayDisplayText }}</strong>
            </div>
            <div class="muted tip">
              {{
                editForm.birthday_calendar === 'LUNAR'
                  ? '农历模式：请选「对应公历日」；上方会显示换算后的农历月日'
                  : '公历模式：直接选公历年月日'
              }}
            </div>
          </el-form-item>
          <el-alert
            v-if="detail.birthday_benefit?.text"
            type="success"
            show-icon
            :closable="false"
            :title="detail.birthday_benefit.is_birthday_today ? '今日生日 · 权益生效中' : '生日优惠'"
            :description="(birthdayDisplayText ? birthdayDisplayText + '。' : '') + detail.birthday_benefit.text"
            style="margin-bottom:12px"
          />
          <el-form-item label="账号状态">
            <el-radio-group v-model="editForm.status">
              <el-radio :value="1">启用</el-radio>
              <el-radio :value="0">禁用</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="智能推荐">
            <el-switch v-model="editForm.recommend_opt_in" :active-value="1" :inactive-value="0" />
          </el-form-item>
        </el-form>
        <el-descriptions v-else :column="1" border>
          <el-descriptions-item label="编号">{{ detail.customer_no }}</el-descriptions-item>
          <el-descriptions-item label="展示名">{{ detail.display_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="昵称">{{ detail.nickname || '—' }}</el-descriptions-item>
          <el-descriptions-item label="手机">{{ detail.phone || '—' }}</el-descriptions-item>
          <el-descriptions-item label="生日">
            {{ detail.birthday_display || detail.birthday_benefit?.birthday_display || detail.birthday || '—' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.birthday_benefit?.text" label="生日优惠">
            {{ detail.birthday_benefit.text }}
            <div class="muted tip">{{ detail.birthday_benefit.how_to_edit }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="账号状态">
            <el-tag size="small" :type="detail.status === 1 ? 'success' : 'danger'">
              {{ detail.status === 1 ? '启用' : '禁用' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="智能推荐">{{ detail.recommend_opt_in ? '开启' : '关闭' }}</el-descriptions-item>
        </el-descriptions>

        <el-descriptions :column="1" border style="margin-top:12px">
          <el-descriptions-item label="注册门店">
            <template v-if="detail.registered_store_name">
              {{ detail.registered_store_name }}
              <span v-if="detail.registered_table_name"> · {{ detail.registered_table_name }}</span>
              <span v-if="detail.registered_table_no" class="muted">（{{ detail.registered_table_no }}）</span>
            </template>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="是否会员">
            <el-tag size="small" :type="detail.is_member ? 'success' : 'info'">
              {{ detail.is_member ? '已注册会员' : '未注册/游客' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="会员等级">{{ detail.member_level_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="当前积分">{{ detail.member_points ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="储值余额">
            <div>合计 <strong>¥{{ detail.balance_total || '0.00' }}</strong></div>
            <div class="muted">
              本金 ¥{{ detail.balance_principal || '0.00' }}
              · 赠送 ¥{{ detail.balance_gift || '0.00' }}
            </div>
          </el-descriptions-item>
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
          <el-descriptions-item label="人脸迎宾">
            {{ detail.has_face ? '已授权（到店摄像头识别）' : '未开启' }}
          </el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ detail.created_at || '—' }}</el-descriptions-item>
          <el-descriptions-item label="最近下单">{{ detail.last_order_at || '—' }}</el-descriptions-item>
        </el-descriptions>

        <div class="pref-block">
          <div class="section-head">
            <h4>收藏餐品</h4>
          </div>
          <div v-if="canEditCustomer" class="fav-add">
            <el-select v-model="favAddProductId" filterable remote clearable reserve-keyword
              placeholder="搜索商品名/编号添加收藏" :remote-method="searchProductsForFav" :loading="favSearching"
              style="flex:1" @change="onFavProductPicked">
              <el-option v-for="p in favProductOptions" :key="p.id"
                :label="`${p.name}（¥${p.price}）`" :value="p.id"
                :disabled="favoriteProductIds.has(p.id)" />
            </el-select>
          </div>
          <el-table v-if="detail.favorites?.length" :data="detail.favorites" size="small" border>
            <el-table-column label="商品" min-width="140">
              <template #default="{ row }">{{ row.name }}</template>
            </el-table-column>
            <el-table-column label="价格" width="80">
              <template #default="{ row }">¥{{ row.price }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'ON_SALE' ? 'success' : 'info'">
                  {{ row.status === 'ON_SALE' ? (row.is_sold_out ? '售罄' : '在售') : '下架' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="canEditCustomer" label="操作" width="72" fixed="right">
              <template #default="{ row }">
                <el-button link type="danger" :loading="favBusyId === row.product_id"
                  @click="removeFavorite(row.product_id)">移除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="muted">暂无收藏餐品</p>
        </div>

        <div class="pref-block">
          <div class="section-head">
            <h4>优惠券</h4>
            <el-button v-if="canIssueCoupon" type="success" size="small"
              @click="openIssueDialog(detail)">手动发券</el-button>
          </div>
          <p v-if="couponsLoading" class="muted">加载中…</p>
          <el-table v-else-if="customerCoupons.length" :data="customerCoupons" size="small" border>
            <el-table-column prop="template_name" label="名称" min-width="100" />
            <el-table-column prop="summary" label="规则" min-width="110" />
            <el-table-column label="发券人" width="90">
              <template #default="{ row }">{{ row.issued_by_name || '系统发放' }}</template>
            </el-table-column>
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
            <el-table-column label="发券人" width="90">
              <template #default="{ row }">{{ row.issued_by_name || '系统发放' }}</template>
            </el-table-column>
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
          <el-alert v-if="detail.welcome_hint" :title="detail.welcome_hint" type="success" :closable="false" show-icon
            style="margin-bottom:12px" />
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
          <li v-for="(w, i) in detail.wechat_accounts" :key="'w' + i" class="acct-row">
            <el-image v-if="w.avatar_url" :src="mediaUrl(w.avatar_url)" fit="cover" class="avatar-sm"
              :preview-src-list="[mediaUrl(w.avatar_url)]" preview-teleported />
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
          <li v-for="(a, i) in detail.alipay_accounts" :key="'a' + i" class="acct-row">
            <el-image v-if="a.avatar_url" :src="mediaUrl(a.avatar_url)" fit="cover" class="avatar-sm"
              :preview-src-list="[mediaUrl(a.avatar_url)]" preview-teleported />
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

    <el-dialog v-model="mergeDialog" title="合并顾客到当前档案" width="560px" destroy-on-close>
      <el-alert type="warning" :closable="false" show-icon title="不可逆操作"
        description="将源顾客的账号、订单、券、积分等迁入目标顾客，源顾客停用。可按手机号、昵称、编号或 ID 搜索选择源顾客。" style="margin-bottom: 12px" />
      <el-form label-width="100px">
        <el-form-item label="目标顾客">
          <span>{{ mergeTargetLabel }}</span>
        </el-form-item>
        <el-form-item label="源顾客" required>
          <el-select v-model="mergeSourceId" filterable remote clearable reserve-keyword
            :remote-method="searchMergeSources" :loading="mergeSourceLoading" placeholder="输入手机号 / 昵称 / 编号 / ID 搜索"
            style="width: 100%">
            <el-option v-for="c in mergeSourceOptions" :key="c.id" :label="formatMergeOption(c)" :value="c.id"
              :disabled="c.id === mergeTarget?.id" />
          </el-select>
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
import { useStoreOptions } from '@/composables/useStoreOptions'
import { mediaUrl } from '@/utils/media'
import { useAuthStore } from '@/stores/auth'
import { COUPON_STATUS, couponStatusTagType, label } from '@/utils/labels'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const customers = ref<any[]>([])
const { storeOptions: stores, loadStores } = useStoreOptions()
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const birthdayTodayOnly = ref(false)
const listBirthdayBenefit = ref<{ text?: string; enabled?: boolean; points_multiplier?: number } | null>(null)
const channel = ref<string | undefined>()
const memberStatusFilter = ref<string | undefined>()
const lifecycleFilter = ref<string | undefined>()
const rfmFilter = ref<string | undefined>()
const levelFilter = ref<number | undefined>()
const statusFilter = ref<number | undefined>(1)
const registeredRange = ref<string[] | null>(null)
const levels = ref<any[]>([])
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
const canEditCustomer = computed(
  () => auth.isSuperAdmin || auth.hasPermission('customer:write'),
)

const editForm = reactive({
  display_name: '',
  nickname: '',
  phone: '',
  birthday: '' as string | null,
  birthday_calendar: 'SOLAR' as 'SOLAR' | 'LUNAR',
  status: 1,
  recommend_opt_in: 1,
})
const savingProfile = ref(false)
const favAddProductId = ref<number | undefined>()
const favProductOptions = ref<any[]>([])
const favSearching = ref(false)
const favBusyId = ref<number | null>(null)
let favSearchSeq = 0

const favoriteProductIds = computed(() => {
  const set = new Set<number>()
  for (const f of detail.value?.favorites || []) {
    if (f?.product_id != null) set.add(Number(f.product_id))
  }
  return set
})

function syncEditForm(d: any) {
  editForm.display_name = d?.display_name || ''
  editForm.nickname = d?.nickname || ''
  editForm.phone = d?.phone || ''
  editForm.birthday = d?.birthday || null
  editForm.birthday_calendar = d?.birthday_calendar === 'LUNAR' ? 'LUNAR' : 'SOLAR'
  editForm.status = d?.status === 0 ? 0 : 1
  editForm.recommend_opt_in = d?.recommend_opt_in ? 1 : 0
}

/** 历法切换后的展示文案（顶层优先，与权益说明内文案一致） */
const birthdayDisplayText = computed(() => {
  const d = detail.value
  if (!d) return ''
  return (
    d.birthday_display
    || d.birthday_benefit?.birthday_display
    || ''
  )
})

function applyDetailPayload(payload: any) {
  detail.value = payload
  syncEditForm(payload)
}

async function onBirthdayCalendarChange(cal: string | number | boolean | undefined) {
  const id = detail.value?.id
  const next = cal === 'LUNAR' ? 'LUNAR' : 'SOLAR'
  if (!id) return
  // 尚无生日：只改本地历法，等选日期后再保存联动
  if (!editForm.birthday) {
    editForm.birthday_calendar = next
    return
  }
  savingProfile.value = true
  try {
    const body: Record<string, any> = { birthday_calendar: next }
    // 表单日期若尚未落库，一并提交，避免历法已切但展示仍用旧锚点
    if (editForm.birthday !== detail.value?.birthday) {
      body.birthday = editForm.birthday
    }
    const res = await api.patch(`/customers/${id}`, body)
    applyDetailPayload(res.data)
    ElMessage.success(
      next === 'LUNAR'
        ? `已切换农历：${birthdayDisplayText.value || '同日对应农历月日'}`
        : `已切换公历：${birthdayDisplayText.value || '同日公历月日'}`,
    )
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '历法切换失败')
    editForm.birthday_calendar = detail.value?.birthday_calendar === 'LUNAR' ? 'LUNAR' : 'SOLAR'
  } finally {
    savingProfile.value = false
  }
}

/** 改日期后若已有历法，立即落库刷新展示（与历法联动） */
async function onBirthdayDateChange(val: string | null) {
  const id = detail.value?.id
  if (!id) return
  // 清空生日
  if (!val) {
    savingProfile.value = true
    try {
      const res = await api.patch(`/customers/${id}`, { birthday: null })
      applyDetailPayload(res.data)
      await load()
    } catch (e: any) {
      ElMessage.error(e.message || '清空生日失败')
      editForm.birthday = detail.value?.birthday || null
    } finally {
      savingProfile.value = false
    }
    return
  }
  savingProfile.value = true
  try {
    const res = await api.patch(`/customers/${id}`, {
      birthday: val,
      birthday_calendar: editForm.birthday_calendar || 'SOLAR',
    })
    applyDetailPayload(res.data)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '生日更新失败')
    editForm.birthday = detail.value?.birthday || null
  } finally {
    savingProfile.value = false
  }
}

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
const mergeSourceOptions = ref<any[]>([])
const mergeSourceLoading = ref(false)
let mergeSearchSeq = 0

const mergeTargetLabel = computed(() => {
  const c = mergeTarget.value
  if (!c) return '—'
  const name = c.display_name || c.nickname || '顾客'
  return `${name}（ID ${c.id} / ${c.customer_no}）`
})

function formatMergeOption(c: any) {
  const name = c.display_name || c.nickname || '顾客'
  const phone = c.phone ? ` · ${c.phone}` : ''
  const no = c.customer_no ? ` · ${c.customer_no}` : ''
  return `${name}${phone}${no}（ID ${c.id}）`
}

async function searchMergeSources(query: string) {
  const kw = String(query || '').trim()
  if (!kw) {
    mergeSourceOptions.value = []
    return
  }
  const seq = ++mergeSearchSeq
  mergeSourceLoading.value = true
  try {
    const params: Record<string, any> = {
      page: 1,
      page_size: 30,
      keyword: kw,
      status: 1,
    }
    // 与列表一致：门店账号随本店范围；超管可带当前筛选门店缩小范围
    if (storeFilter.value) params.store_id = storeFilter.value
    const res = await api.get('/customers', { params })
    if (seq !== mergeSearchSeq) return
    const tid = mergeTarget.value?.id
    mergeSourceOptions.value = (res.data?.items || []).filter((c: any) => c.id !== tid)
  } catch {
    if (seq === mergeSearchSeq) mergeSourceOptions.value = []
  } finally {
    if (seq === mergeSearchSeq) mergeSourceLoading.value = false
  }
}

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
  return label(COUPON_STATUS, s, s || '—')
}

function couponStatusTag(s: string) {
  return couponStatusTagType(s)
}

function reload() {
  page.value = 1
  load()
}

function resetFilters() {
  storeFilter.value = undefined
  keyword.value = ''
  channel.value = undefined
  memberStatusFilter.value = undefined
  lifecycleFilter.value = undefined
  rfmFilter.value = undefined
  levelFilter.value = undefined
  statusFilter.value = 1
  registeredRange.value = null
  birthdayTodayOnly.value = false
  reload()
}

function goMemberLevels() {
  router.push('/member-levels')
}

function goBirthdayBenefitSettings() {
  const sid = storeFilter.value || auth.user?.store_id
  const q = sid ? `?tab=biz&store_id=${sid}` : '?tab=biz'
  router.push(`/store-info${q}`)
}

async function loadLevels() {
  try {
    const res = await api.get('/member-levels')
    levels.value = res.data?.items || []
  } catch {
    levels.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      channel: channel.value || undefined,
      member_status: memberStatusFilter.value || undefined,
      lifecycle: lifecycleFilter.value || undefined,
      rfm_segment: rfmFilter.value || undefined,
      member_level_id: levelFilter.value,
      store_id: storeFilter.value,
    }
    if (statusFilter.value === 1 || statusFilter.value === 0) {
      params.status = statusFilter.value
    }
    if (registeredRange.value?.length === 2) {
      params.registered_from = registeredRange.value[0]
      params.registered_to = registeredRange.value[1]
    }
    if (birthdayTodayOnly.value) params.birthday_today = true
    const res = await api.get('/customers', { params })
    const list = res.data.items
    listBirthdayBenefit.value = res.data.birthday_benefit || null
    // 禁用/合并后末页可能变空，自动回退避免空白页
    if (list.length === 0 && page.value > 1) {
      page.value -= 1
      loading.value = false
      await load()
      return
    }
    customers.value = list
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
  favAddProductId.value = undefined
  favProductOptions.value = []
  const [detailRes] = await Promise.all([
    api.get(`/customers/${id}`),
    loadCustomerCoupons(id),
  ])
  applyDetailPayload(detailRes.data)
}

async function saveProfile() {
  const id = detail.value?.id
  if (!id) return
  const phone = String(editForm.phone || '').trim()
  if (phone && !/^1\d{10}$/.test(phone)) {
    ElMessage.warning('手机号须为 11 位中国大陆号段')
    return
  }
  savingProfile.value = true
  try {
    const res = await api.patch(`/customers/${id}`, {
      display_name: editForm.display_name?.trim() || null,
      nickname: editForm.nickname?.trim() || null,
      phone: phone || null,
      birthday: editForm.birthday || null,
      birthday_calendar: editForm.birthday_calendar || 'SOLAR',
      status: editForm.status,
      recommend_opt_in: editForm.recommend_opt_in,
    })
    applyDetailPayload(res.data)
    ElMessage.success('顾客信息已保存')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingProfile.value = false
  }
}

async function searchProductsForFav(query: string) {
  const kw = String(query || '').trim()
  if (!kw) {
    favProductOptions.value = []
    return
  }
  const seq = ++favSearchSeq
  favSearching.value = true
  try {
    const params: Record<string, any> = {
      page: 1,
      page_size: 30,
      keyword: kw,
      status: 'ON_SALE',
    }
    if (storeFilter.value) params.store_id = storeFilter.value
    const res: any = await api.get('/products', { params })
    if (seq !== favSearchSeq) return
    favProductOptions.value = res.data?.items || []
  } catch {
    if (seq === favSearchSeq) favProductOptions.value = []
  } finally {
    if (seq === favSearchSeq) favSearching.value = false
  }
}

async function onFavProductPicked(productId: number | undefined) {
  if (!productId || !detail.value?.id) return
  if (favoriteProductIds.value.has(productId)) {
    ElMessage.info('已在收藏中')
    favAddProductId.value = undefined
    return
  }
  favBusyId.value = productId
  try {
    const res = await api.post(`/customers/${detail.value.id}/favorites/${productId}`)
    detail.value = { ...detail.value, favorites: res.data?.favorites || [] }
    ElMessage.success('已添加收藏')
  } catch (e: any) {
    ElMessage.error(e.message || '添加失败')
  } finally {
    favBusyId.value = null
    favAddProductId.value = undefined
  }
}

async function removeFavorite(productId: number) {
  if (!detail.value?.id) return
  favBusyId.value = productId
  try {
    const res = await api.delete(`/customers/${detail.value.id}/favorites/${productId}`)
    detail.value = { ...detail.value, favorites: res.data?.favorites || [] }
    ElMessage.success('已移除收藏')
  } catch (e: any) {
    ElMessage.error(e.message || '移除失败')
  } finally {
    favBusyId.value = null
  }
}

async function loadCouponTemplates() {
  if (couponTemplates.value.length) return
  try {
    const res = await api.get('/coupons/templates', { params: { include_disabled: true } })
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
  mergeSourceOptions.value = []
  mergeDialog.value = true
}

async function submitMerge() {
  const targetId = mergeTarget.value?.id
  const sourceId = Number(mergeSourceId.value)
  if (!targetId || !sourceId) {
    ElMessage.warning('请搜索并选择源顾客')
    return
  }
  if (sourceId === Number(targetId)) {
    ElMessage.warning('源与目标不能相同')
    return
  }
  const sourceRow = mergeSourceOptions.value.find((c) => c.id === sourceId)
  const sourceLabel = sourceRow?.display_name || sourceRow?.nickname || `#${sourceId}`
  try {
    await ElMessageBox.confirm(
      `确认将「${sourceLabel}」（ID ${sourceId}）合并入目标顾客 #${targetId}？此操作不可逆。`,
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
  await Promise.all([loadStores(), loadLevels()])
  await load()
})
</script>

<style scoped>
.muted {
  color: #999;
}

.bal-sub {
  font-size: 12px;
  margin-top: 2px;
  line-height: 1.3;
}

.tip {
  font-size: 13px;
  margin: 4px 0 10px;
}

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

.no-avatar {
  color: #c0c4cc;
  font-size: 12px;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
}

.detail-head-meta {
  min-width: 0;
}

.detail-name {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.3;
  word-break: break-word;
}

.pref-block {
  margin-top: 18px;
}

.pref-block h4 {
  margin: 0;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.section-head h4 {
  margin: 0;
}

.edit-form {
  margin-bottom: 8px;
}

.fav-add {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.hint {
  color: #999;
  font-size: 12px;
  margin-top: 4px;
}

.tip {
  font-size: 12px;
  margin-top: 4px;
  line-height: 1.4;
}

.birthday-alert {
  margin: 0 0 12px;
}
.birthday-live {
  margin-top: 8px;
  font-size: 13px;
  color: var(--el-color-primary);
  line-height: 1.5;
}

.acct {
  padding-left: 0;
  list-style: none;
  word-break: break-all;
}

.acct-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.acct-meta {
  min-width: 0;
}

.acct code {
  font-size: 12px;
}

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
