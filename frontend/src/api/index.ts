import axios from 'axios'
import { ElMessage } from 'element-plus'

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
    url.includes('/welcome/face') ||
    (config.headers?.['X-Client'] === 'customer')
  const staffToken = localStorage.getItem('snh_staff_token') || localStorage.getItem('token')
  const customerToken = localStorage.getItem('snh_customer_token') || localStorage.getItem('customer_token')
  const token = isCustomerApi ? customerToken || staffToken : staffToken || customerToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => {
    const data = res.data
    if (data && data.code !== 0 && data.code !== undefined) {
      return Promise.reject(new Error(data.message || '请求失败'))
    }
    return data
  },
  (err) => {
    const status = err.response?.status
    const msg = err.response?.data?.message || err.message || '网络错误'
    if (status === 401) {
      localStorage.removeItem('snh_staff_token')
      localStorage.removeItem('token')
      if (!location.pathname.includes('/login') && !location.pathname.startsWith('/m/')) {
        ElMessage.error('登录已过期，请重新登录')
        location.href = '/login'
      }
    } else if (status === 403) {
      ElMessage.error(msg || '无权限')
    } else if (status === 429) {
      ElMessage.error('请求过于频繁')
    }
    return Promise.reject(err instanceof Error ? err : new Error(msg))
  },
)

export { api }
export default api
