import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { clearAccessToken } from '../src/api/client'
import * as authApi from '../src/api/auth'
import { useAuthStore } from '../src/stores/auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    clearAccessToken()
    vi.restoreAllMocks()
  })

  it('marks user authenticated after sign in', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue({
      access_token: 'token',
      token_type: 'bearer',
      user: {
        id: '1',
        username: 'admin',
        display_name: 'Admin',
        is_active: true,
        must_change_password: false,
        roles: ['system_admin'],
        permissions: ['profile.read'],
      },
    })

    const store = useAuthStore()
    await store.signIn('admin', 'password')

    expect(store.isAuthenticated).toBe(true)
    expect(store.user?.username).toBe('admin')
  })

  it('clears state when bootstrap refresh fails', async () => {
    vi.spyOn(authApi, 'refreshSession').mockRejectedValue(new Error('unauthorized'))

    const store = useAuthStore()
    await store.bootstrap()

    expect(store.isAuthenticated).toBe(false)
    expect(store.initialized).toBe(true)
  })
})
