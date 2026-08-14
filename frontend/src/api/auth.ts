import apiClient, { setAccessToken } from './client'

export interface UserSummary {
  id: string
  username: string
  display_name: string
  is_active: boolean
  must_change_password: boolean
  roles: string[]
  permissions: string[]
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: UserSummary
}

export interface MeResponse {
  user: UserSummary
}

export async function login(username: string, password: string) {
  const response = await apiClient.post<LoginResponse>('/auth/login', {
    username,
    password,
  })
  setAccessToken(response.data.access_token)
  return response.data
}

export async function refreshSession() {
  const response = await apiClient.post<{ access_token: string }>('/auth/refresh')
  setAccessToken(response.data.access_token)
  return response.data
}

export async function logout() {
  await apiClient.post('/auth/logout')
}

export async function fetchMe() {
  const response = await apiClient.get<MeResponse>('/auth/me')
  return response.data.user
}

export async function changePassword(currentPassword: string, newPassword: string) {
  await apiClient.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}
