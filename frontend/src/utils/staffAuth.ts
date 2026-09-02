/** 员工登录态：仅 sessionStorage，关闭浏览器后需重新登录 */
const STAFF_KEY = 'snh_staff_token'
const LEGACY_KEY = 'token'
const STAFF_USER_KEY = 'snh_staff_user'
const POS_LOCKED_KEY = 'pos_locked'

/** 丢弃旧版 localStorage 登录态，避免关浏览器后仍自动登录 */
function discardLocalStaffToken() {
  localStorage.removeItem(STAFF_KEY)
  localStorage.removeItem(LEGACY_KEY)
  localStorage.removeItem(STAFF_USER_KEY)
}

export type StaffSessionMeta = { id: number; role: string }

export function getStaffToken(): string {
  discardLocalStaffToken()
  return sessionStorage.getItem(STAFF_KEY) || sessionStorage.getItem(LEGACY_KEY) || ''
}

export function setStaffToken(token: string) {
  sessionStorage.setItem(STAFF_KEY, token)
  sessionStorage.setItem(LEGACY_KEY, token)
  discardLocalStaffToken()
}

export function setStaffSessionMeta(meta: StaffSessionMeta) {
  sessionStorage.setItem(STAFF_USER_KEY, JSON.stringify(meta))
}

export function getStaffSessionMeta(): StaffSessionMeta | null {
  const raw = sessionStorage.getItem(STAFF_USER_KEY)
  if (!raw) return null
  try {
    const o = JSON.parse(raw)
    if (o && typeof o.id === 'number' && typeof o.role === 'string') return o
  } catch {
    /* ignore */
  }
  return null
}

export function clearStaffToken() {
  sessionStorage.removeItem(STAFF_KEY)
  sessionStorage.removeItem(LEGACY_KEY)
  sessionStorage.removeItem(STAFF_USER_KEY)
  sessionStorage.removeItem(POS_LOCKED_KEY)
  discardLocalStaffToken()
}

export function isPosLocked(): boolean {
  return sessionStorage.getItem(POS_LOCKED_KEY) === '1'
}

export function setPosLocked(locked: boolean) {
  if (locked) sessionStorage.setItem(POS_LOCKED_KEY, '1')
  else sessionStorage.removeItem(POS_LOCKED_KEY)
}
