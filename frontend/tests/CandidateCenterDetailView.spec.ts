import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import * as candidateCenterApi from '../src/api/candidateCenter'
import * as resumesApi from '../src/api/resumes'
import { useAuthStore } from '../src/stores/auth'

vi.mock('element-plus', async () => {
  const actual = await vi.importActual<typeof import('element-plus')>('element-plus')
  return {
    ...actual,
    ElMessage: {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
  }
})

const globalStubs = {
  'el-button': {
    props: ['type', 'plain', 'link', 'size'],
    emits: ['click'],
    template:
      '<button :data-test="$attrs[\'data-test\']" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-tag': { template: '<span><slot /></span>' },
  'el-table': {
    props: ['data'],
    emits: ['row-click'],
    template:
      '<div><div v-for="row in data" :key="row.application_id || row.round_id" class="table-row" @click="$emit(\'row-click\', row)">{{ JSON.stringify(row) }}</div><slot /></div>',
  },
  'el-table-column': { template: '<span />' },
  'el-card': { template: '<div><slot /></div>' },
  'el-descriptions': { template: '<div><slot /></div>' },
  'el-descriptions-item': { template: '<div><slot /></div>' },
  'el-alert': { props: ['title'], template: '<div class="alert">{{ title }}</div>' },
  'el-empty': { props: ['description'], template: '<div class="empty">{{ description }}</div>' },
}

function makeUser() {
  return {
    id: 'u1',
    username: 'admin',
    display_name: 'Admin',
    is_active: true,
    must_change_password: false,
    roles: ['recruiter_admin'],
    permissions: ['recruitment.manage', 'profile.read'],
  }
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/forbidden', name: 'forbidden', component: { template: '<div>forbidden</div>' } },
      {
        path: '/candidate-center/candidates/:candidateId/applications/:applicationId',
        name: 'candidate-center-detail',
        component: { template: '<div />' },
      },
      {
        path: '/resumes/:versionId/review',
        name: 'resume-review',
        component: { template: '<div />' },
      },
      {
        path: '/applications/:applicationId/score-report',
        name: 'score-report',
        component: { template: '<div />' },
      },
      {
        path: '/applications/:applicationId/interviews',
        name: 'application-interviews',
        component: { template: '<div />' },
      },
      {
        path: '/interview-rounds/:roundId/transcript',
        name: 'interview-transcript',
        component: { template: '<div />' },
      },
    ],
  })
}

function makeDetail() {
  return {
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
    close_action: null,
    interview_started: true,
    resume_summary: {
      resume_id: 'resume-1',
      resume_version_id: 'resume-ver-1',
      version_label: 'R3',
      kind: 'upload',
      status: 'confirmed',
      original_filename: 'zhangsan.pdf',
      confirmed_at: '2026-08-18T00:00:00Z',
    },
    score_summary: {
      result_id: 'score-1',
      version_label: 'S2',
      total_score: 80,
      calculated_total_score: 81.5,
      score_band: 'A',
      recommendation: 'enter_interview',
      summary: 'summary',
      information_insufficient: false,
      is_stale: false,
      is_current: true,
      dimensions: [],
    },
    rounds: [
      {
        application_id: 'app-1',
        round_id: 'round-1',
        name: '一面',
        sequence_no: 1,
        status: 'COMPLETED',
        schedule_status: 'scheduled',
        has_meeting_password: false,
        invitation_status: 'sent',
        transcript_status: 'ready',
        question_status: 'ready',
        analysis_status: 'ready',
        analysis_overall_score: 83.5,
      },
    ],
    other_applications: [
      {
        application_id: 'app-2',
        job_id: 'job-2',
        job_name: '前端工程师',
        job_code: 'JOB-2',
        status: 'in_progress',
        pipeline_status: 'pending_hr_screen',
        created_at: '2026-08-17T00:00:00Z',
      },
    ],
  }
}

async function mountView() {
  const { default: CandidateCenterDetailView } = await import(
    '../src/views/CandidateCenterDetailView.vue'
  )
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.initialized = true
  authStore.user = makeUser()
  const router = makeRouter()
  await router.push('/candidate-center/candidates/cand-1/applications/app-1')
  await router.isReady()
  const wrapper = mount(CandidateCenterDetailView, {
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

describe('CandidateCenterDetailView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(makeDetail())
  })

  it('calls detail client with candidateId and applicationId from route', async () => {
    await mountView()
    await vi.waitFor(() => {
      expect(candidateCenterApi.getCandidateCenterApplicationDetail).toHaveBeenCalledWith('cand-1', 'app-1')
    })
  })

  it('renders resume metadata and links to resume-review without rendering body samples', async () => {
    const { wrapper, router } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('R3'))
    expect(wrapper.text()).toContain('zhangsan.pdf')
    expect(wrapper.text()).not.toContain('standardized_text')
    expect(wrapper.text()).not.toContain('confirmed_content')
    await wrapper.find('[data-test="go-resume-review"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('resume-review')
      expect(router.currentRoute.value.params.versionId).toBe('resume-ver-1')
    })
  })

  it('navigates to score-report and does not call resume score APIs directly', async () => {
    const scoreReportSpy = vi.spyOn(resumesApi, 'getScoreReport')
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-test="go-score-report"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('score-report')
      expect(router.currentRoute.value.params.applicationId).toBe('app-1')
    })
    expect(scoreReportSpy).not.toHaveBeenCalled()
  })

  it('navigates to application-interviews', async () => {
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-test="go-interviews"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('application-interviews')
      expect(router.currentRoute.value.params.applicationId).toBe('app-1')
    })
  })

  it('navigates to interview-transcript with accurate roundId', async () => {
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-test="go-transcript-round-1"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('interview-transcript')
      expect(router.currentRoute.value.params.roundId).toBe('round-1')
    })
  })

  it('renders all rounds statuses and other applications summary only', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('一面'))
    expect(wrapper.text()).toContain('scheduled')
    expect(wrapper.text()).toContain('sent')
    expect(wrapper.text()).toContain('ready')
    expect(wrapper.text()).toContain('前端工程师')
  })

  it('navigates to other application detail and keeps current detail isolated', async () => {
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-test="other-application-app-2"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('candidate-center-detail')
      expect(router.currentRoute.value.params.candidateId).toBe('cand-1')
      expect(router.currentRoute.value.params.applicationId).toBe('app-2')
    })
    await vi.waitFor(() => expect(wrapper.text()).toContain('暂无详情数据'))
    expect(wrapper.text()).not.toContain('一面')
    expect(wrapper.text()).not.toContain('R3')
    expect(wrapper.text()).not.toContain('S2')
  })

  it('does not mount invitation/question/analysis drawers or write-action buttons', async () => {
    const { wrapper } = await mountView()
    const text = wrapper.text()
    expect(text).not.toContain('InterviewInvitationDrawer')
    expect(text).not.toContain('InterviewQuestionSetDrawer')
    expect(text).not.toContain('InterviewAnalysisDrawer')
    expect(text).not.toContain('生成邀约')
    expect(text).not.toContain('记发送')
    expect(text).not.toContain('出题')
    expect(text).not.toContain('开始面试')
    expect(text).not.toContain('结束面试')
    expect(text).not.toContain('淘汰')
    expect(text).not.toContain('录用')
    expect(text).not.toContain('Offer')
    expect(text).not.toContain('筛选决策')
  })

  it('shows no-score state when score_summary is null', async () => {
    const noScore = makeDetail()
    noScore.score_summary = null
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(noScore)
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('无评分'))
    expect(wrapper.text()).not.toContain('已完成')
  })

  it('shows clear feedback on request failure and empty rounds', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('加载详情失败'))
  })
})
