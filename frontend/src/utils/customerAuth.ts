/** 顾客 H5 登录态：sessionStorage，降低 XSS 持久窃取面（关闭标签页即失效） */
const CUSTOMER_TOKEN_KEY = 'snh_customer_token'
const CUSTOMER_TOKEN_LEGACY = 'customer_token'
const CUSTOMER_ID_KEY = 'snh_customer_id'
const CUSTOMER_ID_LEGACY = 'customer_id'

function migrateFromLocalStorage() {
  try {
    const t = localStorage.getItem(CUSTOMER_TOKEN_KEY) || localStorage.getItem(CUSTOMER_TOKEN_LEGACY)
    if (t && !sessionStorage.getItem(CUSTOMER_TOKEN_KEY) && !sessionStorage.getItem(CUSTOMER_TOKEN_LEGACY)) {
      sessionStorage.setItem(CUSTOMER_TOKEN_KEY, t)
      sessionStorage.setItem(CUSTOMER_TOKEN_LEGACY, t)
    }
    const id = localStorage.getItem(CUSTOMER_ID_KEY) || localStorage.getItem(CUSTOMER_ID_LEGACY)
    if (id && !sessionStorage.getItem(CUSTOMER_ID_KEY)) {
      sessionStorage.setItem(CUSTOMER_ID_KEY, id)
    }
    localStorage.removeItem(CUSTOMER_TOKEN_KEY)
    localStorage.removeItem(CUSTOMER_TOKEN_LEGACY)
    localStorage.removeItem(CUSTOMER_ID_KEY)
    localStorage.removeItem(CUSTOMER_ID_LEGACY)
  } catch {
    /* ignore */
  }
}

migrateFromLocalStorage()

export function getCustomerToken(): string {
  return sessionStorage.getItem(CUSTOMER_TOKEN_KEY) || sessionStorage.getItem(CUSTOMER_TOKEN_LEGACY) || ''
}

export function setCustomerToken(token: string, customerId?: string | number) {
  sessionStorage.setItem(CUSTOMER_TOKEN_KEY, token)
  sessionStorage.setItem(CUSTOMER_TOKEN_LEGACY, token)
  if (customerId != null && String(customerId)) {
    sessionStorage.setItem(CUSTOMER_ID_KEY, String(customerId))
    sessionStorage.setItem(CUSTOMER_ID_LEGACY, String(customerId))
  }
  // 清理历史 localStorage，避免双写
  try {
    localStorage.removeItem(CUSTOMER_TOKEN_KEY)
    localStorage.removeItem(CUSTOMER_TOKEN_LEGACY)
    localStorage.removeItem(CUSTOMER_ID_KEY)
    localStorage.removeItem(CUSTOMER_ID_LEGACY)
  } catch {
    /* ignore */
  }
}

export function getCustomerId(): string {
  return sessionStorage.getItem(CUSTOMER_ID_KEY) || sessionStorage.getItem(CUSTOMER_ID_LEGACY) || ''
}

export function clearCustomerToken() {
  sessionStorage.removeItem(CUSTOMER_TOKEN_KEY)
  sessionStorage.removeItem(CUSTOMER_TOKEN_LEGACY)
  sessionStorage.removeItem(CUSTOMER_ID_KEY)
  sessionStorage.removeItem(CUSTOMER_ID_LEGACY)
}


/** 员工退出/切换账号时清理同浏览器残留的顾客身份、购物车与桌台上下文。 */
export function clearCustomerSession() {
  try {
    clearCustomerToken()
  } catch {
    /* storage may be unavailable in restricted browser contexts */
  }
  const keys = [
    'snh_cart', 'cart', 'snh_cart_store_id',
    'snh_table_token', 'table_token', 'snh_table',
    'snh_channel', 'channel', 'snh_store_id',
    'member_prompt_done', 'h5_login_code',
  ]
  for (const storage of [sessionStorage, localStorage]) {
    try {
      for (const key of keys) storage.removeItem(key)
    } catch {
      /* ignore storage access failures */
    }
  }
}
