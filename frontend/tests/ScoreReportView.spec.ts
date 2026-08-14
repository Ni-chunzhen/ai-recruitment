import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as aiTasksApi from '../src/api/aiTasks'
import * as resumesApi from '../src/api/resumes'
import type { AITask } from '../src/api/aiTasks'
import type { ApplicationOut, ScoreReport } from '../src/api/resumes'
import ScoreReportView from '../src/views/ScoreReportView.vue'

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

const { ElMessage, ElMessageBox } = await import('element-plus')

const APP_ID = '11111111-1111-1111-1111-111111111111'

const reasonCodeItems = [
  {
    code: 'must_have_mismatch',
    label: '必备条件不符合',
    allowed_decisions: ['reject', 'talent_pool'],
    requires_description: false,
  },
  {
    code: 'other',
    label: '其他',
    allowed_decisions: ['reject', 'talent_pool'],
    requires_description: true,
  },
]

function makeTask(overrides: Partial<AITask> = {}): AITask {
  return {
    id: 'task-1',
    task_type: 'RESUME_SCORE',
    status: 'succeeded',
    business_type: 'application',
    business_id: APP_ID,
    version_id: null,
    created_by: null,
    error_code: null,
    error_message: null,
    error_category: null,
    attempt_count: 1,
    retry_cycle_no: 1,
    cycle_attempt_count: 1,
    started_at: '2026-08-14T01:00:00Z',
    finished_at: '2026-08-14T01:01:00Z',
    created_at: '2026-08-14T01:00:00Z',
    updated_at: '2026-08-14T01:01:00Z',
    attempts: [],
    ...overrides,
  }
}

function makeReport(overrides: Partial<ScoreReport> = {}): ScoreReport {
  return {
    application_id: APP_ID,
    result_id: 'result-1',
    version_label: 'M1',
    candidate_id: 'cand-1',
    candidate_name: '张三',
    job_id: 'job-1',
    job_name: '前端工程师',
    job_version_id: 'jv-1',
    job_version_label: 'V1.0',
    resume_version_id: 'rv-1',
    resume_version_label: 'R1',
    total_score: 87.5,
    calculated_total_score: 87.5,
    model_total_score: 90,
    score_difference: -2.5,
    recommendation: '建议进入面试',
    score_band: 'A',
    summary: '整体匹配良好',
    information_insufficient: false,
    dimensions: [
      {
        name: '专业能力',
        description: 'd',
        weight: 50,
        score: 80,
        weighted_score: 40,
        evidence: '项目经历',
        gap: '',
        risk: '',
      },
    ],
    risks: ['学历信息待核实'],
    must_have_check: ['本科已满足'],
    is_current: true,
    is_stale: false,
    created_at: '2026-08-14T01:01:00Z',
    ai_disclaimer: 'AI辅助建议，最终筛选结果由招聘人员确认。',
    lock_version: 1,
    ...overrides,
  }
}

function makeApplication(overrides: Partial<ApplicationOut> = {}): ApplicationOut {
  return {
    id: APP_ID,
    candidate_id: 'cand-1',
    candidate_name: '张三',
    job_id: 'job-1',
    job_name: '前端工程师',
    job_version_id: 'jv-1',
    job_version_label: 'V1.0',
    resume_version_id: 'rv-1',
    pipeline_status: 'pending_hr_screen',
    status: 'in_progress',
    lock_version: 1,
    created_at: '2026-08-14T00:00:00Z',
    updated_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    {
      path: '/applications/:applicationId/score-report',
      name: 'score-report',
      component: ScoreReportView,
    },
  ],
})

const globalStubs = {
  AdminLayout: { template: '<div class="layout"><slot /></div>' },
  'el-button': {
    props: ['disabled', 'loading', 'type', 'plain', 'link'],
    template:
      '<button :disabled="disabled || loading" :data-type="type"><slot /></button>',
  },
  'el-alert': {
    props: ['title', 'type'],
    template: '<div class="el-alert" :data-type="type">{{ title }}<slot /></div>',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select class="reason-select" :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<textarea class="reason-input" :value="modelValue" @input="$emit(\'update:modelValue\', ($event.target).value)"></textarea>',
  },
  'el-table': {
    props: ['data'],
    emits: ['row-click'],
    template:
      '<div class="el-table"><div v-for="row in data" :key="row.result_id" class="history-row" @click="$emit(\'row-click\', row)">{{ row.version_label }} {{ row.calculated_total_score }}</div></div>',
  },
  'el-table-column': { template: '<span />' },
  'el-empty': { props: ['description'], template: '<div class="el-empty">{{ description }}<slot /></div>' },
  'el-progress': { template: '<div class="el-progress" />' },
}

async function mountView(): Promise<VueWrapper> {
  await router.push(`/applications/${APP_ID}/score-report`)
  await router.isReady()
  const wrapper = mount(ScoreReportView, {
    global: {
      plugins: [router, createPinia()],
      stubs: globalStubs,
    },
  })
  await nextTick()
  await nextTick()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((btn) => btn.text().includes(text))
}

describe('ScoreReportView', () => {
  beforeEach(async () => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    vi.mocked(ElMessageBox.confirm).mockResolvedValue(true)

    vi.spyOn(resumesApi, 'getScoreReport').mockResolvedValue(makeReport())
    vi.spyOn(resumesApi, 'getScoreHistory').mockResolvedValue({ items: [] })
    vi.spyOn(resumesApi, 'getScoreHistoryItem').mockResolvedValue(makeReport())
    vi.spyOn(resumesApi, 'createResumeScoreTask').mockResolvedValue({
      task_id: 'task-1',
      application_id: APP_ID,
      status: 'pending',
    })
    vi.spyOn(resumesApi, 'createScreeningDecision').mockResolvedValue({
      id: 'sd-1',
      application_id: APP_ID,
      decision: 'enter_interview',
      from_pipeline_status: 'pending_hr_screen',
      to_pipeline_status: 'interviewing',
      lock_version: 2,
      created_at: '2026-08-14T02:00:00Z',
    })
    vi.spyOn(resumesApi, 'pollAITaskUntilDone').mockResolvedValue(makeTask())
    vi.spyOn(resumesApi, 'listScreeningReasonCodes').mockResolvedValue({
      items: reasonCodeItems,
    })
    vi.spyOn(resumesApi, 'getApplication').mockResolvedValue(makeApplication())
    vi.spyOn(aiTasksApi, 'listAITasks').mockResolvedValue({ items: [], total: 0 })
    vi.spyOn(aiTasksApi, 'retryAITask').mockResolvedValue(makeTask({ status: 'pending' }))
    vi.spyOn(aiTasksApi, 'cancelAITask').mockResolvedValue(makeTask({ status: 'cancelled' }))
  })

  it('disables the score button while the task is pending', async () => {
    vi.mocked(resumesApi.getScoreReport).mockRejectedValue(new Error('not found'))
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'pending' })],
      total: 1,
    })
    vi.mocked(resumesApi.pollAITaskUntilDone).mockReturnValue(new Promise(() => {}))

    const wrapper = await mountView()
    await vi.waitFor(() => {
      const btn = buttonByText(wrapper, '发起评分')
      expect(btn).toBeTruthy()
      expect(btn!.attributes('disabled')).toBeDefined()
    })
  })

  it('keeps polling while the task is running', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'running' })],
      total: 1,
    })
    vi.mocked(resumesApi.pollAITaskUntilDone).mockReturnValue(new Promise(() => {}))

    await mountView()
    await vi.waitFor(() => {
      expect(resumesApi.pollAITaskUntilDone).toHaveBeenCalledWith(
        'task-1',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
  })

  it('shows calculated_total_score after succeeded', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'succeeded' })],
      total: 1,
    })
    vi.mocked(resumesApi.getScoreReport).mockResolvedValue(
      makeReport({ calculated_total_score: 87.5, total_score: 87.5, model_total_score: 99 }),
    )

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.find('.score .value').text()).toBe('87.5')
    })
  })

  it('shows same-task retry for failed', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'failed', error_message: 'provider timeout' })],
      total: 1,
    })

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('人工重试')
    })
    await buttonByText(wrapper, '人工重试')!.trigger('click')
    expect(aiTasksApi.retryAITask).toHaveBeenCalledWith('task-1')
  })

  it('shows same-task retry for output_invalid', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'output_invalid' })],
      total: 1,
    })

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('人工重试')
      expect(wrapper.text()).toContain('AI结果格式异常')
    })
    await buttonByText(wrapper, '人工重试')!.trigger('click')
    expect(aiTasksApi.retryAITask).toHaveBeenCalledWith('task-1')
  })

  it('shows cancelled-only hint and hides retry', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [makeTask({ status: 'cancelled' })],
      total: 1,
    })

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('评分任务已取消，可重新发起评分。')
    })
    expect(wrapper.text()).not.toContain('人工重试')
  })

  it('loads history list', async () => {
    vi.mocked(resumesApi.getScoreHistory).mockResolvedValue({
      items: [
        makeReport({ result_id: 'result-1', version_label: 'M1', is_current: true }),
        makeReport({ result_id: 'result-2', version_label: 'M2', is_current: false }),
      ],
    })

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('M1')
      expect(wrapper.text()).toContain('M2')
    })
  })

  it('loads a history item when a history row is clicked', async () => {
    const historyItem = makeReport({
      result_id: 'result-2',
      version_label: 'M2',
      is_current: false,
      calculated_total_score: 70,
    })
    vi.mocked(resumesApi.getScoreHistory).mockResolvedValue({
      items: [makeReport(), historyItem],
    })
    vi.mocked(resumesApi.getScoreHistoryItem).mockResolvedValue(historyItem)

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.findAll('.history-row').length).toBeGreaterThan(0)
    })
    await wrapper.findAll('.history-row')[1].trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.getScoreHistoryItem).toHaveBeenCalledWith(APP_ID, 'result-2')
    })
  })

  it('returns to the current report from history mode', async () => {
    const current = makeReport({ result_id: 'result-1', version_label: 'M2', is_current: true })
    const historic = makeReport({
      result_id: 'result-0',
      version_label: 'M1',
      is_current: false,
    })
    vi.mocked(resumesApi.getScoreHistory).mockResolvedValue({
      items: [current, historic],
    })
    vi.mocked(resumesApi.getScoreHistoryItem).mockResolvedValue(historic)

    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.findAll('.history-row').length).toBe(2))
    await wrapper.findAll('.history-row')[1].trigger('click')
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('返回当前报告')
    })
    vi.mocked(resumesApi.getScoreReport).mockClear()
    await buttonByText(wrapper, '返回当前报告')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.getScoreReport).toHaveBeenCalledWith(APP_ID)
    })
  })

  it('shows stale tag and yellow warning when is_stale', async () => {
    vi.mocked(resumesApi.getScoreReport).mockResolvedValue(
      makeReport({ is_stale: true, is_current: true }),
    )
    vi.mocked(resumesApi.getScoreHistory).mockResolvedValue({
      items: [makeReport({ is_stale: true, is_current: true })],
    })

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('过期')
      expect(wrapper.find('.el-alert[data-type="warning"]').exists()).toBe(true)
    })
  })

  it('renders top-level risks', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('学历信息待核实')
    })
  })

  it('does not submit reject without reason_code', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('淘汰'))
    await buttonByText(wrapper, '淘汰')!.trigger('click')
    expect(resumesApi.createScreeningDecision).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })

  it('does not submit talent_pool without reason_code', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('人才库'))
    await buttonByText(wrapper, '人才库')!.trigger('click')
    expect(resumesApi.createScreeningDecision).not.toHaveBeenCalled()
  })

  it('does not submit OTHER without description', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.find('.reason-select').exists()).toBe(true))
    await wrapper.find('.reason-select').setValue('other')
    await buttonByText(wrapper, '淘汰')!.trigger('click')
    expect(resumesApi.createScreeningDecision).not.toHaveBeenCalled()
  })

  it('allows enter_interview and hold without a reason', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('进入面试'))
    await buttonByText(wrapper, '进入面试')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.createScreeningDecision).toHaveBeenCalledWith(
        APP_ID,
        expect.objectContaining({ decision: 'enter_interview' }),
      )
    })
    vi.mocked(resumesApi.createScreeningDecision).mockClear()
    vi.mocked(resumesApi.getApplication).mockResolvedValue(
      makeApplication({ pipeline_status: 'pending_hr_screen', lock_version: 2 }),
    )
    await vi.waitFor(() => {
      expect(buttonByText(wrapper, '待定')?.attributes('disabled')).toBeUndefined()
    })
    await buttonByText(wrapper, '待定')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.createScreeningDecision).toHaveBeenCalledWith(
        APP_ID,
        expect.objectContaining({ decision: 'hold' }),
      )
    })
  })

  it('blocks reject and talent_pool when reason-code API fails', async () => {
    vi.mocked(resumesApi.listScreeningReasonCodes).mockRejectedValue(new Error('network'))

    const wrapper = await mountView()
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('原因选项加载失败')
    })
    await buttonByText(wrapper, '淘汰')!.trigger('click')
    await buttonByText(wrapper, '人才库')!.trigger('click')
    expect(resumesApi.createScreeningDecision).not.toHaveBeenCalled()
    await buttonByText(wrapper, '进入面试')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.createScreeningDecision).toHaveBeenCalledWith(
        APP_ID,
        expect.objectContaining({ decision: 'enter_interview' }),
      )
    })
  })

  it('sends idempotency_key with screening requests', async () => {
    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('进入面试'))
    await buttonByText(wrapper, '进入面试')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.createScreeningDecision).toHaveBeenCalledWith(
        APP_ID,
        expect.objectContaining({
          decision: 'enter_interview',
          idempotency_key: expect.any(String),
        }),
      )
    })
    const payload = vi.mocked(resumesApi.createScreeningDecision).mock.calls[0][1]
    expect(payload.idempotency_key).toBeTruthy()
    expect(payload).not.toHaveProperty('ai_result_id')
  })

  it('refreshes application status after a successful screening', async () => {
    vi.mocked(resumesApi.getApplication)
      .mockResolvedValueOnce(makeApplication())
      .mockResolvedValueOnce(
        makeApplication({ pipeline_status: 'interviewing', lock_version: 2 }),
      )

    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('待HR筛选'))
    await buttonByText(wrapper, '进入面试')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.getApplication).toHaveBeenCalledTimes(2)
      expect(wrapper.text()).toContain('面试中')
    })
  })

  it('shows a refresh prompt on HTTP 409 and does not overwrite server state', async () => {
    const conflict = Object.assign(new Error('conflict'), {
      response: { status: 409, data: { detail: 'application was updated' } },
    })
    vi.mocked(resumesApi.createScreeningDecision).mockRejectedValue(conflict)

    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('待HR筛选'))
    await buttonByText(wrapper, '进入面试')!.trigger('click')
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('候选人状态已被其他人员更新，请刷新后重试')
    })
    expect(wrapper.text()).toContain('待HR筛选')
    expect(wrapper.text()).not.toContain('面试中')
    vi.mocked(resumesApi.getApplication).mockClear()
    await buttonByText(wrapper, '刷新')!.trigger('click')
    await vi.waitFor(() => {
      expect(resumesApi.getApplication).toHaveBeenCalled()
    })
  })

  it('does not render technical audit fields on the ordinary page', async () => {
    vi.mocked(aiTasksApi.listAITasks).mockResolvedValue({
      items: [
        makeTask({
          status: 'failed',
          error_message: '评分失败',
          ...({
            input_snapshot: { resume_text: 'SECRET_RESUME_BODY' },
            result_payload: { raw: 'RAW_RESULT' },
            raw_response: { dump: 'RAW_RESPONSE_DUMP' },
            provider_run_id: 'prv-should-not-render',
            request_id: 'req-should-not-render',
          } as object),
        } as AITask),
      ],
      total: 1,
    })

    const wrapper = await mountView()
    await vi.waitFor(() => expect(wrapper.text()).toContain('评分失败'))
    const text = wrapper.text()
    expect(text).not.toContain('SECRET_RESUME_BODY')
    expect(text).not.toContain('RAW_RESPONSE_DUMP')
    expect(text).not.toContain('prv-should-not-render')
    expect(text).not.toContain('req-should-not-render')
    expect(text).not.toContain('input_snapshot')
    expect(text).not.toContain('provider_run_id')
  })
})
