import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import * as candidateCenterApi from '../src/api/candidateCenter'
import * as comprehensiveAnalysisApi from '../src/api/comprehensiveAnalysis'
import * as hiringDecisionsApi from '../src/api/hiringDecisions'
import * as interviewAiApi from '../src/api/interviewAi'
import * as offersApi from '../src/api/offers'
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
    props: ['type', 'plain', 'link', 'size', 'disabled'],
    emits: ['click'],
    template:
      '<button :data-test="$attrs[\'data-test\']" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-tag': { template: '<span><slot /></span>' },
  'el-table': {
    props: ['data'],
    emits: ['row-click'],
    template:
      '<div :data-test="$attrs[\'data-test\']"><div v-for="(row, idx) in data" :key="row.application_id || row.round_id || row.id || idx" class="table-row" @click="$emit(\'row-click\', row)">{{ row.error_message_safe || JSON.stringify(row) }}</div><slot /></div>',
  },
  'el-table-column': { template: '<span />' },
  'el-card': { template: '<div><slot /></div>' },
  'el-descriptions': { template: '<div><slot /></div>' },
  'el-descriptions-item': { template: '<div><slot /></div>' },
  'el-alert': { props: ['title'], template: '<div class="alert">{{ title }}</div>' },
  'el-empty': { props: ['description'], template: '<div class="empty">{{ description }}</div>' },
  'el-dialog': {
    props: ['modelValue', 'title'],
    template:
      '<div v-if="modelValue" class="dialog" :data-test="$attrs[\'data-test\']"><slot /><slot name="footer" /></div>',
  },
  'el-select': {
    props: ['modelValue', 'disabled', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<select :data-test="$attrs[\'data-test\']" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': { props: ['label'], template: '<div><label>{{ label }}</label><slot /></div>' },
  'el-input': {
    props: ['modelValue', 'type', 'rows'],
    emits: ['update:modelValue'],
    template:
      '<textarea v-if="type===\'textarea\'" :data-test="$attrs[\'data-test\']" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /><input v-else :data-test="$attrs[\'data-test\']" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
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

function makeDetail(overrides: Record<string, unknown> = {}) {
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
    lock_version: 3,
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
    ...overrides,
  }
}

async function mountView(permissions: string[] = ['recruitment.manage', 'profile.read']) {
  const { default: CandidateCenterDetailView } = await import(
    '../src/views/CandidateCenterDetailView.vue'
  )
  const pinia = createPinia()
  setActivePinia(pinia)
  const authStore = useAuthStore()
  authStore.initialized = true
  authStore.user = {
    ...makeUser(),
    permissions,
    roles: permissions.includes('recruitment.manage') ? ['recruiter_admin'] : ['interviewer'],
  }
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
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail() as never,
    )
    vi.spyOn(hiringDecisionsApi, 'listHiringDecisions').mockResolvedValue({ items: [] })
    vi.spyOn(offersApi, 'listOffers').mockResolvedValue({ items: [] })
    vi.spyOn(offersApi, 'getOffer').mockResolvedValue({
      id: 'offer-1',
      application_id: 'app-1',
      status: 'draft',
      hiring_decision_id: 'hd-1',
      recipient_email_masked: 'a***@example.com',
      recipient_name: '张三',
      lock_version: 1,
      version_id: 'ov-1',
      version_no: 1,
      content_hash: 'hash',
      frozen: false,
      subject: 'SECRET_SUBJECT',
      body_html: '<p>SECRET_BODY</p>',
      body_text: 'SECRET_BODY',
      template_code: 'offer_console_v1',
      template_version: '1',
      created_at: '2026-08-21T00:00:00Z',
      updated_at: '2026-08-21T00:00:00Z',
    })
    vi.spyOn(offersApi, 'listOfferAttempts').mockResolvedValue({ items: [] })
    vi.spyOn(hiringDecisionsApi, 'listHiringReasonCodes').mockResolvedValue({
      items: [
        {
          code: 'meets_role_bar',
          label: '达到岗位录用标准',
          allowed_decisions: ['recommend_hire'],
        },
        {
          code: 'skill_gap',
          label: '关键技能不足',
          allowed_decisions: ['reject'],
        },
        {
          code: 'need_another_round',
          label: '需要安排补面',
          allowed_decisions: ['hold'],
        },
      ],
    })
    vi.spyOn(interviewAiApi, 'getRoundAnalysis').mockResolvedValue({
      analysis_id: 'an-1',
      round_id: 'round-1',
      current_version_id: 'ver-a1',
      versions: [
        {
          analysis_id: 'an-1',
          version_id: 'ver-a1',
          version_no: 1,
          version_label: 'A1',
          transcript_version_id: 'tv-1',
          job_version_id: 'jv-1',
          ai_task_id: 'task-1',
          overall_score: 4.2,
          dimension_count: 2,
          evidence_count: 2,
          created_by: null,
          created_at: '2026-08-20T00:00:00Z',
          is_current: true,
          is_stale: false,
        },
      ],
    })
    vi.spyOn(comprehensiveAnalysisApi, 'getComprehensiveAnalysis').mockResolvedValue({
      analysis_id: 'ca-1',
      application_id: 'app-1',
      current_version_id: 'cv-1',
      versions: [
        {
          analysis_id: 'ca-1',
          version_id: 'cv-1',
          version_no: 1,
          version_label: 'C1',
          ai_task_id: 'ctask-1',
          overall_score: 4,
          coverage_report: {
            eligible_round_count: 1,
            total_round_count: 2,
            included_rounds: [
              {
                round_id: 'round-1',
                sequence_no: 1,
                analysis_version_id: 'ver-a1',
                overall_score: 4.2,
              },
            ],
            gaps: [
              {
                round_id: 'round-2',
                sequence_no: 2,
                reason_code: 'analysis_none',
                status: 'SCHEDULED',
              },
            ],
            coverage_insufficient: true,
            single_round_only: true,
            missing_round_count: 1,
          },
          created_by: 'u1',
          created_at: '2026-08-21T00:00:00Z',
          is_current: true,
          is_stale: false,
        },
      ],
    })
    vi.spyOn(comprehensiveAnalysisApi, 'getComprehensiveAnalysisVersion').mockResolvedValue({
      analysis_id: 'ca-1',
      version_id: 'cv-1',
      version_no: 1,
      version_label: 'C1',
      ai_task_id: 'ctask-1',
      overall_score: 4,
      overall_summary: '辅助综合短评',
      round_refs: [
        {
          round_id: 'round-1',
          sequence_no: 1,
          analysis_version_id: 'ver-a1',
          analysis_version_no: 1,
          overall_score: 4.2,
          dimensions: [
            {
              dimension_key: 'collab',
              dimension_name: '协作',
              weight: 100,
              score: 4,
              insufficient_information: false,
            },
          ],
          evidence_refs: [],
        },
      ],
      coverage_report: {
        eligible_round_count: 1,
        total_round_count: 2,
        included_rounds: [],
        gaps: [
          {
            round_id: 'round-2',
            sequence_no: 2,
            reason_code: 'analysis_none',
            status: 'SCHEDULED',
          },
        ],
        coverage_insufficient: true,
        single_round_only: true,
        missing_round_count: 1,
      },
      created_by: 'u1',
      created_at: '2026-08-21T00:00:00Z',
      is_current: true,
      is_stale: false,
    })
    vi.spyOn(comprehensiveAnalysisApi, 'generateComprehensiveAnalysis').mockResolvedValue({
      task_id: 'ctask-new',
      task_type: 'INTERVIEW_COMPREHENSIVE_ANALYZE',
      status: 'pending',
      application_id: 'app-1',
      dispatch_status: 'queued',
    })
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
    expect(text).not.toContain('筛选决策')
    expect(text).not.toContain('发送 Offer')
    expect(text).not.toContain('自动决策')
    expect(text).not.toContain('Dify')
    expect(text).not.toContain('SMTP')
  })

  it('shows hiring panel for interviewing manage user', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-decision-panel"]').exists()).toBe(true))
    expect(wrapper.text()).toContain('面后人工决策')
    expect(wrapper.find('[data-test="hiring-recommend-hire"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="hiring-reject"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="hiring-hold"]').exists()).toBe(true)
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.find('[data-test="hiring-decision-history"]').exists()).toBe(true)
  })

  it('hides write actions when pending_offer', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail({ pipeline_status: 'pending_offer' }) as never,
    )
    vi.spyOn(offersApi, 'listOffers').mockResolvedValue({
      items: [
        {
          id: 'offer-1',
          application_id: 'app-1',
          status: 'draft',
          hiring_decision_id: 'hd-1',
          recipient_email_masked: 'a***@example.com',
          recipient_name: '张三',
          lock_version: 1,
          version_no: 1,
          content_hash: 'hash',
          frozen: false,
          created_at: '2026-08-21T00:00:00Z',
          updated_at: '2026-08-21T00:00:00Z',
        },
      ],
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('录用建议待后续'))
    expect(wrapper.find('[data-test="hiring-recommend-hire"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="hiring-reject"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="hiring-hold"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="hiring-decision-history"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('发送 Offer')
  })

  it('hides hiring panel for execute-only user', async () => {
    const { wrapper } = await mountView(['interview.execute', 'profile.read'])
    await vi.waitFor(() => expect(wrapper.text()).toContain('张三'))
    expect(wrapper.find('[data-test="hiring-decision-panel"]').exists()).toBe(false)
  })

  it('shows comprehensive panel for manage interviewing', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-analysis-panel"]').exists()).toBe(true),
    )
    expect(wrapper.text()).toContain('综合面试分析（辅助）')
    expect(wrapper.find('[data-test="comprehensive-generate"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="comprehensive-coverage"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="comprehensive-gaps"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('单轮覆盖不足提示')
    expect(wrapper.find('[data-test="comprehensive-dimension-summary"]').exists()).toBe(true)
  })

  it('hides generate on pending_offer but allows read', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail({ pipeline_status: 'pending_offer' }) as never,
    )
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-analysis-panel"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="comprehensive-generate"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="comprehensive-refresh"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="comprehensive-version-history"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="comprehensive-write-hint"]').exists()).toBe(true)
  })

  it('hides comprehensive panel for execute', async () => {
    const { wrapper } = await mountView(['interview.execute', 'profile.read'])
    await vi.waitFor(() => expect(wrapper.text()).toContain('张三'))
    expect(wrapper.find('[data-test="comprehensive-analysis-panel"]').exists()).toBe(false)
    expect(comprehensiveAnalysisApi.getComprehensiveAnalysis).not.toHaveBeenCalled()
  })

  it('forbids offer automation copy in comprehensive panel', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-analysis-panel"]').exists()).toBe(true),
    )
    const panel = wrapper.find('[data-test="comprehensive-analysis-panel"]').text()
    expect(panel).not.toContain('发送 Offer')
    expect(panel).not.toContain('Offer')
    expect(panel).not.toContain('自动决策')
    expect(panel).not.toContain('Dify')
    expect(panel).not.toContain('通知候选人')
    expect(panel).not.toContain('淘汰')
  })

  it('shows fresh badge only when comprehensiveDetail.is_stale === false', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-freshness"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="comprehensive-freshness"]').text()).toContain('有效')
    expect(wrapper.find('[data-test="comprehensive-fresh-badge"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="comprehensive-stale-badge"]').exists()).toBe(false)
  })

  it('shows stale badge when comprehensiveDetail.is_stale is true', async () => {
    vi.spyOn(comprehensiveAnalysisApi, 'getComprehensiveAnalysisVersion').mockResolvedValue({
      analysis_id: 'ca-1',
      version_id: 'cv-1',
      version_no: 1,
      version_label: 'C1',
      ai_task_id: 'ctask-1',
      overall_score: 4,
      overall_summary: '辅助综合短评',
      round_refs: [],
      coverage_report: {
        eligible_round_count: 1,
        total_round_count: 2,
        included_rounds: [],
        gaps: [],
        coverage_insufficient: true,
        single_round_only: true,
        missing_round_count: 1,
      },
      created_by: 'u1',
      created_at: '2026-08-21T00:00:00Z',
      is_current: true,
      is_stale: true,
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-stale-badge"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="comprehensive-freshness"]').text()).toContain('已过期')
    expect(wrapper.find('[data-test="comprehensive-fresh-badge"]').exists()).toBe(false)
  })

  it('does not show 有效 when comprehensiveDetail is missing', async () => {
    vi.spyOn(comprehensiveAnalysisApi, 'getComprehensiveAnalysis').mockResolvedValue({
      analysis_id: 'ca-1',
      application_id: 'app-1',
      current_version_id: null,
      versions: [],
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="comprehensive-analysis-panel"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="comprehensive-fresh-badge"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="comprehensive-freshness"]').text()).toMatch(
      /暂无可验证状态|加载中/,
    )
    expect(wrapper.find('[data-test="comprehensive-freshness"]').text()).not.toContain('有效')
  })

  it('submits recommend_hire with reason_code and analysis_version_id', async () => {
    const createSpy = vi.spyOn(hiringDecisionsApi, 'createHiringDecision').mockResolvedValue({
      id: 'hd-1',
      application_id: 'app-1',
      decision: 'recommend_hire',
      reason_code: 'meets_role_bar',
      round_id: 'round-1',
      analysis_version_id: 'ver-a1',
      overall_score: 4.2,
      analysis_version_no: 1,
      from_pipeline_status: 'interviewing',
      to_pipeline_status: 'pending_offer',
      lock_version: 4,
      created_at: '2026-08-20T00:00:00Z',
      decided_by: 'u1',
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-recommend-hire"]').exists()).toBe(true))
    await wrapper.find('[data-test="hiring-recommend-hire"]').trigger('click')
    await nextTick()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-decision-dialog"]').exists()).toBe(true))
    const reasonSelect = wrapper.find('[data-test="hiring-reason-select"]')
    await reasonSelect.setValue('meets_role_bar')
    await wrapper.find('[data-test="hiring-submit"]').trigger('click')
    await vi.waitFor(() => expect(createSpy).toHaveBeenCalled())
    const [, body] = createSpy.mock.calls[0]
    expect(body.decision).toBe('recommend_hire')
    expect(body.reason_code).toBe('meets_role_bar')
    expect(body.analysis_version_id).toBe('ver-a1')
    expect(body.lock_version).toBe(3)
    expect(body.idempotency_key).toBeTruthy()
    expect(body).not.toHaveProperty('reason')
    expect(candidateCenterApi.getCandidateCenterApplicationDetail).toHaveBeenCalled()
  })

  it('forbids offer-send and automation copy', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-decision-panel"]').exists()).toBe(true))
    const text = wrapper.text()
    expect(text).toContain('面后人工决策')
    expect(text).toContain('录用建议')
    expect(text).not.toContain('发送 Offer')
    expect(text).not.toContain('自动决策')
    expect(text).not.toContain('Dify')
    expect(text).not.toContain('SMTP')
    expect(text).not.toContain('hired')
    expect(text).not.toContain('通知')
  })

  it('disables write when no valid analysis and shows reason', async () => {
    vi.spyOn(interviewAiApi, 'getRoundAnalysis').mockResolvedValue({
      analysis_id: 'an-1',
      round_id: 'round-1',
      current_version_id: 'ver-old',
      versions: [
        {
          analysis_id: 'an-1',
          version_id: 'ver-old',
          version_no: 1,
          version_label: 'A1',
          transcript_version_id: 'tv-1',
          job_version_id: 'jv-1',
          ai_task_id: 'task-1',
          overall_score: 3,
          dimension_count: 1,
          evidence_count: 0,
          created_by: null,
          created_at: '2026-08-20T00:00:00Z',
          is_current: true,
          is_stale: true,
        },
      ],
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-disabled-reason"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="hiring-recommend-hire"]').attributes('disabled')).toBeDefined()
  })

  it('shows no-score state when score_summary is null', async () => {
    const noScore = makeDetail({ score_summary: null })
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(noScore as never)
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('无评分'))
    expect(wrapper.text()).not.toContain('已完成')
  })

  it('shows clear feedback on request failure and empty rounds', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('加载详情失败'))
  })

  function mockPendingOfferReady() {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail({ pipeline_status: 'pending_offer' }) as never,
    )
    vi.spyOn(offersApi, 'listOffers').mockResolvedValue({
      items: [
        {
          id: 'offer-1',
          application_id: 'app-1',
          status: 'ready',
          hiring_decision_id: 'hd-1',
          recipient_email_masked: 'a***@example.com',
          recipient_name: '张三',
          lock_version: 2,
          version_no: 2,
          content_hash: 'hash2',
          frozen: true,
          created_at: '2026-08-21T00:00:00Z',
          updated_at: '2026-08-21T01:00:00Z',
        },
      ],
    })
    vi.spyOn(offersApi, 'getOffer').mockResolvedValue({
      id: 'offer-1',
      application_id: 'app-1',
      status: 'ready',
      hiring_decision_id: 'hd-1',
      recipient_email_masked: 'a***@example.com',
      recipient_name: '张三',
      lock_version: 2,
      version_id: 'ov-2',
      version_no: 2,
      content_hash: 'hash2',
      frozen: true,
      subject: 'SECRET_SUBJECT',
      body_html: '<p>SECRET_BODY</p>',
      body_text: 'SECRET_BODY',
      template_code: 'offer_console_v1',
      template_version: '1',
      created_at: '2026-08-21T00:00:00Z',
      updated_at: '2026-08-21T01:00:00Z',
    })
    vi.spyOn(offersApi, 'listOfferAttempts').mockResolvedValue({
      items: [
        {
          id: 'att-1',
          offer_id: 'offer-1',
          offer_version_id: 'ov-2',
          provider: 'console',
          status: 'failed',
          attempt_no: 1,
          error_code: 'console_error',
          error_message_safe: 'console failed',
          started_at: '2026-08-21T01:00:00Z',
          finished_at: '2026-08-21T01:00:01Z',
          next_retry_at: null,
          created_at: '2026-08-21T01:00:00Z',
        },
      ],
    })
  }

  it('shows offer panel when manage and pending_offer', async () => {
    mockPendingOfferReady()
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="offer-recipient-masked"]').text()).toContain('a***@example.com')
    expect(wrapper.find('[data-test="offer-version-no"]').text()).toContain('2')
  })

  it('hides offer panel for execute-only', async () => {
    mockPendingOfferReady()
    const { wrapper } = await mountView(['interview.execute', 'profile.read'])
    await vi.waitFor(() => expect(wrapper.text()).toContain('张三'))
    expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(false)
    expect(offersApi.listOffers).not.toHaveBeenCalled()
  })

  it('hides offer panel outside pending_offer', async () => {
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="hiring-decision-panel"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(false)
  })

  it('send requires confirmation mentioning console', async () => {
    mockPendingOfferReady()
    const sendSpy = vi.spyOn(offersApi, 'confirmOfferSend').mockResolvedValue({
      offer_id: 'offer-1',
      attempt_id: 'att-2',
      status: 'sending',
      attempt_status: 'pending',
      attempt_no: 1,
      lock_version: 3,
      version_id: 'ov-2',
      provider: 'console',
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="offer-send"]').exists()).toBe(true))
    await wrapper.find('[data-test="offer-send"]').trigger('click')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="offer-send-confirm-dialog"]').exists()).toBe(true),
    )
    const copy = wrapper.find('[data-test="offer-send-confirm-copy"]').text()
    expect(copy).toContain('Console')
    expect(copy).toContain('不会发送真实邮件')
    await wrapper.find('[data-test="offer-send-confirm"]').trigger('click')
    await vi.waitFor(() => expect(sendSpy).toHaveBeenCalled())
    const [, body] = sendSpy.mock.calls[0]
    expect(body.offer_version_id).toBe('ov-2')
    expect(body.idempotency_key).toBeTruthy()
  })

  it('displays masked recipient and safe error only', async () => {
    mockPendingOfferReady()
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(true),
    )
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="offer-attempt-history"]').text()).toContain('console failed'),
    )
    const panel = wrapper.find('[data-test="offer-console-panel"]')
    expect(panel.text()).toContain('a***@example.com')
    expect(panel.text()).not.toContain('SECRET_SUBJECT')
    expect(panel.text()).not.toContain('SECRET_BODY')
    expect(panel.text()).not.toContain('alice@')
  })

  it('forbids smtp dify autosend hired copy in offer panel', async () => {
    mockPendingOfferReady()
    const { wrapper } = await mountView()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="offer-console-panel"]').exists()).toBe(true),
    )
    const panel = wrapper.find('[data-test="offer-console-panel"]').text()
    expect(panel).not.toContain('SMTP')
    expect(panel).not.toContain('Dify')
    expect(panel).not.toContain('自动发送')
    expect(panel).not.toContain('hired')
    expect(panel).not.toContain('入职完成')
    expect(panel).not.toContain('电子签')
    expect(wrapper.find('[data-test="offer-advance-hired"]').exists()).toBe(false)
  })

  it('disables edit and send when sending', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail({ pipeline_status: 'pending_offer' }) as never,
    )
    vi.spyOn(offersApi, 'listOffers').mockResolvedValue({
      items: [
        {
          id: 'offer-1',
          application_id: 'app-1',
          status: 'sending',
          hiring_decision_id: 'hd-1',
          recipient_email_masked: 'a***@example.com',
          recipient_name: '张三',
          lock_version: 3,
          version_no: 2,
          content_hash: 'hash2',
          frozen: true,
          created_at: '2026-08-21T00:00:00Z',
          updated_at: '2026-08-21T01:00:00Z',
        },
      ],
    })
    vi.spyOn(offersApi, 'getOffer').mockResolvedValue({
      id: 'offer-1',
      application_id: 'app-1',
      status: 'sending',
      hiring_decision_id: 'hd-1',
      recipient_email_masked: 'a***@example.com',
      recipient_name: '张三',
      lock_version: 3,
      version_id: 'ov-2',
      version_no: 2,
      content_hash: 'hash2',
      frozen: true,
      subject: 's',
      body_html: 'h',
      body_text: 't',
      template_code: 'offer_console_v1',
      template_version: '1',
      created_at: '2026-08-21T00:00:00Z',
      updated_at: '2026-08-21T01:00:00Z',
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="offer-edit"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="offer-edit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="offer-send"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="offer-ready"]').attributes('disabled')).toBeDefined()
  })

  it('enables retry only when failed', async () => {
    vi.spyOn(candidateCenterApi, 'getCandidateCenterApplicationDetail').mockResolvedValue(
      makeDetail({ pipeline_status: 'pending_offer' }) as never,
    )
    vi.spyOn(offersApi, 'listOffers').mockResolvedValue({
      items: [
        {
          id: 'offer-1',
          application_id: 'app-1',
          status: 'failed',
          hiring_decision_id: 'hd-1',
          recipient_email_masked: 'a***@example.com',
          recipient_name: '张三',
          lock_version: 4,
          version_no: 2,
          content_hash: 'hash2',
          frozen: true,
          created_at: '2026-08-21T00:00:00Z',
          updated_at: '2026-08-21T02:00:00Z',
        },
      ],
    })
    vi.spyOn(offersApi, 'getOffer').mockResolvedValue({
      id: 'offer-1',
      application_id: 'app-1',
      status: 'failed',
      hiring_decision_id: 'hd-1',
      recipient_email_masked: 'a***@example.com',
      recipient_name: '张三',
      lock_version: 4,
      version_id: 'ov-2',
      version_no: 2,
      content_hash: 'hash2',
      frozen: true,
      subject: 's',
      body_html: 'h',
      body_text: 't',
      template_code: 'offer_console_v1',
      template_version: '1',
      created_at: '2026-08-21T00:00:00Z',
      updated_at: '2026-08-21T02:00:00Z',
    })
    const retrySpy = vi.spyOn(offersApi, 'retryOfferSend').mockResolvedValue({
      offer_id: 'offer-1',
      attempt_id: 'att-9',
      status: 'sending',
      attempt_status: 'pending',
      attempt_no: 1,
      lock_version: 5,
      version_id: 'ov-2',
      provider: 'console',
    })
    const { wrapper } = await mountView()
    await vi.waitFor(() => expect(wrapper.find('[data-test="offer-retry"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="offer-retry"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('[data-test="offer-retry"]').trigger('click')
    await vi.waitFor(() => expect(retrySpy).toHaveBeenCalled())
  })
})
