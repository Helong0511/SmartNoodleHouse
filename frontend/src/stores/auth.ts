import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'

const STAFF_KEY = 'snh_staff_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(STAFF_KEY) || localStorage.getItem('token') || '')
  const user = ref<any>(null)

  async function login(username: string, password: string) {
    const res = await api.post('/auth/login', { username, password })
    token.value = res.data.access_token
    user.value = res.data.user
    localStorage.setItem(STAFF_KEY, token.value)
    localStorage.setItem('token', token.value) // 兼容旧键
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem(STAFF_KEY)
    localStorage.removeItem('token')
  }

  return { token, user, login, logout }
})
