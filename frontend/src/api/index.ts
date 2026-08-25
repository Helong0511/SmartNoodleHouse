import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000
})

api.interceptors.request.use(config => {
  const staffToken = localStorage.getItem('token')
  const customerToken = localStorage.getItem('customer_token')
  const token = staffToken || customerToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  res => {
    const data = res.data
    if (data.code !== 0 && data.code !== undefined) {
      return Promise.reject(new Error(data.message || '请求失败'))
    }
    return data
  },
  err => Promise.reject(err)
)

export default api
