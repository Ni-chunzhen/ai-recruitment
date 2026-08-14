import axios from 'axios'

const apiClient = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

let accessToken: string | null = null
let refreshPromise: Promise<string | null> | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken() {
  return accessToken
}

export function clearAccessToken() {
  accessToken = null
}

apiClient.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    if (
      error.response?.status !== 401 ||
      originalRequest._retry ||
      originalRequest.url?.includes('/auth/login') ||
      originalRequest.url?.includes('/auth/refresh')
    ) {
      return Promise.reject(error)
    }

    originalRequest._retry = true

    if (!refreshPromise) {
      refreshPromise = apiClient
        .post<{ access_token: string }>('/auth/refresh')
        .then((response) => {
          accessToken = response.data.access_token
          return accessToken
        })
        .catch(() => {
          accessToken = null
          return null
        })
        .finally(() => {
          refreshPromise = null
        })
    }

    const newToken = await refreshPromise
    if (!newToken) {
      return Promise.reject(error)
    }

    originalRequest.headers.Authorization = `Bearer ${newToken}`
    return apiClient(originalRequest)
  },
)

export default apiClient
