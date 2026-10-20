import axios from 'axios'
import { ElMessage } from 'element-plus'
import { clearStaffToken, getStaffToken } from '@/utils/staffAuth'
import { clearCustomerToken, getCustomerToken, setCustomerToken } from '@/utils/customerAuth'
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

/** 将 Axios / 浏览器常见英文网络错误转成中文提示 */
function localizeNetworkMessage(raw: string, code?: string): string | null {
  const msg = (raw || '').trim()
  const lower = msg.toLowerCase()
  if (code === 'ECONNABORTED' || /timeout of \d+ms exceeded/i.test(msg) || lower.includes('timeout')) {
    return '请求超时，请检查网络或稍后重试'
  }
  if (code === 'ERR_NETWORK' || lower === 'network error') {
    return '网络连接失败，请检查服务是否已启动'
  }
  if (code === 'ERR_CANCELED' || lower === 'request aborted' || lower.includes('aborted')) {
    return '请求已取消'
  }
  if (/failed to fetch|load failed|networkerror/i.test(msg)) {
    return '网络连接失败，请检查服务是否已启动'
  }
  // 纯英文 Axios 兜底，避免把英文直接抛给用户
  if (msg && /^[A-Za-z0-9][\w\s.,:;'"()/-]*$/.test(msg) && !/[\u4e00-\u9fff]/.test(msg)) {
    return '网络错误，请稍后重试'
  }
  return null
}

function localizeValidationMsg(raw: string): string {
  const msg = (raw || '').trim()
  if (!msg) return '请求参数有误'
  if (/field required/i.test(msg)) return '请填写必填项'
  if (/value is not a valid/i.test(msg)) return '参数格式不正确'
  if (/input should be/i.test(msg)) return '参数类型不正确'
  if (/string should have at least|ensure this value has at least/i.test(msg)) return '内容过短'
  if (/string should have at most|ensure this value has at most/i.test(msg)) return '内容过长'
  if (/none is not an allowed value/i.test(msg)) return '该项不能为空'
  const localized = localizeNetworkMessage(msg)
  return localized || msg
}

export function parseApiError(err: unknown): string {
  if (!err || typeof err !== 'object') return '网络错误'
  const ax = err as {
    response?: { data?: any; status?: number }
    message?: string
    code?: string
  }
  const data = ax.response?.data
  if (data?.message && typeof data.message === 'string') {
    return localizeNetworkMessage(data.message) || data.message
  }
  if (Array.isArray(data?.detail)) {
    return data.detail.map((d: any) => localizeValidationMsg(String(d.msg || JSON.stringify(d)))).join('；')
  }
  if (typeof data?.detail === 'string') return localizeValidationMsg(data.detail)
  if (ax.response?.status === 422) return '请求参数有误，请检查表单'
  if (ax.response?.status === 502 || ax.response?.status === 503 || ax.response?.status === 504) {
    return '服务暂时不可用，请稍后重试'
  }
  const localized = localizeNetworkMessage(ax.message || '', ax.code)
  if (localized) return localized
  if (ax.message) return localizeNetworkMessage(ax.message) || ax.message
  return '网络错误'
}

/** 顾客侧 API 判定（全仓唯一入口）：请求选顾客 JWT、401 后走顾客刷新/登出流程 */
function isCustomerApiUrl(url: string, clientHint: string): boolean {
  return (
    url.includes('/miniprogram') ||
    url.includes('/orders/mine') ||
    /\/orders\/\d+\/reorder/.test(url) ||
    url.includes('/payments/orders/') ||
    clientHint === 'customer'
  )
}

/** 强制顾客 token 的 API（即便后台员工已登录，也不允许员工态抢占） */
function isForceCustomerUrl(url: string): boolean {
  return (
    url.includes('/miniprogram') || url.includes('/orders/mine') || /\/orders\/\d+\/reorder/.test(url)
  )
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json; charset=utf-8' },
})

api.interceptors.request.use((config) => {
  const url = config.url || ''
  const clientHint = String(config.headers?.['X-Client'] || '').toLowerCase()
  // 后台管理顾客列表/合并等走员工 token；仅顾客侧 API 才用顾客 JWT
  const staffToken = getStaffToken()
  const customerToken = getCustomerToken()
  // 顾客订单列表/复购/小程序强制用顾客 token，避免后台登录态抢占
  const forceCustomer = isForceCustomerUrl(url)
  const isCustomerApi = isCustomerApiUrl(url, clientHint)
  const isStaffPath =
    !forceCustomer &&
    !isCustomerApi &&
    (url.startsWith('/customers') ||
      url.startsWith('/orders') ||
      url.startsWith('/payments') ||
      url.startsWith('/products') ||
      url.startsWith('/settings') ||
      url.startsWith('/users') ||
      url.startsWith('/wallet') ||
      url.startsWith('/delivery') ||
      url.startsWith('/pos') ||
      url.startsWith('/cameras') ||
      url.startsWith('/tables') ||
      url.startsWith('/coupons') ||
      url.startsWith('/dashboard') ||
      url.startsWith('/audit') ||
      url.startsWith('/auth'))
  let token: string | null | undefined
  if (forceCustomer) {
    token = customerToken
  } else if (isCustomerApi) {
    token = customerToken || staffToken
  } else if (isStaffPath) {
    token = staffToken
  } else {
    token = staffToken || customerToken
  }
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/** 顾客 JWT 滑动续期（单飞）；对齐小程序 /miniprogram/auth/refresh */
let customerRefreshPromise: Promise<boolean> | null = null

async function attemptCustomerTokenRefresh(): Promise<boolean> {
  if (customerRefreshPromise) return customerRefreshPromise
  const oldToken = getCustomerToken()
  if (!oldToken) return false
  customerRefreshPromise = (async () => {
    try {
      const res = await axios.post(
        `${api.defaults.baseURL || '/api'}/miniprogram/auth/refresh`,
        {},
        {
          headers: {
            Authorization: `Bearer ${oldToken}`,
            'X-Client': 'customer',
            'Content-Type': 'application/json; charset=utf-8',
          },
          timeout: 10000,
        },
      )
      const body = res.data
      const token = body?.code === 0 ? body?.data?.customer_token : null
      if (token) {
        setCustomerToken(token, body?.data?.customer_id)
        return true
      }
      return false
    } catch {
      return false
    } finally {
      customerRefreshPromise = null
    }
  })()
  return customerRefreshPromise
}

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
  async (err) => {
    const status = err.response?.status
    const msg = parseApiError(err)
    const bizCode = err.response?.data?.code
    const url = String(err.config?.url || '')
    const cfg = err.config || {}
    reportApiLog('ERROR', cfg.method || 'GET', url, { status, message: msg, code: bizCode })
    if (status === 401) {
      const reqUrl = String(cfg.url || '')
      const clientHint = String(cfg.headers?.['X-Client'] || '').toLowerCase()
      const onAdmin =
        location.pathname.startsWith('/admin') ||
        (!location.pathname.startsWith('/scan') &&
          !location.pathname.startsWith('/order') &&
          !location.pathname.startsWith('/member') &&
          !location.pathname.startsWith('/display') &&
          !location.pathname.startsWith('/kitchen') &&
          location.pathname !== '/')
      const isCustApi = isCustomerApiUrl(reqUrl, clientHint)
      // H5 顾客路径：先静默 refresh 再重放一次（对齐小程序）
      if (
        isCustApi &&
        !onAdmin &&
        !(cfg as { __customerRefreshed?: boolean }).__customerRefreshed &&
        !/auth\/refresh/.test(reqUrl) &&
        getCustomerToken()
      ) {
        const ok = await attemptCustomerTokenRefresh()
        if (ok) {
          ; (cfg as { __customerRefreshed?: boolean }).__customerRefreshed = true
          cfg.headers = cfg.headers || {}
          cfg.headers.Authorization = `Bearer ${getCustomerToken()}`
          return api.request(cfg)
        }
      }
      if (isCustApi && !onAdmin) {
        clearCustomerToken()
        if (
          !location.pathname.startsWith('/scan') &&
          !location.pathname.startsWith('/order') &&
          !location.pathname.startsWith('/member') &&
          !location.pathname.startsWith('/display')
        ) {
          ElMessage.error('登录已过期，请重新扫码')
          location.href = '/scan'
        }
      } else if (!isCustApi) {
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
