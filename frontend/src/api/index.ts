import axios from 'axios'
import { ElMessage } from 'element-plus'
import { clearStaffToken, getStaffToken } from '@/utils/staffAuth'
import { reportApiLog } from '@/utils/logger'

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
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
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
  const customerToken = localStorage.getItem('snh_customer_token') || localStorage.getItem('customer_token')
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
      return Promise.reject(new Error(data.message || '请求失败'))
    }
    return data
  },
  (err) => {
    const status = err.response?.status
    const msg = parseApiError(err)
    const url = String(err.config?.url || '')
    reportApiLog('ERROR', err.config?.method || 'GET', url, { status, message: msg })
    if (status === 401) {
      clearStaffToken()
      if (!location.pathname.includes('/login') && !location.pathname.startsWith('/scan') && !location.pathname.startsWith('/order') && !location.pathname.startsWith('/member') && !location.pathname.startsWith('/display')) {
        ElMessage.error('登录已过期，请重新登录')
        location.href = '/login'
      }
    } else if (status === 403) {
      ElMessage.error(msg || '无权限')
    } else if (status === 429) {
      ElMessage.error('请求过于频繁')
    }
    return Promise.reject(new Error(msg))
  },
)

export { api }
export default api
