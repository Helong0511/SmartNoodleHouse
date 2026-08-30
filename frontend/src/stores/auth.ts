import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import api from '@/api'
import { clearStaffToken, getStaffToken, setStaffToken } from '@/utils/staffAuth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getStaffToken())
  const user = ref<any>(null)

  const permissions = computed<string[]>(() => user.value?.permissions || [])
  const isSuperAdmin = computed(() => user.value?.role === 'SUPER_ADMIN')

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

  async function login(username: string, password: string) {
    const res = await api.post('/auth/login', { username, password })
    token.value = res.data.access_token
    user.value = res.data.user
    setStaffToken(token.value)
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
      user.value = res.data
    } catch {
      logout()
    }
  }

  return { token, user, permissions, isSuperAdmin, hasPermission, canAccessKitchen, login, logout, fetchMe }
})
