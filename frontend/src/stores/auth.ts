import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import api from '@/api'

const STAFF_KEY = 'snh_staff_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(STAFF_KEY) || localStorage.getItem('token') || '')
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
    localStorage.setItem(STAFF_KEY, token.value)
    localStorage.setItem('token', token.value)
    await fetchMe()
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem(STAFF_KEY)
    localStorage.removeItem('token')
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
