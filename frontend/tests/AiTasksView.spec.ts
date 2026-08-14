import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import * as adminAiTasksApi from '../src/api/adminAiTasks'
import type { AITaskAdminDetail, AITaskAdminListItem } from '../src/api/adminAiTasks'
import AdminLayout from '../src/layouts/AdminLayout.vue'
import { useAuthStore } from '../src/stores/auth'
import AiTasksView from '../src/views/AiTasksView.vue'
import ForbiddenView from '../src/views/ForbiddenView.vue'
import HomeView from '../src/views/HomeView.vue'

vi.mock('element-plus', async () => {
  const actual = await vi.importActual<typeof import('element-plus')>('element-plus')
  return {
    ...actual,
    ElMessage: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
    ElMessageBox: {
      confirm: vi.fn().mockResolvedValue(true),
    },
  }
})

const { ElMessageBox } = await import('element-plus')

const TASK_ID = '22222222-2222-2222-2222-222222222222'

function makeUser(permissions: string[]) {
  return {
    id: 'u1',
    username: 'admin',
    display_name: 'Admin',
    is_active: true,
    must_change_password: false,
    roles: permissions.includes('audit.read') ? ['system_admin'] : ['recruiter_admin'],
    permissions,
  }
}

function makeListItem(
  overrides: Partial<AITaskAdminListItem> = {},
): AITaskAdminListItem {
  return {
    id: TASK_ID,
    task_type: 'RESUME_SCORE',
    business_type: 'application',
    business_id: 'app-1',
    status: 'failed',
    attempt_count: 3,
    retry_cycle_no: 1,
    cycle_attempt_count: 1,
    error_category: 'retryable',
    error_code: 'provider_error',
    error_message: 'provider timeout',
    created_by: 'u1',
    created_at: '2026-08-14T01:00:00Z',
    started_at: '2026-08-14T01:00:00Z',
    finished_at: '2026-08-14T01:01:00Z',
    duration_ms: 1200,
    ...overrides,
  }
}

function makeDetail(
  overrides: Partial<AITaskAdminDetail> = {},
): AITaskAdminDetail {
  return {
    ...makeListItem(),
    attempts: [
      {
        id: 'att-1',
        attempt_no: 3,
        retry_cycle_no: 1,
        cycle_attempt_no: 1,
        status: 'failed',
        started_at: '2026-08-14T01:00:00Z',
        finished_at: '2026-08-14T01:01:00Z',
        duration_ms: 1200,
        http_status: 502,
        error_category: 'retryable',
        error_message: 'provider timeout',
        provider_run_id: 'run-abc',
        request_id: 'req-xyz',
      },
    ],
    ...overrides,
  }
}

const globalStubs = {
  'el-button': {
    props: ['disabled', 'loading', 'type', 'plain', 'link', 'size'],
    template: '<button :disabled="disabled || loading" :data-type="type"><slot /></button>',
  },
  'el-alert': {
    props: ['title', 'type'],
    template: '<div class="el-alert" :data-type="type">{{ title }}<slot /></div>',
  },
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="filter-input" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select class="filter-select" :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-date-picker': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="date-range" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-table': {
    props: ['data'],
    template:
      '<div class="el-table"><div v-for="row in data" :key="row.id" class="table-row" @click="$emit(\'row-click\', row)">{{ row.id }} {{ row.status }} {{ row.task_type }} {{ row.error_message }}</div><slot /></div>',
  },
  'el-table-column': { template: '<span />' },
  'el-tag': { props: ['type'], template: '<span class="el-tag"><slot /></span>' },
  'el-pagination': {
    props: ['currentPage', 'pageSize', 'total'],
    emits: ['current-change', 'size-change'],
    template:
      '<div class="pager"><button class="next-page" @click="$emit(\'current-change\', 2)">next</button></div>',
  },
  'el-drawer': {
    props: ['modelValue', 'title'],
    template: '<div v-if="modelValue" class="drawer">{{ title }}<slot /></div>',
  },
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((btn) => btn.text().includes(text))
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView, meta: { requiresAuth: true, permission: 'profile.read' } },
      {
        path: '/system/ai-tasks',
        name: 'admin-ai-tasks',
        component: AiTasksView,
        meta: { requiresAuth: true, permission: 'audit.read' },
      },
      { path: '/forbidden', name: 'forbidden', component: ForbiddenView, meta: { requiresAuth: true } },
    ],
  })
}

async function mountLayout(permissions: string[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAuthStore()
  store.initialized = true
  store.user = makeUser(permissions)
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  return mount(AdminLayout, {
    global: { plugins: [pinia, router] },
  })
}

async function mountView(
  permissions: string[] = ['audit.read', 'ai_task.manage'],
): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAuthStore()
  store.initialized = true
  store.user = makeUser(permissions)
  const router = makeRouter()
  await router.push('/system/ai-tasks')
  await router.isReady()
  const wrapper = mount(AiTasksView, {
    global: {
      plugins: [pinia, router],
      stubs: { AdminLayout: { template: '<div class="layout"><slot /></div>' }, ...globalStubs },
    },
  })
  await nextTick()
  await nextTick()
  return wrapper
}

describe('AiTasksView and admin menu', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.mocked(ElMessageBox.confirm).mockResolvedValue(true)
    vi.spyOn(adminAiTasksApi, 'listAdminAITasks').mockResolvedValue({
      items: [makeListItem()],
      total: 1,
      page: 1,
      page_size: 20,
    })
    vi.spyOn(adminAiTasksApi, 'getAdminAITask').mockResolvedValue(makeDetail())
    vi.spyOn(adminAiTasksApi, 'retryAdminAITask').mockResolvedValue(makeDetail({ status: 'pending' }))
    vi.spyOn(adminAiTasksApi, 'cancelAdminAITask').mockResolvedValue(makeDetail({ status: 'cancelled' }))
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('shows AI task menu for system admin', async () => {
    const wrapper = await mountLayout(['audit.read', 'profile.read'])
    expect(wrapper.text()).toContain('AI任务中心')
    expect(wrapper.text()).toContain('系统管理')
  })

  it('hides AI task menu for non-admin', async () => {
    const wrapper = await mountLayout(['recruitment.manage', 'profile.read'])
    expect(wrapper.text()).not.toContain('AI任务中心')
  })

  it('rejects non-admin from the admin route', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAuthStore()
    store.initialized = true
    store.user = makeUser(['recruitment.manage', 'profile.read'])
    const { default: appRouter } = await import('../src/router')
    await appRouter.push('/system/ai-tasks')
    await appRouter.isReady()
    expect(appRouter.currentRoute.value.name).toBe('forbidden')
  })

  it('keeps the page read-only without ai_task.manage', async () => {
    const wrapper = await mountView(['audit.read'])
    await vi.waitFor(() => expect(wrapper.find('.table-row').exists()).toBe(true))
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.drawer').exists()).toBe(true))
    expect(wrapper.text()).toContain('任务详情')
    expect(wrapper.text()).not.toContain('重新执行')
    expect(wrapper.text()).not.toContain('取消任务')
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'pending', attempts: [] }),
    )
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('待处理'))
    expect(wrapper.text()).not.toContain('取消任务')
  })

  it('shows retry and cancel only when user has ai_task.manage', async () => {
    const wrapper = await mountView(['audit.read', 'ai_task.manage'])
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('重新执行'))
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'pending', attempts: [] }),
    )
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('取消任务'))
  })

  it('loads the list and paginates', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(adminAiTasksApi.listAdminAITasks).toHaveBeenCalled()
      expect(wrapper.text()).toContain(TASK_ID)
    })
    await wrapper.find('.next-page').trigger('click')
    await vi.waitFor(() => {
      expect(adminAiTasksApi.listAdminAITasks).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 }),
      )
    })
  })

  it('filters by status, type and date range', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.find('.filter-select').exists()).toBe(true))
    const selects = wrapper.findAll('.filter-select')
    await selects[0].setValue('RESUME_SCORE')
    await selects[1].setValue('failed')
    const dates = wrapper.findAll('.date-range')
    await dates[0].setValue('2026-08-01')
    await dates[1].setValue('2026-08-14')
    await buttonByText(wrapper, '查询')!.trigger('click')
    await vi.waitFor(() => {
      expect(adminAiTasksApi.listAdminAITasks).toHaveBeenCalledWith(
        expect.objectContaining({
          task_type: 'RESUME_SCORE',
          status: 'failed',
          created_from: '2026-08-01',
          created_to: '2026-08-14',
        }),
      )
    })
  })

  it('resets filters', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(buttonByText(wrapper, '重置')).toBeTruthy())
    vi.mocked(adminAiTasksApi.listAdminAITasks).mockClear()
    await buttonByText(wrapper, '重置')!.trigger('click')
    await vi.waitFor(() => {
      expect(adminAiTasksApi.listAdminAITasks).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, status: undefined, task_type: undefined }),
      )
    })
  })

  it('opens the detail drawer', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.find('.table-row').exists()).toBe(true))
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => {
      expect(adminAiTasksApi.getAdminAITask).toHaveBeenCalledWith(TASK_ID)
      expect(wrapper.find('.drawer').exists()).toBe(true)
    })
  })

  it('shows attempt dual counters and copyable tech ids', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.find('.table-row').exists()).toBe(true))
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('run-abc'))
    expect(wrapper.text()).toContain('req-xyz')
    expect(wrapper.text()).toContain('1')
    await buttonByText(wrapper, '复制')!.trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenCalled()
  })

  it('does not render raw_response', async () => {
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue({
      ...makeDetail(),
      ...({ raw_response: { dump: 'RAW_RESPONSE_SECRET' } } as object),
    } as AITaskAdminDetail)
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.drawer').exists()).toBe(true))
    expect(wrapper.text()).not.toContain('RAW_RESPONSE_SECRET')
    expect(wrapper.text()).not.toContain('raw_response')
  })

  it('shows retry for failed and output_invalid', async () => {
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('重新执行'))
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'output_invalid' }),
    )
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('重新执行'))
  })

  it('shows cancel for pending only', async () => {
    vi.mocked(adminAiTasksApi.listAdminAITasks).mockResolvedValue({
      items: [makeListItem({ status: 'pending' })],
      total: 1,
      page: 1,
      page_size: 20,
    })
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'pending', attempts: [] }),
    )
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('取消任务'))
    expect(wrapper.text()).not.toContain('重新执行')
  })

  it('hides illegal actions for running, succeeded and cancelled', async () => {
    for (const status of ['running', 'succeeded', 'cancelled'] as const) {
      vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
        makeDetail({ status, attempts: [] }),
      )
      const wrapper = await mountView()
      await wrapper.find('.table-row').trigger('click')
      await vi.waitFor(() => expect(wrapper.find('.drawer').exists()).toBe(true))
      expect(wrapper.text()).not.toContain('重新执行')
      expect(wrapper.text()).not.toContain('取消任务')
      if (status === 'running') {
        expect(wrapper.text()).toContain('执行中')
      }
      wrapper.unmount()
    }
  })

  it('confirms retry and refreshes detail', async () => {
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(buttonByText(wrapper, '重新执行')).toBeTruthy())
    vi.mocked(adminAiTasksApi.getAdminAITask).mockClear()
    await buttonByText(wrapper, '重新执行')!.trigger('click')
    await vi.waitFor(() => {
      expect(ElMessageBox.confirm).toHaveBeenCalled()
      expect(adminAiTasksApi.retryAdminAITask).toHaveBeenCalledWith(TASK_ID)
      expect(adminAiTasksApi.getAdminAITask).toHaveBeenCalledWith(TASK_ID)
    })
  })

  it('confirms cancel and refreshes detail', async () => {
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'pending', attempts: [] }),
    )
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(buttonByText(wrapper, '取消任务')).toBeTruthy())
    vi.mocked(adminAiTasksApi.getAdminAITask).mockClear()
    await buttonByText(wrapper, '取消任务')!.trigger('click')
    await vi.waitFor(() => {
      expect(ElMessageBox.confirm).toHaveBeenCalled()
      expect(adminAiTasksApi.cancelAdminAITask).toHaveBeenCalledWith(TASK_ID, expect.anything())
      expect(adminAiTasksApi.getAdminAITask).toHaveBeenCalledWith(TASK_ID)
    })
  })

  it('shows refresh prompt on 409 conflict', async () => {
    const conflict = Object.assign(new Error('conflict'), {
      response: { status: 409, data: { detail: 'only pending tasks can be cancelled' } },
    })
    vi.mocked(adminAiTasksApi.retryAdminAITask).mockRejectedValue(conflict)
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(buttonByText(wrapper, '重新执行')).toBeTruthy())
    await buttonByText(wrapper, '重新执行')!.trigger('click')
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('任务状态已变化，请刷新后重试')
    })
  })

  it('stops polling after the drawer is closed', async () => {
    vi.useFakeTimers()
    vi.mocked(adminAiTasksApi.getAdminAITask).mockResolvedValue(
      makeDetail({ status: 'running', attempts: [] }),
    )
    const wrapper = await mountView()
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.drawer').exists()).toBe(true))
    const callsAfterOpen = vi.mocked(adminAiTasksApi.getAdminAITask).mock.calls.length
    vi.mocked(adminAiTasksApi.getAdminAITask).mockClear()
    await buttonByText(wrapper, '关闭')!.trigger('click')
    await vi.advanceTimersByTimeAsync(20000)
    expect(vi.mocked(adminAiTasksApi.getAdminAITask).mock.calls.length).toBe(0)
    expect(callsAfterOpen).toBeGreaterThan(0)
    vi.useRealTimers()
  })
})
