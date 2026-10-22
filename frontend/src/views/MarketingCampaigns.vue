<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>营销活动</h2>
        <p class="sub">绑定分群 + 券模板后发布发券；可选推荐加权餐品与排期。状态流：草稿 → 发布 → 进行中 / 已排期 / 已暂停 → 结束。</p>
      </div>
      <div class="header-actions">
        <el-button @click="goSegments">客户分群</el-button>
        <el-button @click="goReport">营销报表</el-button>
        <el-button @click="goCoupons">优惠券模板</el-button>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManageCampaigns" type="primary" @click="openEdit()">新建活动</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tip"
      title="店长怎么用"
      description="① 客户分群估人 → ② 优惠券备模板 → ③ 选活动类型（或快捷模板）→ ④ 选分群与券 → ⑤ 可选排期 / 推荐加权 / 周频控 → ⑥ 发布 → ⑦ 营销报表看 ROI。门店引用总部预置分群时，发布会按本店自动圈人。推荐加权仅「进行中」约 +25%。"
    />
    <el-alert
      type="success"
      show-icon
      :closable="false"
      class="tip"
      title="分群里的「生命周期 / RFM」怎么理解？"
      description="生命周期=顾客阶段（新客/活跃/风险/沉睡/流失），主要看多久没来；RFM=近度+频次+金额三维标签（冠军/忠诚/休眠等）。两者都由夜批刷新。详细释义与取值请到「客户分群」页点开「生命周期 / RFM / 字段说明」，或「顾客管理」点「生命周期/RFM 是什么？」。"
    />

    <div v-if="canManageCampaigns" class="preset-bar">
      <span class="preset-label">快捷模板</span>
      <el-button
        v-for="p in CAMPAIGN_PRESETS"
        :key="p.codePrefix"
        size="small"
        plain
        @click="openEditFromPreset(p)"
      >{{ p.label }}</el-button>
    </div>

    <div class="filter-bar">
      <el-select
        v-if="auth.isSuperAdmin"
        v-model="filterStoreId"
        clearable
        placeholder="门店（空=全部/含总部）"
        style="width: 200px"
        @change="load"
      >
        <el-option
          v-for="s in storeOptions"
          :key="s.id"
          :label="s.name"
          :value="s.id"
        />
      </el-select>
      <el-tag v-else-if="boundStoreName" type="info">门店：{{ boundStoreName }}</el-tag>
      <el-select v-model="typeFilter" clearable placeholder="按类型筛选" style="width: 160px">
        <el-option v-for="t in CAMPAIGN_TYPES" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <el-select v-model="statusFilter" clearable placeholder="按状态筛选" style="width: 140px" @change="load">
        <el-option
          v-for="(lab, code) in MARKETING_CAMPAIGN_STATUS"
          :key="code"
          :label="lab"
          :value="code"
        />
      </el-select>
      <el-button link type="primary" @click="goStoreBiz">门店营销开关</el-button>
    </div>

    <el-alert
      v-if="marketingEnabled === false"
      type="error"
      show-icon
      :closable="false"
      class="tip"
      title="当前门店已关闭「营销活动」"
      description="不可新建/发布。请到 门店 → 门店信息 → 业务参数 → 会员与优惠 开启「营销活动」。关闭后门店推荐加权也不会生效。"
    />
    <el-alert
      v-else-if="marketingEnabled === true && filterStoreId"
      type="success"
      show-icon
      :closable="false"
      class="tip"
      title="本店营销活动已开启"
      description="可按门店建活动；总部活动（门店列为空）对开启营销的门店仍可共享推荐加权/发券任务。"
    />

    <el-empty v-if="!loading && !filteredItems.length" description="暂无活动；点「新建活动」或上方快捷模板开始" />
    <el-table v-else :data="filteredItems" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="门店" width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ storeLabel(row.store_id) }}</template>
      </el-table-column>
      <el-table-column prop="code" label="编码" width="140" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" min-width="130" show-overflow-tooltip />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">{{ typeLabel(row.campaign_type) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="statusTag(row.status)">
            {{ label(MARKETING_CAMPAIGN_STATUS, row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="排期" width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.schedule_mode === 'ANNUAL_MD'">
            每年 {{ row.annual_label || '—' }}
            <el-tag v-if="row.in_schedule_window" size="small" type="success" style="margin-left:4px">窗内</el-tag>
            <el-tag v-else size="small" type="info" style="margin-left:4px">窗外</el-tag>
          </span>
          <span v-else-if="row.start_at || row.end_at">{{ row.start_at || '—' }} ~ {{ row.end_at || '—' }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="预算" width="90" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.budget_cap != null && row.budget_cap !== '' ? row.budget_cap : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="分群" width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ segmentLabel(row.segment_id) }}</template>
      </el-table-column>
      <el-table-column label="券模板" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ couponLabel(row.coupon_template_id) }}</template>
      </el-table-column>
      <el-table-column label="推荐加权" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ boostProductsLabel(row.boost_product_ids) }}</template>
      </el-table-column>
      <el-table-column label="创建人" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ creatorLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="420" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="showDetail(row)">详情</el-button>
          <el-button v-if="row.status === 'DRAFT' && canEditRow(row)" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            v-if="row.status === 'DRAFT' || row.status === 'SCHEDULED'"
            size="small"
            type="primary"
            :disabled="!canPublish(row)"
            :title="publishBlockReason(row) || '发布并按分群入队发券'"
            @click="publish(row)"
          >发布</el-button>
          <el-button
            v-if="row.status === 'RUNNING' && canEditRow(row)"
            size="small"
            @click="setStatus(row, 'PAUSED')"
          >暂停</el-button>
          <el-button
            v-if="row.status === 'PAUSED' && canEditRow(row)"
            size="small"
            type="success"
            @click="setStatus(row, 'RUNNING')"
          >恢复</el-button>
          <el-button
            v-if="(row.status === 'RUNNING' || row.status === 'PAUSED' || row.status === 'SCHEDULED') && canEditRow(row)"
            size="small"
            type="warning"
            @click="setStatus(row, 'ENDED')"
          >结束</el-button>
          <el-button
            v-if="['DRAFT', 'SCHEDULED', 'RUNNING', 'PAUSED'].includes(row.status)"
            size="small"
            type="danger"
            plain
            @click="setStatus(row, 'CANCELLED')"
          >取消</el-button>
          <el-button size="small" @click="showTasks(row)">任务</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑活动' : '新建活动'" width="680px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        :title="typeHint(form.campaign_type)"
      />
      <el-form label-width="120px">
        <el-form-item v-if="auth.isSuperAdmin" label="归属门店">
          <el-select v-model="form.store_id" clearable placeholder="总部（全店共享）" style="width:100%" :disabled="!!form.id">
            <el-option
              v-for="s in storeOptions"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
          <div class="hint">空=总部活动；指定门店则仅该店。创建后不可改归属。</div>
        </el-form-item>
        <el-form-item v-else label="归属门店">
          <el-tag type="info">{{ boundStoreName || '本店' }}</el-tag>
        </el-form-item>
        <el-form-item label="编码">
          <el-input v-model="form.code" placeholder="如 festival_spring_2026" />
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.code }}</div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" />
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.name }}</div>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.campaign_type" style="width:100%" filterable>
            <el-option
              v-for="t in CAMPAIGN_TYPES"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
          </el-select>
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.campaign_type }}</div>
        </el-form-item>
        <el-form-item label="分群">
          <el-select v-model="form.segment_id" filterable clearable placeholder="选择分群" style="width:100%">
            <el-option
              v-for="s in segments"
              :key="s.id"
              :label="`${s.name} (#${s.id}${s.estimated_count != null ? ' · 约' + s.estimated_count + '人' : ''}${s.store_id == null ? ' · 总部' : ''})`"
              :value="s.id"
            />
          </el-select>
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.segment_id }}</div>
        </el-form-item>
        <el-form-item label="券模板">
          <el-select
            v-model="form.coupon_template_id"
            filterable
            clearable
            placeholder="选择启用中的券模板"
            style="width:100%"
          >
            <el-option
              v-for="t in couponTemplates"
              :key="t.id"
              :label="couponOptionLabel(t)"
              :value="t.id"
              :disabled="isCouponOptionDisabled(t)"
            />
          </el-select>
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.coupon_template_id }}</div>
        </el-form-item>
        <el-form-item label="排期方式">
          <el-radio-group v-model="form.schedule_mode">
            <el-radio-button value="ANNUAL_MD">每年月日（推荐节日）</el-radio-button>
            <el-radio-button value="ABSOLUTE">绝对起止时间</el-radio-button>
          </el-radio-group>
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.schedule_mode }}</div>
        </el-form-item>
        <template v-if="form.schedule_mode === 'ANNUAL_MD'">
          <el-form-item label="历法">
            <el-radio-group v-model="form.schedule_calendar">
              <el-radio-button value="SOLAR">公历</el-radio-button>
              <el-radio-button value="LUNAR">农历</el-radio-button>
            </el-radio-group>
            <div class="hint">{{ CAMPAIGN_FIELD_HELP.schedule_calendar }}</div>
          </el-form-item>
          <el-form-item label="起始月日">
            <div style="display:flex;gap:8px;align-items:center;width:100%">
              <el-input-number v-model="form.annual_start_month" :min="1" :max="12" controls-position="right" />
              <span>月</span>
              <el-input-number v-model="form.annual_start_day" :min="1" :max="31" controls-position="right" />
              <span>日</span>
            </div>
            <div class="hint">{{ CAMPAIGN_FIELD_HELP.annual_md }}</div>
          </el-form-item>
          <el-form-item label="结束月日">
            <div style="display:flex;gap:8px;align-items:center;width:100%">
              <el-input-number v-model="form.annual_end_month" :min="1" :max="12" controls-position="right" />
              <span>月</span>
              <el-input-number v-model="form.annual_end_day" :min="1" :max="31" controls-position="right" />
              <span>日</span>
            </div>
            <div class="hint">可跨年（如腊月廿四～正月初七）；空结束=仅起始当天。</div>
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="开始时间">
            <el-date-picker
              v-model="form.start_at"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="空=发布后立即进行"
              style="width:100%"
            />
            <div class="hint">{{ CAMPAIGN_FIELD_HELP.start_at }}</div>
          </el-form-item>
          <el-form-item label="结束时间">
            <el-date-picker
              v-model="form.end_at"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="空=不自动结束"
              style="width:100%"
            />
            <div class="hint">{{ CAMPAIGN_FIELD_HELP.end_at }}</div>
          </el-form-item>
        </template>
        <el-form-item label="周发券上限">
          <el-input-number v-model="form.weekly_cap_override" :min="0" :max="100" :controls="true" />
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.weekly_cap_override }}</div>
        </el-form-item>
        <el-form-item label="预算上限">
          <el-input-number
            v-model="form.budget_cap"
            :min="0"
            :precision="2"
            :step="100"
            :controls="true"
            placeholder="空=不限制"
          />
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.budget_cap }}</div>
        </el-form-item>
        <el-form-item label="推荐加权餐品">
          <el-select
            v-model="form.boost_product_ids"
            multiple
            filterable
            clearable
            collapse-tags
            collapse-tags-tooltip
            placeholder="搜索并选择餐品（可多选）"
            style="width:100%"
            :loading="productsLoading"
          >
            <el-option
              v-for="p in productOptions"
              :key="p.id"
              :label="productOptionLabel(p)"
              :value="p.id"
            />
          </el-select>
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.boost_product_ids }}</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
          <div class="hint">{{ CAMPAIGN_FIELD_HELP.remark }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="taskDlg" :title="`任务监控 #${taskCampaignId}`" width="760px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
        title="发券任务按幂等键去重。失败任务可点「重试失败任务」重置后由后台再发。"
      />
      <p v-if="taskStats">
        汇总：
        <span v-for="(v, k) in taskStats" :key="k" style="margin-right: 12px">{{ label(MARKETING_TASK_STATUS, String(k), String(k)) }}={{ v }}</span>
      </p>
      <el-button size="small" @click="retryFailed" style="margin-bottom: 8px">重试失败任务</el-button>
      <el-empty v-if="!tasks.length" description="暂无任务（未发布或尚未入队）" />
      <el-table v-else :data="tasks" size="small" stripe max-height="360">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="customer_id" label="顾客" width="90" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">{{ label(MARKETING_TASK_STATUS, row.status) }}</template>
        </el-table-column>
        <el-table-column prop="try_count" label="尝试" width="70" />
        <el-table-column prop="idempotency_key" label="幂等键" min-width="180" show-overflow-tooltip />
        <el-table-column prop="last_error" label="错误" min-width="140" show-overflow-tooltip />
      </el-table>
    </el-dialog>

    <el-drawer
      v-model="detailDlg"
      :title="detailRow ? `活动详情 · ${detailRow.name}` : '活动详情'"
      size="560px"
      destroy-on-close
    >
      <template v-if="detailRow">
        <h4 class="detail-section">基础</h4>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="ID">{{ detailRow.id }}</el-descriptions-item>
          <el-descriptions-item label="编码">{{ detailRow.code || '—' }}</el-descriptions-item>
          <el-descriptions-item label="名称">{{ detailRow.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ typeLabel(detailRow.campaign_type) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="statusTag(detailRow.status)">
              {{ label(MARKETING_CAMPAIGN_STATUS, detailRow.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="门店">{{ storeLabel(detailRow.store_id) }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-section">投放</h4>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="分群">{{ segmentLabel(detailRow.segment_id) }}</el-descriptions-item>
          <el-descriptions-item label="券模板">{{ couponLabel(detailRow.coupon_template_id) }}</el-descriptions-item>
          <el-descriptions-item label="排期">
            <span v-if="detailRow.start_at || detailRow.end_at">
              {{ detailRow.start_at || '—' }} ~ {{ detailRow.end_at || '—' }}
            </span>
            <span v-else>—</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-section">规则</h4>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="预算上限">
            {{ detailRow.budget_cap != null && detailRow.budget_cap !== '' ? `${detailRow.budget_cap} 元` : '不限制' }}
          </el-descriptions-item>
          <el-descriptions-item label="周发券上限">
            {{
              detailRow.weekly_cap_override == null
                ? '系统默认'
                : detailRow.weekly_cap_override === 0
                  ? '不限制'
                  : detailRow.weekly_cap_override
            }}
          </el-descriptions-item>
          <el-descriptions-item label="推荐加权">{{ boostProductsLabel(detailRow.boost_product_ids) }}</el-descriptions-item>
          <el-descriptions-item label="备注">{{ detailRow.remark || '—' }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-section">元数据</h4>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="创建人">{{ creatorLabel(detailRow) }}</el-descriptions-item>
          <el-descriptions-item label="发布时间">{{ detailRow.published_at || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ detailRow.created_at || '—' }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ detailRow.updated_at || '—' }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-section">任务汇总</h4>
        <div v-loading="detailStatsLoading" class="detail-stats">
          <template v-if="detailTaskStats && Object.keys(detailTaskStats).length">
            <el-tag
              v-for="(v, k) in detailTaskStats"
              :key="k"
              size="small"
              class="detail-stat-tag"
            >{{ label(MARKETING_TASK_STATUS, String(k), String(k)) }}={{ v }}</el-tag>
          </template>
          <span v-else-if="!detailStatsLoading" class="hint">暂无任务（未发布或尚未入队）</span>
        </div>
        <el-button type="primary" plain style="margin-top: 12px" @click="openTasksFromDetail">打开任务监控</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useStoreOptions } from '@/composables/useStoreOptions'
import { MARKETING_CAMPAIGN_STATUS, MARKETING_TASK_STATUS, label } from '@/utils/labels'
import { CAMPAIGN_FIELD_HELP } from '@/utils/marketingHelp'
import { notifySaveWithOverride, pickOverrideWarning } from '@/utils/overrideWarning'

/** 活动类型（与实施方案日历节点对齐；后端存大写字符串） */
const CAMPAIGN_TYPES = [
  { value: 'MEMBER_DAY', label: '会员日', hint: '老客回访轻券，适合会员日/店庆。' },
  { value: 'FESTIVAL', label: '节日', hint: '春节、妇女节、儿童节、重阳、教师节等节日发券。' },
  { value: 'TIME_SLOT', label: '时段', hint: '冬至/下午茶等时段套餐或促销券。' },
  { value: 'SOCIAL', label: '公益营销', hint: '如每月环卫爱心日发豆浆券；与「社会贡献」身份折扣不同。' },
  { value: 'BIRTHDAY', label: '生日', hint: '生日权益加码发券（可与门店生日参数配合）。' },
  { value: 'MEMBER_ANNIVERSARY', label: '入会周年', hint: '未填生日的会员，按入会日公历月日发周年礼；勿与生日混用。' },
  { value: 'NEW_MEMBER', label: '新客', hint: '新注册/开卡欢迎券包。' },
  { value: 'WAKEUP', label: '唤醒', hint: '沉睡/流失客召回发券。' },
  { value: 'EXAM', label: '考生', hint: '高考/中考加油券，可联动公益身份。' },
  { value: 'RECO_BOOST', label: '推荐加权', hint: '以推荐加权为主；仍可附带发券。' },
] as const

type CampaignPreset = {
  label: string
  campaign_type: string
  codePrefix: string
  name: string
  remark: string
  schedule_mode?: 'ABSOLUTE' | 'ANNUAL_MD'
  schedule_calendar?: 'SOLAR' | 'LUNAR'
  annual_start_month?: number
  annual_start_day?: number
  annual_end_month?: number
  annual_end_day?: number
}

const CAMPAIGN_PRESETS: CampaignPreset[] = [
  { label: '春节返乡', campaign_type: 'FESTIVAL', codePrefix: 'festival_spring', name: '春节·回家吃碗家乡面', remark: '农历每年自动启停；满20减3；加权肠旺/豌杂', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'LUNAR', annual_start_month: 12, annual_start_day: 24, annual_end_month: 1, annual_end_day: 7 },
  { label: '元宵', campaign_type: 'FESTIVAL', codePrefix: 'festival_lantern', name: '元宵·汤圆团圆', remark: '农历正月十五前后；汤圆加权', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'LUNAR', annual_start_month: 1, annual_start_day: 14, annual_end_month: 1, annual_end_day: 16 },
  { label: '妇女节', campaign_type: 'FESTIVAL', codePrefix: 'festival_mother', name: '妇女节·有温度的甜', remark: '公历3/8前后；满20减3+汤圆', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 3, annual_start_day: 6, annual_end_month: 3, annual_end_day: 9 },
  { label: '儿童节', campaign_type: 'FESTIVAL', codePrefix: 'festival_children', name: '儿童节·小份快乐', remark: '公历6/1前后；抄手/香葱包', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 5, annual_start_day: 30, annual_end_month: 6, annual_end_day: 2 },
  { label: '端午', campaign_type: 'FESTIVAL', codePrefix: 'festival_duanwu', name: '端午·咸鲜一碗', remark: '农历五月初五前后；肠旺/抄手', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'LUNAR', annual_start_month: 5, annual_start_day: 4, annual_end_month: 5, annual_end_day: 6 },
  { label: '中秋', campaign_type: 'FESTIVAL', codePrefix: 'festival_mid_autumn', name: '中秋·团圆甜一口', remark: '农历八月十五前后；汤圆', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'LUNAR', annual_start_month: 8, annual_start_day: 14, annual_end_month: 8, annual_end_day: 16 },
  { label: '国庆', campaign_type: 'FESTIVAL', codePrefix: 'festival_national', name: '国庆·家乡面畅吃', remark: '公历10/1～7；满20减3', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 10, annual_start_day: 1, annual_end_month: 10, annual_end_day: 7 },
  { label: '教师节', campaign_type: 'FESTIVAL', codePrefix: 'festival_teacher', name: '教师节·感念师恩', remark: '公历9/10；social_teacher', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 9, annual_start_day: 9, annual_end_month: 9, annual_end_day: 11 },
  { label: '重阳', campaign_type: 'FESTIVAL', codePrefix: 'festival_chongyang', name: '重阳·长寿面', remark: '农历九月初九；social_elder', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'LUNAR', annual_start_month: 9, annual_start_day: 8, annual_end_month: 9, annual_end_day: 10 },
  { label: '八一建军节', campaign_type: 'FESTIVAL', codePrefix: 'festival_army_day', name: '八一建军节·致敬军人', remark: '公历8/1；social_veteran', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 7, annual_start_day: 31, annual_end_month: 8, annual_end_day: 2 },
  { label: '冬至抄手', campaign_type: 'TIME_SLOT', codePrefix: 'timeslot_dongzhi', name: '冬至·饺/抄手节', remark: '公历约12/21～23；抄手加权', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 12, annual_start_day: 21, annual_end_month: 12, annual_end_day: 23 },
  { label: '环卫爱心日', campaign_type: 'SOCIAL', codePrefix: 'social_sanitation_day', name: '环卫爱心日·豆浆券', remark: '公历10/26；social_sanitation', schedule_mode: 'ANNUAL_MD', schedule_calendar: 'SOLAR', annual_start_month: 10, annual_start_day: 25, annual_end_month: 10, annual_end_day: 27 },
  { label: '会员日', campaign_type: 'MEMBER_DAY', codePrefix: 'member_day', name: '会员日·老客回访', remark: 'champions+满20减3；可用绝对时间', schedule_mode: 'ABSOLUTE' },
  { label: '生日加蛋', campaign_type: 'BIRTHDAY', codePrefix: 'birthday_egg', name: '生日·免费加蛋', remark: '必须 birthday_today；保持 RUNNING，调度每日补圈；同年不重复次年可发', schedule_mode: 'ABSOLUTE' },
  { label: '入会周年礼', campaign_type: 'MEMBER_ANNIVERSARY', codePrefix: 'member_anniversary', name: '入会周年·感谢陪伴', remark: '必须 member_anniversary_today（无生日）；保持 RUNNING，调度每日补圈；同年不重复', schedule_mode: 'ABSOLUTE' },
  { label: '沉睡唤醒', campaign_type: 'WAKEUP', codePrefix: 'wakeup_sleep', name: '沉睡唤醒', remark: 'sleep30/90；周频1', schedule_mode: 'ABSOLUTE' },
  { label: '新客欢迎', campaign_type: 'NEW_MEMBER', codePrefix: 'new_member', name: '新客欢迎券', remark: '开卡欢迎；周频1', schedule_mode: 'ABSOLUTE' },
  { label: '低余额催充', campaign_type: 'MEMBER_DAY', codePrefix: 'low_balance', name: '低余额催充', remark: '满20减3催回店', schedule_mode: 'ABSOLUTE' },
]

const router = useRouter()
const auth = useAuthStore()
const { storeOptions, loadStores } = useStoreOptions()
const loading = ref(false)
const saving = ref(false)
const productsLoading = ref(false)
const items = ref<any[]>([])
const segments = ref<any[]>([])
const couponTemplates = ref<any[]>([])
const productOptions = ref<any[]>([])
const typeFilter = ref<string | undefined>()
const statusFilter = ref<string | undefined>()
/** 超管筛选门店；本店账号固定为本店 */
const filterStoreId = ref<number | undefined>()
const marketingEnabled = ref<boolean | null>(null)
const dlg = ref(false)
const taskDlg = ref(false)
const detailDlg = ref(false)
const detailRow = ref<any | null>(null)
const detailTaskStats = ref<Record<string, number> | null>(null)
const detailStatsLoading = ref(false)
const tasks = ref<any[]>([])
const taskStats = ref<Record<string, number> | null>(null)
const taskCampaignId = ref<number | null>(null)
const form = reactive<any>({
  id: null,
  store_id: null as number | null,
  code: '',
  name: '',
  campaign_type: 'MEMBER_DAY',
  segment_id: null,
  coupon_template_id: null,
  schedule_mode: 'ANNUAL_MD' as string,
  schedule_calendar: 'SOLAR' as string,
  annual_start_month: null as number | null,
  annual_start_day: null as number | null,
  annual_end_month: null as number | null,
  annual_end_day: null as number | null,
  start_at: null as string | null,
  end_at: null as string | null,
  weekly_cap_override: null as number | null,
  budget_cap: null as number | null,
  boost_product_ids: [] as number[],
  remark: '',
})

function blankScheduleFields() {
  return {
    schedule_mode: 'ANNUAL_MD',
    schedule_calendar: 'SOLAR',
    annual_start_month: null as number | null,
    annual_start_day: null as number | null,
    annual_end_month: null as number | null,
    annual_end_day: null as number | null,
    start_at: null as string | null,
    end_at: null as string | null,
  }
}

const boundStoreName = computed(() => {
  if (auth.isSuperAdmin) return ''
  const sid = auth.user?.store_id
  const s = storeOptions.value.find((x) => x.id === sid)
  return s?.name || auth.user?.store_name || (sid ? `门店#${sid}` : '')
})

const filteredItems = computed(() => {
  if (!typeFilter.value) return items.value
  return items.value.filter((x) => x.campaign_type === typeFilter.value)
})

function effectiveStoreId(): number | null | undefined {
  if (auth.isSuperAdmin) return filterStoreId.value ?? undefined
  return auth.user?.store_id ?? undefined
}

const canManageCampaigns = computed(
  () => auth.isSuperAdmin || auth.hasPermission('coupon:manage'),
)

function canEditRow(row: any) {
  if (!canManageCampaigns.value) return false
  if (auth.isSuperAdmin) return true
  return row.store_id != null && row.store_id === auth.user?.store_id
}

function goSegments() {
  router.push('/marketing/segments')
}
function goReport() {
  router.push('/marketing/report')
}
function goCoupons() {
  router.push('/coupons')
}
function goStoreBiz() {
  const sid = effectiveStoreId() || storeOptions.value[0]?.id
  router.push(sid ? { path: '/store-info', query: { store_id: String(sid), tab: 'biz' } } : '/store-info')
}

function storeLabel(id: number | null | undefined) {
  if (id == null) return '总部'
  const s = storeOptions.value.find((x) => x.id === id)
  return s?.name || `#${id}`
}

function typeLabel(code: string | null | undefined) {
  const t = CAMPAIGN_TYPES.find((x) => x.value === code)
  return t?.label || code || '—'
}

function typeHint(code: string | null | undefined) {
  const t = CAMPAIGN_TYPES.find((x) => x.value === code)
  return t?.hint || '草稿可反复改；发布后按分群圈人批量发券。'
}

function statusTag(status: string) {
  if (status === 'RUNNING') return 'success'
  if (status === 'PAUSED' || status === 'SCHEDULED') return 'warning'
  if (status === 'ENDED' || status === 'CANCELLED') return 'info'
  return ''
}

function segmentLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const s = segments.value.find((x) => x.id === id)
  return s ? `${s.name} (#${id})` : `#${id}`
}

function couponLabel(id: number | null | undefined) {
  if (id == null) return '—'
  const t = couponTemplates.value.find((x) => x.id === id)
  return t ? `${t.name} (#${id})` : `#${id}`
}

/** 名称以「新客」开头的欢迎券，仅允许活动类型 NEW_MEMBER */
function isWelcomeOnlyCoupon(t: { name?: string } | null | undefined) {
  return Boolean((t?.name || '').trim().startsWith('新客'))
}

function couponOptionLabel(t: any) {
  const base = `${t.name} (#${t.id} · ${t.coupon_type || ''})`
  if (t.schedule_mode === 'ANNUAL_MD' && t.in_issue_window === false) {
    return `${base} · 窗外停发`
  }
  if (t.annual_label) return `${base} · ${t.annual_label}`
  return base
}

function isCouponOptionDisabled(t: any) {
  if (isWelcomeOnlyCoupon(t) && form.campaign_type !== 'NEW_MEMBER') return true
  // 绑节日券到活动仍允许（来年可发）；仅提示窗外。手动发券侧已禁用。
  return false
}

watch(
  () => form.campaign_type,
  (ctype) => {
    if (ctype === 'NEW_MEMBER' || form.coupon_template_id == null) return
    const t = couponTemplates.value.find((x) => x.id === form.coupon_template_id)
    if (isWelcomeOnlyCoupon(t)) form.coupon_template_id = null
  },
)

function productOptionLabel(p: any) {
  const price = p.price != null ? ` ¥${p.price}` : ''
  return `${p.name}${price} (#${p.id})`
}

function boostProductsLabel(ids: unknown) {
  if (!Array.isArray(ids) || !ids.length) return '—'
  return ids
    .map((id) => {
      const p = productOptions.value.find((x) => x.id === id)
      return p ? p.name : `#${id}`
    })
    .join('、')
}

function creatorLabel(row: any) {
  if (!row) return '—'
  const name = (row.created_by_name || '').trim()
  if (name && row.created_by != null) return `${name} (#${row.created_by})`
  if (name) return name
  if (row.created_by != null) return `#${row.created_by}`
  return '—'
}

function normalizeBoostIds(ids: unknown): number[] {
  if (!Array.isArray(ids)) return []
  return ids.map((n) => Number(n)).filter((n) => Number.isInteger(n) && n > 0)
}

function normalizeBudgetCap(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function todayTag() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
}

async function loadProducts() {
  productsLoading.value = true
  try {
    const storeId = form.store_id || effectiveStoreId() || storeOptions.value[0]?.id
    const all: any[] = []
    let page = 1
    let totalPages = 1
    do {
      const res: any = await api.get('/products', {
        params: {
          page,
          page_size: 200,
          status: 'ON_SALE',
          ...(storeId ? { store_id: storeId } : {}),
        },
      })
      const data = res?.data || {}
      all.push(...(data.items || []))
      totalPages = Math.max(1, Number(data.total_pages || 1))
      page += 1
    } while (page <= totalPages && page <= 30)
    productOptions.value = all
  } catch {
    productOptions.value = []
  } finally {
    productsLoading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    await loadStores()
    if (!auth.isSuperAdmin && auth.user?.store_id) {
      filterStoreId.value = auth.user.store_id
    }
    const sid = effectiveStoreId()
    const [c, s, t] = await Promise.all([
      api.get('/marketing/campaigns', {
        params: {
          page_size: 100,
          ...(sid ? { store_id: sid } : {}),
          ...(statusFilter.value ? { status: statusFilter.value } : {}),
        },
      }),
      api.get('/marketing/segments', {
        params: { page_size: 100, ...(sid ? { store_id: sid } : {}) },
      }),
      api.get('/coupons/templates', { params: { include_disabled: false } }),
      loadProducts(),
    ])
    items.value = c.data?.items || []
    marketingEnabled.value =
      typeof c.data?.marketing_enabled === 'boolean' ? c.data.marketing_enabled : null
    segments.value = s.data?.items || []
    const raw = t.data?.items || t.data || []
    couponTemplates.value = Array.isArray(raw) ? raw : []
  } catch (e: any) {
    ElMessage.error(e?.message || '加载活动失败')
  } finally {
    loading.value = false
  }
}

async function ensureBoostProductsInOptions(ids: number[]) {
  for (const id of ids) {
    if (productOptions.value.some((p) => p.id === id)) continue
    try {
      const res = await api.get(`/products/${id}`)
      if (res.data?.id) productOptions.value.push(res.data)
    } catch {
      productOptions.value.push({ id, name: `餐品#${id}` })
    }
  }
}

async function openEdit(row?: any) {
  if (!canManageCampaigns.value) {
    ElMessage.warning('缺少权限：coupon:manage（仅可查看）')
    return
  }
  if (marketingEnabled.value === false && !row) {
    ElMessage.warning('当前门店已关闭营销活动，请先到门店业务参数开启')
    return
  }
  if (row && !canEditRow(row)) {
    ElMessage.warning('总部活动仅超管可编辑')
    return
  }
  if (row) {
    const boost = normalizeBoostIds(row.boost_product_ids)
    Object.assign(form, {
      id: row.id,
      store_id: row.store_id ?? null,
      code: row.code,
      name: row.name,
      campaign_type: row.campaign_type || 'MEMBER_DAY',
      segment_id: row.segment_id,
      coupon_template_id: row.coupon_template_id,
      schedule_mode: row.schedule_mode || 'ABSOLUTE',
      schedule_calendar: row.schedule_calendar || 'SOLAR',
      annual_start_month: row.annual_start_month ?? null,
      annual_start_day: row.annual_start_day ?? null,
      annual_end_month: row.annual_end_month ?? null,
      annual_end_day: row.annual_end_day ?? null,
      start_at: row.start_at || null,
      end_at: row.end_at || null,
      weekly_cap_override: row.weekly_cap_override ?? null,
      budget_cap: normalizeBudgetCap(row.budget_cap),
      boost_product_ids: boost,
      remark: row.remark || '',
    })
    await loadProducts()
    await ensureBoostProductsInOptions(boost)
  } else {
    const defaultStore = auth.isSuperAdmin
      ? (filterStoreId.value ?? null)
      : (auth.user?.store_id ?? null)
    Object.assign(form, {
      id: null,
      store_id: defaultStore,
      code: '',
      name: '',
      campaign_type: 'MEMBER_DAY',
      segment_id: null,
      coupon_template_id: null,
      ...blankScheduleFields(),
      weekly_cap_override: null,
      budget_cap: null,
      boost_product_ids: [],
      remark: '',
    })
    await loadProducts()
  }
  dlg.value = true
}

async function openEditFromPreset(p: CampaignPreset) {
  if (!canManageCampaigns.value) {
    ElMessage.warning('缺少权限：coupon:manage')
    return
  }
  if (marketingEnabled.value === false) {
    ElMessage.warning('当前门店已关闭营销活动，请先到门店业务参数开启')
    return
  }
  const defaultStore = auth.isSuperAdmin
    ? (filterStoreId.value ?? null)
    : (auth.user?.store_id ?? null)
  const mode = p.schedule_mode || 'ANNUAL_MD'
  Object.assign(form, {
    id: null,
    store_id: defaultStore,
    code: mode === 'ANNUAL_MD' ? p.codePrefix : `${p.codePrefix}_${todayTag()}`,
    name: p.name,
    campaign_type: p.campaign_type,
    segment_id: null,
    coupon_template_id: null,
    schedule_mode: mode,
    schedule_calendar: p.schedule_calendar || 'SOLAR',
    annual_start_month: p.annual_start_month ?? null,
    annual_start_day: p.annual_start_day ?? null,
    annual_end_month: p.annual_end_month ?? null,
    annual_end_day: p.annual_end_day ?? null,
    start_at: null,
    end_at: null,
    weekly_cap_override: null,
    budget_cap: null,
    boost_product_ids: [],
    remark: p.remark,
  })
  await loadProducts()
  dlg.value = true
}

async function save() {
  if (!form.code?.trim() || !form.name?.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  if (!auth.isSuperAdmin && !auth.user?.store_id) {
    ElMessage.warning('账号未绑定门店')
    return
  }
  if (form.schedule_mode === 'ANNUAL_MD') {
    if (!form.annual_start_month || !form.annual_start_day) {
      ElMessage.warning('每年月日排期请填写起始月日')
      return
    }
  } else if (form.start_at && form.end_at && form.start_at >= form.end_at) {
    ElMessage.warning('结束时间须晚于开始时间')
    return
  }
  const boost = normalizeBoostIds(form.boost_product_ids)
  saving.value = true
  try {
    const annual = form.schedule_mode === 'ANNUAL_MD'
    const body = {
      code: form.code.trim(),
      name: form.name.trim(),
      campaign_type: form.campaign_type,
      segment_id: form.segment_id,
      coupon_template_id: form.coupon_template_id,
      schedule_mode: form.schedule_mode || 'ABSOLUTE',
      schedule_calendar: form.schedule_calendar || 'SOLAR',
      annual_start_month: annual ? form.annual_start_month : null,
      annual_start_day: annual ? form.annual_start_day : null,
      annual_end_month: annual ? form.annual_end_month : null,
      annual_end_day: annual ? form.annual_end_day : null,
      start_at: annual ? null : (form.start_at || null),
      end_at: annual ? null : (form.end_at || null),
      weekly_cap_override:
        form.weekly_cap_override === null || form.weekly_cap_override === undefined
          ? null
          : Number(form.weekly_cap_override),
      budget_cap: normalizeBudgetCap(form.budget_cap),
      boost_product_ids: boost.length ? boost : null,
      remark: form.remark || null,
      store_id: auth.isSuperAdmin ? (form.store_id || null) : (auth.user?.store_id || null),
    }
    const res = form.id
      ? await api.put(`/marketing/campaigns/${form.id}`, body)
      : await api.post('/marketing/campaigns', body)
    notifySaveWithOverride(res)
    dlg.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function segmentCodeOf(row: any): string {
  const id = row?.segment_id
  if (id != null) {
    const s = segments.value.find((x) => Number(x.id) === Number(id))
    if (s?.code) return String(s.code).trim()
  }
  return String(row?.segment_code || '').trim()
}

function publishBlockReason(row: any): string {
  if (!row) return '活动无效'
  if (!canManageCampaigns.value) return '缺少权限：coupon:manage'
  if (!canEditRow(row)) return '仅可管理本店活动'
  if (!row.segment_id) return '请先编辑并选择分群'
  if (!row.coupon_template_id) return '请先编辑并选择发券模板'
  const segCode = segmentCodeOf(row)
  const ctype = String(row.campaign_type || '').toUpperCase()
  const mode = String(row.schedule_mode || 'ABSOLUTE').toUpperCase()
  if (ctype === 'BIRTHDAY' && segCode === 'all_members') {
    return '生日活动禁止绑全体会员，请改用 birthday_today'
  }
  if (ctype === 'MEMBER_ANNIVERSARY' && segCode === 'all_members') {
    return '入会周年活动禁止绑全体会员，请改用 member_anniversary_today'
  }
  if (mode === 'ANNUAL_MD') {
    if (!row.annual_start_month || !row.annual_start_day) {
      return '每年月日排期须填写起始月日'
    }
    return ''
  }
  if (segCode === 'all_members' && (!row.start_at || !row.end_at)) {
    return '全体会员须设绝对起止，或改为「每年月日」排期'
  }
  return ''
}

function canPublish(row: any) {
  return !publishBlockReason(row)
}

async function publish(row: any) {
  const block = publishBlockReason(row)
  if (block) {
    ElMessage.warning(block)
    return
  }
  try {
    await ElMessageBox.confirm(
      `发布活动「${row.name}」并按分群生成发券任务？请确认分群与券模板可用。门店引用总部预置分群时将按本店自动圈人。`,
      '确认发布',
    )
    const res = await api.post(`/marketing/campaigns/${row.id}/publish`)
    const mode = res.data?.materialized?.mode
    const tip = mode === 'dynamic_store' ? '（按本店动态圈人）' : ''
    ElMessage.success(`已入队 ${res.data?.enqueued ?? 0} 条任务${tip}`)
    const warn = pickOverrideWarning(res)
    if (warn) ElMessage.warning(warn)
    await load()
  } catch (e: any) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(e?.message || '发布失败')
  }
}

async function setStatus(row: any, status: string) {
  const lab = label(MARKETING_CAMPAIGN_STATUS, status, status)
  try {
    if (status === 'ENDED' || status === 'CANCELLED') {
      await ElMessageBox.confirm(
        status === 'ENDED'
          ? `结束活动「${row.name}」？未发完的待执行任务将被取消。`
          : `取消活动「${row.name}」？未发完的待执行任务将被取消。`,
        '确认',
        { type: 'warning' },
      )
    }
    await api.post(`/marketing/campaigns/${row.id}/status`, { status })
    ElMessage.success(`已切换为「${lab}」`)
    await load()
  } catch (e: any) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(e?.message || '状态切换失败')
  }
}

async function showDetail(row: any) {
  detailRow.value = row
  detailTaskStats.value = null
  detailDlg.value = true
  const boost = normalizeBoostIds(row.boost_product_ids)
  if (boost.length) await ensureBoostProductsInOptions(boost)
  detailStatsLoading.value = true
  try {
    const res = await api.get(`/marketing/campaigns/${row.id}/tasks`, {
      params: { page: 1, page_size: 1 },
    })
    detailTaskStats.value = res.data?.task_stats || null
  } catch (e: any) {
    detailTaskStats.value = null
    ElMessage.error(e?.message || '加载任务汇总失败')
  } finally {
    detailStatsLoading.value = false
  }
}

function openTasksFromDetail() {
  if (!detailRow.value) return
  detailDlg.value = false
  showTasks(detailRow.value)
}

async function showTasks(row: any) {
  try {
    taskCampaignId.value = row.id
    const res = await api.get(`/marketing/campaigns/${row.id}/tasks`, { params: { page_size: 50 } })
    tasks.value = res.data?.items || []
    taskStats.value = res.data?.task_stats || null
    taskDlg.value = true
  } catch (e: any) {
    ElMessage.error(e?.message || '加载任务失败')
  }
}

async function retryFailed() {
  if (!taskCampaignId.value) return
  try {
    const res = await api.post(`/marketing/campaigns/${taskCampaignId.value}/tasks/retry-failed`)
    ElMessage.success(`已重置 ${res.data?.reset ?? 0} 条`)
    await showTasks({ id: taskCampaignId.value })
  } catch (e: any) {
    ElMessage.error(e?.message || '重试失败')
  }
}

onMounted(load)
</script>

<style scoped>
.page { padding: 8px; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 18px; }
.sub { margin: 0; font-size: 13px; color: #888; line-height: 1.5; max-width: 640px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }
.tip { margin-bottom: 10px; }
.preset-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.preset-label {
  font-size: 13px;
  color: #666;
  margin-right: 4px;
}
.filter-bar {
  margin-bottom: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.hint { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
.detail-section {
  margin: 16px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.detail-section:first-child { margin-top: 0; }
.detail-stats { min-height: 28px; }
.detail-stat-tag { margin: 0 8px 8px 0; }
</style>
