/**
 * 会员营销相关帮助文案（与 backend lifecycle_service 默认阈值对齐）。
 * 供顾客管理 / 客户分群 / 营销活动共用，避免各页说法不一致。
 */

/** 生命周期总说明 */
export const LIFECYCLE_OVERVIEW = [
  '「生命周期」描述顾客从注册到流失的阶段，由夜间批处理根据「距上次下单天数 / 入会时间」刷新到 customer_lifecycle 表。',
  '默认门槛（可在系统设置 lifecycle_thresholds_json 覆盖）：入会≤7天=新客；上次下单≤30天=活跃；≤90天=风险；≤120天=沉睡；更久=流失；非会员=游客。',
  '用途：顾客列表筛选、客户分群圈人（字段 lifecycle_code）、运营判断该发「唤醒券」还是「维护券」。',
].join('\n')

/** 各生命周期码说明（与 labels.CUSTOMER_LIFECYCLE 键一致） */
export const LIFECYCLE_CODE_HELP: Record<string, string> = {
  GUEST: '游客：尚未成为会员（member_status≠MEMBER）。一般不进发券分群。',
  NEW: '新客：已入会，且入会时间在「新客天数」内（默认 7 天）。适合欢迎券、首单引导。',
  ACTIVE: '活跃：距上次下单 ≤「活跃天数」（默认 30 天）。适合会员日、加购、推荐加权。',
  AT_RISK: '风险：距上次下单已超过活跃天数，但未到沉睡（默认 31–90 天）。优先发轻券召回，防止滑向沉睡。',
  DORMANT: '沉睡：更久未下单（默认 91–120 天），或会员但从无消费。适合沉睡唤醒券包。',
  LOST: '流失：沉默超过沉睡门槛（默认 >120 天）。需更大力度召回，或先估人数再决定是否投放。',
}

/** RFM 总说明 */
export const RFM_OVERVIEW = [
  '「RFM」是营销常用三维打分：R=Recency 近度（多久没来）、F=Frequency 频次（近 90 天单量）、M=Monetary 金额（近 90 天实付）。每维 1–5 分，再合成粗标签。',
  '默认分界（可在系统设置 rfm_thresholds_json 覆盖）：沉默天 7/30/60/90；单量 1/2/4/8；实付 30/80/150/300 元。夜批写入 customer_lifecycle。',
  '与「生命周期」区别：生命周期主要看「多久没下单」一条轴；RFM 同时看近度+频次+金额，更适合精细分群（冠军维护 vs 休眠唤醒）。',
  '用途：顾客列表筛选、客户分群字段 rfm_segment。筛选用作排查；批量发券请走「客户分群 → 营销活动」。',
].join('\n')

/** 各 RFM 标签说明（与 lifecycle_service.compute_rfm_segment 对齐） */
export const RFM_SEGMENT_HELP: Record<string, string> = {
  CHAMPIONS: '冠军：R/F/M 都高（约 ≥4）。核心贡献客，宜维护权益、轻券、推荐加权，忌过度砸券。',
  LOYAL: '忠诚：来得勤（R 高）且频次不错（F 较高）。适合会员日、储值提醒、加菜推荐。',
  NEW_PROMISING: '潜力：最近有来（R 高）但频次还低（F 低）。适合培养复购（第二单券、套餐引导）。',
  AT_RISK: '风险：来得少了（R 低）但曾经频次不低（F 高）。优先召回，防止变休眠。',
  HIBERNATING: '休眠：近度与频次都低。适合沉睡唤醒、节日券；先估人数控制成本。',
  POTENTIAL: '潜力客（综合）：三维平均分偏高但未落入上面几档。可试轻券或推荐加权观察转化。',
  OTHER: '其他：未落入明确档位。建议结合生命周期或自定义规则（如 days_since_order）再圈人。',
}

/** 分群规则字段帮助 */
export const RULE_FIELD_HELP: Record<string, string> = {
  member_status: '会员身份：MEMBER=已注册会员，GUEST=游客。发券分群通常先加「= MEMBER」。',
  days_since_order: '距上次下单天数：按 last_order_at 相对今天。≥30 常表示沉睡段；无消费视为「很久」。',
  days_since_member: '入会天数：按 member_activated_at。≤7 常用于新客欢迎。',
  points: '当前积分余额（customers.points）。',
  balance_total: '储值本金+赠金合计。常用于「低余额提醒」分群。',
  has_order: '是否下过单：true=有 last_order_at，false=从未下单。',
  lifecycle_code: '生命周期码（夜批）。取值见下方「生命周期」说明，如 AT_RISK、DORMANT。',
  rfm_segment: 'RFM 粗标签（夜批）。取值如 CHAMPIONS、HIBERNATING，见「RFM」说明。',
  silence_days: '沉默天数（夜批快照）：距上次下单天数；无单记为很大值。可与 days_since_order 二选一。',
  account_status: '账号状态：一般为 ACTIVE / DISABLED 等。',
  status: '启用标记：1=启用，0=停用。系统估人默认只算启用且 ACTIVE。',
  registered_store_id: '注册门店 ID。门店活动引用总部分群时，发布会按本店再过滤，通常不必手填。',
  social_identity_code:
    '社会贡献公益身份码（customer_social_identities）：仅统计 status=ACTIVE 且在 valid_from/valid_to 有效期内的认定。取值如 TEACHER、ELDER、VETERAN、SANITATION。教师节/重阳/八一等应与 MEMBER 组合圈人，避免无身份普发。',
  is_birthday_today:
    '今日是否生日（北京时间；支持公历/农历）。生日营销必须用此字段，切勿绑「全体会员」——否则一发布就会给无关人发券。',
}

export const SEGMENT_TYPE_HELP =
  '动态 DYNAMIC：按规则实时/发布时计算；静态 STATIC：以「物化」快照为准，人不变直到再次物化。总部预置分群仅超管可物化；门店发布时会按本店自动圈人。'

export const MATERIALIZE_HELP =
  '「物化」= 按当前规则把命中顾客写入分群成员快照，估人数也会更新。本轮营销想圈人稳定时，对本店自建分群先物化再发布；总部预置由超管物化，门店直接发布即可。'

/** 营销活动表单字段说明 */
export const CAMPAIGN_FIELD_HELP = {
  code: '业务键，同门店唯一。建议英文+日期，如 wakeup_sleep_20260419。',
  name: '店长可见的活动名称，会出现在列表与报表。',
  campaign_type: '仅作分类标签（会员日/节日/唤醒等），不影响发券引擎；发券仍靠「分群+券模板」。',
  segment_id: '圈人来源。总部预置分群可直接选；门店发布时按本店自动圈人。建议先在客户分群估人数。',
  coupon_template_id: '发布后按分群批量发放的券模板，须为启用状态。与手动发券共用同一套模板。',
  start_at: '开始时间。空=发布后立即「进行中」；若晚于当前时间则先「已排期」，到点自动开始。',
  end_at: '结束时间。到点活动变「已结束」，未发出的待执行任务会取消。空=不自动结束，需手动结束。',
  weekly_cap_override: '每人每周通过营销任务(mkt:)发券上限。空=用系统默认（常见为 3）；0=不限制。防止同一人被多活动刷券。',
  budget_cap: '活动预算上限（元）。空=不限制；仅作运营参考，当前发券引擎不会按此自动截停。',
  boost_product_ids: '活动「进行中」期间，这些餐品在小程序推荐里额外加权约 +25%，结束后自动失效。',
  remark: '内部备注，不对顾客展示。',
}

/** 营销报表 KPI 列说明 */
export const REPORT_KPI_HELP = {
  coupons_issued: '本活动幂等键发到顾客账户的券张数（含未用）。',
  coupons_used: '其中已核销（USED）张数。',
  redeem_rate: '核销率 = 核销 / 发券；发券为 0 时显示 —。',
  attributed_gross: '归因实收：用本活动券下单后记入营销归因台账的支付实收合计。',
  attributed_subsidy: '补贴：归因订单上的券面额/减免合计，近似营销成本。',
  roi: 'ROI = (归因实收 − 补贴) / 补贴；补贴为 0 时无法算，显示 —。正数表示收回成本有余。',
  tasks_success: '发券任务 SUCCESS 条数（与发券张数接近，但按任务计）。',
}

/** 折叠面板用条目 */
export function lifecycleHelpItems() {
  return Object.entries(LIFECYCLE_CODE_HELP).map(([code, text]) => ({ code, text }))
}

export function rfmHelpItems() {
  return Object.entries(RFM_SEGMENT_HELP).map(([code, text]) => ({ code, text }))
}
