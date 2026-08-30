/** 员工登录态：仅 sessionStorage，关闭浏览器后需重新登录 */
const STAFF_KEY = 'snh_staff_token'
const LEGACY_KEY = 'token'

/** 丢弃旧版 localStorage 登录态，避免关浏览器后仍自动登录 */
function discardLocalStaffToken() {
  localStorage.removeItem(STAFF_KEY)
  localStorage.removeItem(LEGACY_KEY)
}

export function getStaffToken(): string {
  discardLocalStaffToken()
  return sessionStorage.getItem(STAFF_KEY) || sessionStorage.getItem(LEGACY_KEY) || ''
}

export function setStaffToken(token: string) {
  sessionStorage.setItem(STAFF_KEY, token)
  sessionStorage.setItem(LEGACY_KEY, token)
  discardLocalStaffToken()
}

export function clearStaffToken() {
  sessionStorage.removeItem(STAFF_KEY)
  sessionStorage.removeItem(LEGACY_KEY)
  discardLocalStaffToken()
}
