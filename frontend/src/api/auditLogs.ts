import api from '@/api'

/** 单条操作日志 */
export interface AuditLogItem {
  id: number
  user_id: number | null
  username: string | null
  display_name: string | null
  action: string
  module: string
  category: string
  store_id: number | null
  target_type: string | null
  target_id: string | null
  ip: string | null
  user_agent: string | null
  request_id: string | null
  detail: unknown
  created_at: string
}

/** /audit-logs 响应数据 */
export interface AuditLogListData {
  items: AuditLogItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
  is_super_admin: boolean
}

/** 分类下拉选项 */
export interface AuditCategoryOption {
  value: string
  label: string
}

/** /audit-logs/categories 响应数据 */
export interface AuditCategoryData {
  items: AuditCategoryOption[]
  visible_values: string[]
}

/** /audit-logs 查询参数 */
export interface AuditLogListParams {
  page?: number
  page_size?: number
  store_id?: number
  category?: string
  module?: string
  action?: string
  user_id?: number
  target_type?: string
  target_id?: string
  keyword?: string
  date_from?: string
  date_to?: string
}

/** 查询操作日志列表 */
export function listAuditLogs(params: AuditLogListParams) {
  return api.get('/audit-logs', { params }) as Promise<{ data: AuditLogListData }>
}

/** 查询操作日志分类下拉（带可见性过滤） */
export function listAuditCategories() {
  return api.get('/audit-logs/categories') as Promise<{ data: AuditCategoryData }>
}
