import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import api from '@/api'
import {
  clearStaffToken,
  getStaffSessionMeta,
  getStaffToken,
  setStaffSessionMeta,
  setStaffToken,
} from '@/utils/staffAuth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getStaffToken())
  const user = ref<any>(null)

  const permissions = computed<string[]>(() => user.value?.permissions || [])
  const isSuperAdmin = computed(() => user.value?.role === 'SUPER_ADMIN')
  const isCashier = computed(() => user.value?.role === 'CASHIER')
  const isStoreAdmin = computed(() => user.value?.role === 'STORE_ADMIN')
  const canManageSettlement = computed(
    () => isSuperAdmin.value || isStoreAdmin.value || permissions.value.includes('payment:refund'),
  )

  function hasPermission(code: string) {
    if (isSuperAdmin.value) return true
    return permissions.value.includes(code)
  }

  /** 厨房 KDS：超管 / 店长 / 厨房 / 有订单更新权限 */
  function canAccessKitchen() {
    if (isSuperAdmin.value) return true
    const role = user.value?.role
    if (role === 'STORE_ADMIN' || role === 'KITCHEN') return true
    return hasPermission('order:update')
  }

  /** 收银点餐：超管 / 店长 / 收银员 / 有 order:update */
  function canAccessPos() {
    if (isSuperAdmin.value) return true
    const role = user.value?.role
    if (role === 'STORE_ADMIN' || role === 'CASHIER') return true
    return hasPermission('order:update')
  }

  function syncSessionMeta() {
    if (user.value?.id && user.value?.role) {
      setStaffSessionMeta({ id: user.value.id, role: user.value.role })
    }
  }

  function assertSessionNotStale() {
    const meta = getStaffSessionMeta()
    if (!meta || !user.value) return
    if (meta.id !== user.value.id || meta.role !== user.value.role) {
      logout()
    }
  }

  async function login(username: string, password: string) {
    logout()
    const res = await api.post('/auth/login', { username, password })
    token.value = res.data.access_token
    user.value = res.data.user
    setStaffToken(token.value)
    syncSessionMeta()
    await fetchMe()
  }

  function logout() {
    token.value = ''
    user.value = null
    clearStaffToken()
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      const res = await api.get('/auth/me')
      const meta = getStaffSessionMeta()
      if (meta && meta.id !== res.data.id) {
        logout()
        return
      }
      user.value = res.data
      syncSessionMeta()
    } catch {
      logout()
    }
  }

  return {
    token,
    user,
    permissions,
    isSuperAdmin,
    isCashier,
    isStoreAdmin,
    canManageSettlement,
    hasPermission,
    canAccessKitchen,
    canAccessPos,
    login,
    logout,
    fetchMe,
    assertSessionNotStale,
  }
})
