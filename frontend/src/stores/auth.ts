import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  changePassword,
  fetchMe,
  login,
  logout,
  refreshSession,
  type UserSummary,
} from '../api/auth'
import { clearAccessToken } from '../api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserSummary | null>(null)
  const loading = ref(false)
  const initialized = ref(false)

  const isAuthenticated = computed(() => user.value !== null)
  const mustChangePassword = computed(
    () => user.value?.must_change_password ?? false,
  )

  function hasPermission(code: string) {
    return user.value?.permissions.includes(code) ?? false
  }

  async function bootstrap() {
    loading.value = true
    try {
      await refreshSession()
      user.value = await fetchMe()
    } catch {
      clearAccessToken()
      user.value = null
    } finally {
      loading.value = false
      initialized.value = true
    }
  }

  async function signIn(username: string, password: string) {
    const response = await login(username, password)
    user.value = response.user
    return response.user
  }

  async function signOut() {
    try {
      await logout()
    } finally {
      clearAccessToken()
      user.value = null
    }
  }

  async function updatePassword(currentPassword: string, newPassword: string) {
    await changePassword(currentPassword, newPassword)
    user.value = await fetchMe()
  }

  return {
    user,
    loading,
    initialized,
    isAuthenticated,
    mustChangePassword,
    hasPermission,
    bootstrap,
    signIn,
    signOut,
    updatePassword,
  }
})
