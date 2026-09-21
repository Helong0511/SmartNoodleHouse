import axios from 'axios'
import { ElMessage } from 'element-plus'
import { clearStaffToken, getStaffToken } from '@/utils/staffAuth'
import { clearCustomerToken, getCustomerToken } from '@/utils/customerAuth'
import { reportApiLog } from '@/utils/logger'

/** 带业务码的 API 错误，便于按 code 分支（如 40903 幂等键已结束） */
export class ApiError extends Error {
  code?: number
  status?: number

  constructor(message: string, opts?: { code?: number; status?: number }) {
    super(message)
    this.name = 'ApiError'
    this.code = opts?.code
    this.status = opts?.status
  }
}

function parseApiError(err: unknown): string {
  if (!err || typeof err !== 'object') return '网络错误'
  const ax = err as { response?: { data?: any; status?: number }; message?: string }
  const data = ax.response?.data
  if (data?.message && typeof data.message === 'string') return data.message
  if (Array.isArray(data?.detail)) {
    return data.detail.map((d: any) => d.msg || JSON.stringify(d)).join('；')
  }
  if (typeof data?.detail === 'string') return data.detail
  if (ax.response?.status === 422) return '请求参数有误，请检查表单'
  if (ax.message) return ax.message
  return '网络错误'
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const url = config.url || ''
  // H5/会员接口优先顾客 token；后台接口优先员工 token
  const isCustomerApi =
    url.includes('/miniprogram') ||
    url.startsWith('/customers/') ||
    url.includes('/orders/mine') ||
    /\/orders\/\d+\/reorder/.test(url) ||
    url.includes('/welcome/face') ||
    (config.headers?.['X-Client'] === 'customer')
  const staffToken = getStaffToken()
  const customerToken = getCustomerToken()
  // 顾客订单列表/复购强制用顾客 token，避免后台登录态抢占
  const forceCustomer =
    url.includes('/orders/mine') || /\/orders\/\d+\/reorder/.test(url) || url.includes('/miniprogram')
  const token = forceCustomer
    ? customerToken
    : isCustomerApi
      ? customerToken || staffToken
      : staffToken || customerToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => {
    const data = res.data
    const url = String(res.config?.url || '')
    if (data && data.code !== 0 && data.code !== undefined) {
      reportApiLog('WARN', res.config?.method || 'GET', url, { code: data.code, message: data.message })
      return Promise.reject(
        new ApiError(data.message || '请求失败', { code: Number(data.code), status: res.status }),
      )
    }
    return data
  },
  (err) => {
    const status = err.response?.status
    const msg = parseApiError(err)
    const bizCode = err.response?.data?.code
    const url = String(err.config?.url || '')
    reportApiLog('ERROR', err.config?.method || 'GET', url, { status, message: msg, code: bizCode })
    if (status === 401) {
      // P1: 401 时区分顾客/员工 API，清理对应 token，避免残留失效态
      const reqUrl = String(err.config?.url || '')
      const isCustApi =
        reqUrl.includes('/miniprogram') ||
        reqUrl.startsWith('/customers/') ||
        reqUrl.includes('/orders/mine') ||
        /\/orders\/\d+\/reorder/.test(reqUrl) ||
        reqUrl.includes('/welcome/face') ||
        (err.config?.headers?.['X-Client'] === 'customer')
      if (isCustApi) {
        clearCustomerToken()
        if (!location.pathname.startsWith('/scan') && !location.pathname.startsWith('/order') && !location.pathname.startsWith('/member') && !location.pathname.startsWith('/display')) {
          ElMessage.error('登录已过期，请重新扫码')
          location.href = '/scan'
        }
      } else {
        clearStaffToken()
        if (!location.pathname.includes('/login')) {
          ElMessage.error('登录已过期，请重新登录')
          location.href = '/login'
        }
      }
    } else if (status === 403) {
      ElMessage.error(msg || '无权限')
    } else if (status === 429) {
      ElMessage.error('请求过于频繁')
    }
    return Promise.reject(
      new ApiError(msg, {
        code: bizCode != null ? Number(bizCode) : undefined,
        status,
      }),
    )
  },
)

export default api
export { api }
