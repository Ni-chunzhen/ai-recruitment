import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as healthApi from '../src/api/health'
import HomeView from '../src/views/HomeView.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/jobs', name: 'jobs', component: { template: '<div />' } },
    { path: '/jobs/new', name: 'job-create', component: { template: '<div />' } },
    { path: '/login', name: 'login', component: { template: '<div />' } },
  ],
})

describe('HomeView', () => {
  beforeEach(async () => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    await router.push('/')
  })

  it('shows success state when health check passes', async () => {
    vi.spyOn(healthApi, 'fetchHealth').mockResolvedValue({
      code: 0,
      message: 'ok',
      data: {
        status: 'ok',
        service: 'ai-recruitment-api',
      },
    })

    const wrapper = mount(HomeView, {
      global: {
        plugins: [router],
        stubs: {
          AdminLayout: { template: '<div><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
        },
      },
    })
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('前后端连接正常')
    })
    expect(wrapper.text()).toContain('ai-recruitment-api')
  })

  it('shows error state when health check fails', async () => {
    vi.spyOn(healthApi, 'fetchHealth').mockRejectedValue(new Error('network error'))

    const wrapper = mount(HomeView, {
      global: {
        plugins: [router],
        stubs: {
          AdminLayout: { template: '<div><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
        },
      },
    })
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('后端连接失败')
    })
    expect(wrapper.find('button').exists()).toBe(true)
    expect(wrapper.text()).toContain('重新检测')
  })
})
