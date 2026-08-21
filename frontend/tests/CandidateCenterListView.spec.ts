import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import * as candidateCenterApi from '../src/api/candidateCenter'
import AdminLayout from '../src/layouts/AdminLayout.vue'
import ForbiddenView from '../src/views/ForbiddenView.vue'
import HomeView from '../src/views/HomeView.vue'
import { useAuthStore } from '../src/stores/auth'

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
  }
})

const globalStubs = {
  'el-button': {
    props: ['type', 'plain', 'link', 'size', 'disabled'],
    emits: ['click'],
    template:
      '<button :data-test="$attrs[\'data-test\']" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select :data-test="$attrs[\'data-test\']" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-table': {
    props: ['data'],
    emits: ['row-click'],
    template:
      '<div :data-test="$attrs[\'data-test\']"><div v-for="row in data" :key="row.application_id" class="table-row" @click="$emit(\'row-click\', row)">{{ row.name }} {{ row.job_name }} {{ row.status }} {{ row.pipeline_status }} {{ row.round_name || \'\' }} {{ row.schedule_status }} {{ row.invitation_status }} {{ row.transcript_status }} {{ row.question_status }} {{ row.analysis_status }}</div><slot /></div>',
  },
  'el-table-column': { template: '<span />' },
  'el-tag': { template: '<span><slot /></span>' },
  'el-empty': { props: ['description'], template: '<div class="el-empty">{{ description }}</div>' },
  'el-alert': { props: ['title'], template: '<div class="el-alert">{{ title }}</div>' },
  'el-pagination': {
    props: ['total', 'currentPage', 'pageSize'],
    emits: ['current-change'],
    template: '<div class="pager">total:{{ total }} size:{{ pageSize }}</div>',
  },
}

function makeUser(permissions: string[]) {
  return {
    id: 'u1',
    username: 'recruiter',
    display_name: 'Recruiter',
    is_active: true,
    must_change_password: false,
    roles: ['recruiter'],
    permissions,
  }
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView, meta: { requiresAuth: true, permission: 'profile.read' } },
      { path: '/candidate-center', name: 'candidate-center', component: { template: '<div />' } },
      {
        path: '/candidate-center/candidates/:candidateId/applications/:applicationId',
        name: 'candidate-center-detail',
        component: { template: '<div />' },
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
  return mount(AdminLayout, { global: { plugins: [pinia, router] } })
}

async function mountView(permissions: string[] = ['recruitment.manage', 'profile.read']) {
  const { default: CandidateCenterListView } = await import('../src/views/CandidateCenterListView.vue')
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAuthStore()
  store.initialized = true
  store.user = makeUser(permissions)
  const router = makeRouter()
  await router.push('/candidate-center')
  await router.isReady()
  const wrapper = mount(CandidateCenterListView, {
    global: {
      plugins: [pinia, router],
      directives: { loading: {} },
      stubs: { AdminLayout: { template: '<div><slot /></div>' }, ...globalStubs },
    },
  })
  await nextTick()
  await nextTick()
  return { wrapper, router }
}

describe('CandidateCenter list menu, route and view', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(candidateCenterApi, 'listCandidateCenterApplications').mockResolvedValue({
      items: [
        {
          application_id: 'app-1',
          candidate_id: 'cand-1',
          name: '张三',
          phone: '13800000000',
          email: 'a@example.com',
          job_id: 'job-1',
          job_name: '后端工程师',
          job_code: 'JOB-1',
          job_version_id: 'ver-1',
          job_version_label: 'v1',
          status: 'in_progress',
          pipeline_status: 'interviewing',
          round_id: 'round-1',
          round_name: '一面',
          sequence_no: 1,
          round_status: 'scheduled',
          schedule_status: 'scheduled',
          invitation_status: 'sent',
          transcript_status: 'ready',
          question_status: 'ready',
          analysis_status: 'ready',
          analysis_overall_score: 82.5,
        },
      ],
      total: 21,
      page: 1,
      page_size: 20,
    })
  })

  it('shows candidate center menu after resumes for recruitment.manage', async () => {
    const wrapper = await mountLayout(['recruitment.manage', 'profile.read'])
    const text = wrapper.text()
    expect(text).toContain('简历库')
    expect(text).toContain('候选人中心')
    expect(text.indexOf('简历库')).toBeLessThan(text.indexOf('候选人中心'))
    const links = wrapper.findAll('a')
    const target = links.find((link) => link.text().includes('候选人中心'))
    expect(target?.attributes('href')).toBe('/candidate-center')
  })

  it('hides candidate center menu for interview.execute only', async () => {
    const wrapper = await mountLayout(['interview.execute', 'profile.read'])
    expect(wrapper.text()).not.toContain('候选人中心')
  })

  it('forbids interview.execute only user on candidate-center route', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAuthStore()
    store.initialized = true
    store.user = makeUser(['interview.execute', 'profile.read'])
    const { default: appRouter } = await import('../src/router')
    await appRouter.push('/candidate-center')
    await appRouter.isReady()
    expect(appRouter.currentRoute.value.name).toBe('forbidden')
  })

  it('loads list with assigned true by default', async () => {
    await mountView()
    await vi.waitFor(() => {
      expect(candidateCenterApi.listCandidateCenterApplications).toHaveBeenCalledWith(
        expect.objectContaining({ assigned: true, page: 1, page_size: 20 }),
      )
    })
  })

  it('switches to all applications with assigned false', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="assigned-filter-all"]').exists()).toBe(true))
    await wrapper.find('[data-test="assigned-filter-all"]').trigger('click')
    await vi.waitFor(() => {
      expect(candidateCenterApi.listCandidateCenterApplications).toHaveBeenLastCalledWith(
        expect.objectContaining({ assigned: false }),
      )
    })
  })

  it('renders required columns and paginates when total is 21', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="candidate-center-table"]').exists()).toBe(true))
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('后端工程师')
    expect(wrapper.text()).toContain('in_progress')
    expect(wrapper.text()).toContain('interviewing')
    expect(wrapper.text()).toContain('一面')
    expect(wrapper.text()).toContain('scheduled')
    expect(wrapper.text()).toContain('sent')
    expect(wrapper.text()).toContain('ready')
    expect(wrapper.text()).toContain('total:21')
    expect(wrapper.text()).toContain('size:20')
  })

  it('navigates to candidate-center-detail on row click', async () => {
    const { wrapper, router } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('.table-row').exists()).toBe(true))
    await wrapper.find('.table-row').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('candidate-center-detail')
      expect(router.currentRoute.value.params.candidateId).toBe('cand-1')
      expect(router.currentRoute.value.params.applicationId).toBe('app-1')
    })
  })

  it('does not expose write action entries', async () => {
    const { wrapper } = await mountView()
    const text = wrapper.text()
    expect(text).not.toContain('生成邀约')
    expect(text).not.toContain('记发送')
    expect(text).not.toContain('出题')
    expect(text).not.toContain('开始面试')
    expect(text).not.toContain('发送 Offer')
    expect(text).not.toContain('自动决策')
    expect(text).not.toContain('Dify')
    expect(text).not.toContain('SMTP')
  })

  it('list filter includes pending_offer label', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="pipeline-status-filter"]').exists()).toBe(true))
    expect(wrapper.text()).toContain('录用建议待后续')
  })
})
